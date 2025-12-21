"""
Discord bot for Clara - Multi-user AI assistant with memory.

Inspired by llmcord's clean design, but integrates directly with Clara's
MemoryManager for full mem0 memory support.

Usage:
    poetry run python discord_bot.py

Environment variables:
    DISCORD_BOT_TOKEN - Discord bot token (required)
    DISCORD_CLIENT_ID - Discord client ID (for invite link)
    DISCORD_MAX_MESSAGES - Max messages in conversation chain (default: 25)
    DISCORD_MAX_CHARS - Max chars per message content (default: 100000)
    DISCORD_ALLOWED_CHANNELS - Comma-separated channel IDs (optional, empty = all)
    DISCORD_ALLOWED_ROLES - Comma-separated role IDs (optional, empty = all)
"""

from __future__ import annotations

import os

# Load .env BEFORE other imports that read env vars at module level
from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import discord
import uvicorn
from discord import Message as DiscordMessage
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from db import SessionLocal, init_db
from e2b_tools import E2B_TOOLS, get_sandbox_manager
from llm_backends import make_llm, make_llm_with_tools
from local_files import LOCAL_FILE_TOOLS, get_file_manager
from memory_manager import MemoryManager
from models import ChannelSummary, Project, Session


# ============== Console Colors ==============
class C:
    """ANSI color codes for console output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Colors
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"

    # Shortcuts for common patterns
    OK = GREEN + "✓" + RESET
    FAIL = RED + "✗" + RESET
    WARN = YELLOW + "⚠" + RESET
    INFO = CYAN + "ℹ" + RESET


def log(tag: str, msg: str, color: str = C.CYAN) -> None:
    """Print a colored log message with tag."""
    print(f"{color}[{tag}]{C.RESET} {msg}")

# Configuration
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
MAX_MESSAGES = int(os.getenv("DISCORD_MAX_MESSAGES", "25"))
MAX_CHARS = int(os.getenv("DISCORD_MAX_CHARS", "100000"))
MAX_FILE_SIZE = int(os.getenv("DISCORD_MAX_FILE_SIZE", "100000"))  # 100KB default
SUMMARY_AGE_MINUTES = int(os.getenv("DISCORD_SUMMARY_AGE_MINUTES", "30"))
CHANNEL_HISTORY_LIMIT = int(os.getenv("DISCORD_CHANNEL_HISTORY_LIMIT", "50"))

# Supported text file extensions
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".scss",
    ".xml",
    ".csv",
    ".log",
    ".sh",
    ".bash",
    ".zsh",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".sql",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".gitignore",
    ".dockerfile",
}
ALLOWED_CHANNELS = [
    ch.strip()
    for ch in os.getenv("DISCORD_ALLOWED_CHANNELS", "").split(",")
    if ch.strip()
]
ALLOWED_ROLES = [
    r.strip() for r in os.getenv("DISCORD_ALLOWED_ROLES", "").split(",") if r.strip()
]
DEFAULT_PROJECT = os.getenv("DEFAULT_PROJECT", "Default Project")

# E2B configuration
E2B_ENABLED = bool(os.getenv("E2B_API_KEY"))
MAX_TOOL_ITERATIONS = 10  # Max tool call rounds per response

# Combined tools: E2B sandbox + local file management
# Local file tools are always available, E2B tools only when enabled
ALL_TOOLS = LOCAL_FILE_TOOLS + (E2B_TOOLS if E2B_ENABLED else [])

# Discord message limit
DISCORD_MSG_LIMIT = 2000

# Monitor configuration
MONITOR_PORT = int(os.getenv("DISCORD_MONITOR_PORT", "8001"))
MONITOR_ENABLED = os.getenv("DISCORD_MONITOR_ENABLED", "true").lower() == "true"
MAX_LOG_ENTRIES = 100


@dataclass
class CachedMessage:
    """Cached Discord message with content and metadata."""

    content: str
    images: list[str] = field(default_factory=list)
    user_id: str = ""
    username: str = ""
    is_bot: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class LogEntry:
    """A log entry for the monitor."""

    timestamp: datetime
    event_type: str  # "message", "dm", "response", "error", "system"
    guild: str | None
    channel: str | None
    user: str
    content: str

    def to_dict(self):
        content = self.content
        if len(content) > 500:
            content = content[:500] + "..."
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "guild": self.guild,
            "channel": self.channel,
            "user": self.user,
            "content": content,
        }


class BotMonitor:
    """Shared state for monitoring the bot."""

    def __init__(self):
        self.logs: deque[LogEntry] = deque(maxlen=MAX_LOG_ENTRIES)
        self.guilds: dict[int, dict] = {}
        self.start_time: datetime | None = None
        self.message_count = 0
        self.dm_count = 0
        self.response_count = 0
        self.error_count = 0
        self.bot_user: str | None = None

    def log(
        self,
        event_type: str,
        user: str,
        content: str,
        guild: str | None = None,
        channel: str | None = None,
    ):
        """Add a log entry."""
        entry = LogEntry(
            timestamp=datetime.now(UTC),
            event_type=event_type,
            guild=guild,
            channel=channel,
            user=user,
            content=content,
        )
        self.logs.appendleft(entry)

        if event_type == "message":
            self.message_count += 1
        elif event_type == "dm":
            self.dm_count += 1
        elif event_type == "response":
            self.response_count += 1
        elif event_type == "error":
            self.error_count += 1

    def update_guilds(self, guilds):
        """Update guild information."""
        self.guilds = {
            g.id: {
                "id": g.id,
                "name": g.name,
                "member_count": g.member_count,
                "icon": str(g.icon.url) if g.icon else None,
            }
            for g in guilds
        }

    def get_stats(self):
        """Get current statistics."""
        uptime = None
        if self.start_time:
            uptime = (datetime.now(UTC) - self.start_time).total_seconds()

        return {
            "bot_user": self.bot_user,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "uptime_seconds": uptime,
            "guild_count": len(self.guilds),
            "message_count": self.message_count,
            "dm_count": self.dm_count,
            "response_count": self.response_count,
            "error_count": self.error_count,
        }


# Global monitor instance
monitor = BotMonitor()


class ClaraDiscordBot(discord.Client):
    """Discord bot that integrates Clara's memory-enhanced AI."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)

        # Message cache: discord_msg_id -> CachedMessage
        self.msg_cache: dict[int, CachedMessage] = {}
        self.cache_lock = asyncio.Lock()

        # Initialize Clara's backend
        init_db()
        self.mm = MemoryManager(llm_callable=self._sync_llm)

    def _sync_llm(self, messages: list[dict]) -> str:
        """Synchronous LLM call for MemoryManager."""
        llm = make_llm()
        return llm(messages)

    def _build_discord_context(
        self,
        message: DiscordMessage,
        user_mems: list[str],
        proj_mems: list[str],
        is_dm: bool = False,
    ) -> str:
        """Build Discord-specific system context."""
        # Get user info
        author = message.author
        display_name = author.display_name
        username = author.name
        user_id = author.id

        # Get channel/server info
        channel_name = getattr(message.channel, "name", "DM")
        guild_name = message.guild.name if message.guild else "Direct Message"

        # Build environment context based on DM vs channel
        if is_dm:
            env_context = f"""
## Current Environment
You are in a **private DM** with {display_name}. This is a one-on-one conversation.

## Current User
- Display name: {display_name}
- Username: {username}
- User ID: discord-{user_id}"""
        else:
            env_context = f"""
## Current Environment
You are in **{guild_name}** server, channel **#{channel_name}**.
This is a SHARED channel where multiple users can participate.

## Conversation Context
- Messages from different users are prefixed with their name: [Username]: message
- You can see and respond to multiple users in the same conversation
- The current message is from: **{display_name}** (@{username})
- Address users by name when responding to specific people
- You maintain separate memories for each user, even in shared channels

## Current Speaker
- Display name: {display_name}
- Username: {username}
- User ID: discord-{user_id}"""

        # Build context
        context = f"""{env_context}

## Your Memory System
You have persistent semantic memory powered by mem0. This means:
- You remember facts, preferences, and context about each user across conversations
- Each Discord user has their own isolated memory space (identified by their Discord ID)
- Memories are automatically extracted from conversations and stored
- When a user returns, you can recall what you've learned about them
- You currently have {len(user_mems)} relevant memories about {display_name}
- You have {len(proj_mems)} relevant project-specific memories

Use your memories naturally in conversation. Reference past discussions when relevant.
Don't announce that you're "checking memories" - just use the knowledge seamlessly.
If you remember something about the user, you can mention it naturally.

## Discord Formatting (Markdown)
Discord uses a flavor of Markdown. Use these to format your messages:

**Text Styling:**
- **Bold**: `**text**`
- *Italic*: `*text*` or `_text_`
- __Underline__: `__text__`
- ~~Strikethrough~~: `~~text~~`
- Combine: `***bold italic***`, `__**underline bold**__`

**Structure:**
- # Header 1, ## Header 2, ### Header 3 (need space after #)
- > Block quote (single line)
- >>> Block quote (multi-line, everything after)
- - or * for bullet lists (need space after)
- 1. 2. 3. for numbered lists

**Code:**
- `inline code` with single backticks
- ```language
  code block
  ``` with triple backticks (use python, js, json, etc. for syntax highlighting)

**Other:**
- ||spoiler text|| - hidden until clicked
- [link text](https://url) - clickable links
- -# subtext - smaller text

## File Attachments
You can send files to users! When you want to share code, long text, or formatted
content as a downloadable file, use this special syntax:

<<<file:filename.ext>>>
file content here
<<</file>>>

Examples:
- `<<<file:script.py>>>` for Python files
- `<<<file:notes.md>>>` for Markdown documents
- `<<<file:data.json>>>` for JSON data
- `<<<file:report.html>>>` for HTML pages
- `<<<file:output.txt>>>` for plain text

Use files when:
- Code is too long for a code block (>50 lines)
- User asks for a downloadable file
- Sharing structured data (JSON, CSV, etc.)
- Creating formatted documents (HTML, Markdown)

## Discord Etiquette
- Keep responses concise when possible (Discord is conversational)
- You can use emojis sparingly when they fit the tone
- Long responses will be split across multiple messages automatically
- Users interact by @mentioning you or replying to your messages

## Local File Storage
You can save files locally that persist across conversations:

**Available Tools:**
- `save_to_local` - Save content to a local file (persists forever)
- `list_local_files` - List saved files for this user
- `read_local_file` - Read a previously saved file
- `delete_local_file` - Delete a saved file
- `send_local_file` - Send a saved file to the Discord chat

**Use Cases:**
- Save important results for later reference
- Store user preferences or notes
- Keep generated content that might be needed again
- Save files from the sandbox permanently (use `download_from_sandbox`)

Each user has their own private file storage.

## Chat History Access
You can search and review the full chat history beyond what's in your current context:

**Available Tools:**
- `search_chat_history` - Search for messages containing specific text
- `get_chat_history` - Retrieve past messages (with optional time filter)

**Use Cases:**
- User asks "what did we talk about yesterday?"
- User asks "find that link I shared last week"
- User wants a summary of past conversations
- Looking up something specific from earlier discussions

**Note:** Only the current channel's history is accessible.
"""

        # Add E2B capabilities if available
        if E2B_ENABLED:
            e2b_context = """

## Code Execution (E2B Sandbox)
You have access to a secure cloud sandbox where you can execute code! This gives you
real computational abilities - you're not just simulating or explaining code.

**Available Tools:**
- `execute_python` - Run Python code (stateful - variables persist across calls)
- `install_package` - Install pip packages (requests, pandas, numpy, etc.)
- `read_file` / `write_file` - Read and write files in the sandbox
- `list_files` - List directory contents
- `run_shell` - Run shell commands (curl, git, etc.)
- `web_search` - Search the web using Tavily (for current info, research, docs)

**When to Use Code Execution:**
- Mathematical calculations (don't calculate in your head - run the code!)
- Data analysis or processing
- Web requests / API calls
- File generation (then share results)
- Testing code snippets users ask about
- Any task where running real code gives better results than explaining

**Important:**
- The sandbox has internet access - you can fetch URLs, call APIs, etc.
- Each user has their own persistent sandbox (variables and files persist)
- Show users what you're doing: mention when you're running code
- If code fails, you'll see the error - fix and retry
- For complex tasks, break into steps and run incrementally

**Example Usage:**
When a user asks "What's 2^100?", instead of trying to calculate mentally:
1. Call `execute_python` with code: `print(2**100)`
2. Return the exact result: 1267650600228229401496703205376

Always prefer running actual code over mental math or approximations!

**IMPORTANT - Sandbox vs Local vs Discord Files:**
- `write_file` tool writes to your SANDBOX filesystem (temporary, for your own use)
- `download_from_sandbox` copies a sandbox file to LOCAL storage (permanent)
- `save_to_local` writes directly to LOCAL storage (permanent)
- `send_local_file` sends a locally saved file to Discord chat
- Or use `<<<file:...>>>` syntax to create and send a file inline:
  ```
  <<<file:result.py>>>
  # Your generated code here
  <<</file>>>
  ```

**Recommended Workflow for Important Files:**
1. Generate content in sandbox with `execute_python`
2. Save to sandbox with `write_file`
3. Download to local storage with `download_from_sandbox`
4. Send to user with `send_local_file`
"""
            context += e2b_context

        return context.strip()

    async def _extract_attachments(
        self, message: DiscordMessage, user_id: str | None = None
    ) -> list[dict]:
        """Extract text content from message attachments.

        Also saves all attachments to local storage if user_id is provided.
        """
        attachments = []
        file_manager = get_file_manager() if user_id else None
        channel_id = str(message.channel.id) if message.channel else None

        for attachment in message.attachments:
            # Check file extension
            filename = attachment.filename.lower()
            original_filename = attachment.filename
            ext = "." + filename.split(".")[-1] if "." in filename else ""

            # Always try to save to local storage first (for later access)
            if file_manager and user_id:
                try:
                    content_bytes = await attachment.read()
                    save_result = file_manager.save_from_bytes(
                        user_id, original_filename, content_bytes, channel_id
                    )
                    if save_result.success:
                        print(f"[discord] Saved attachment to storage: {original_filename}")
                except Exception as e:
                    print(f"[discord] Failed to save attachment locally: {e}")

            if ext not in TEXT_EXTENSIONS:
                # Note: file was still saved locally above
                attachments.append(
                    {
                        "filename": original_filename,
                        "saved_locally": True,
                        "note": f"Binary file saved locally. Use `read_local_file` or `send_local_file` to access.",
                    }
                )
                continue

            # Check file size for inline display
            if attachment.size > MAX_FILE_SIZE:
                size = attachment.size
                print(f"[discord] Large file saved locally: {filename} ({size} bytes)")
                attachments.append(
                    {
                        "filename": original_filename,
                        "saved_locally": True,
                        "note": f"Large file ({size} bytes) saved locally. Use `read_local_file` to access.",
                    }
                )
                continue

            try:
                # Download and decode the file (may already be cached from save above)
                content_bytes = await attachment.read()
                try:
                    content = content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    content = content_bytes.decode("latin-1")

                # Truncate if still too long for inline display
                if len(content) > MAX_CHARS:
                    content = content[:MAX_CHARS] + "\n... [truncated, full file saved locally]"

                attachments.append(
                    {
                        "filename": attachment.filename,
                        "content": content,
                    }
                )
                print(f"[discord] Read attachment: {filename} ({len(content)} chars)")

            except Exception as e:
                print(f"[discord] Error reading attachment {filename}: {e}")
                attachments.append(
                    {
                        "filename": attachment.filename,
                        "error": str(e),
                    }
                )

        return attachments

    async def on_ready(self):
        """Called when bot is ready."""
        print(f"\n{C.GREEN}[discord]{C.RESET} {C.BOLD}Logged in as {C.CYAN}{self.user}{C.RESET}")
        if CLIENT_ID:
            invite = f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}&permissions=274877991936&scope=bot"
            print(f"{C.GRAY}[discord]{C.RESET} Invite URL: {C.BLUE}{invite}{C.RESET}")

        # Update monitor
        monitor.bot_user = str(self.user)
        monitor.start_time = datetime.now(UTC)
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Logged in as {self.user}")

    async def on_guild_join(self, guild):
        """Called when bot joins a guild."""
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Joined server: {guild.name}")

    async def on_guild_remove(self, guild):
        """Called when bot leaves a guild."""
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Left server: {guild.name}")

    async def on_message(self, message: DiscordMessage):
        """Handle incoming messages."""
        # Debug: log all messages
        print(f"{C.BLUE}[discord]{C.RESET} Message from {C.CYAN}{message.author}{C.RESET}: {C.GRAY}{message.content[:50]!r}{C.RESET}")

        # Ignore own messages
        if message.author == self.user:
            return

        # Check if this is a DM
        is_dm = message.guild is None

        # For DMs: always respond (no mention needed)
        # For channels: require mention or reply
        if not is_dm:
            is_mentioned = self.user.mentioned_in(message)
            is_reply_to_bot = (
                message.reference
                and message.reference.resolved
                and message.reference.resolved.author == self.user
            )

            print(f"[discord] mentioned={is_mentioned}, reply_to_bot={is_reply_to_bot}")

            if not is_mentioned and not is_reply_to_bot:
                return

            # Check channel permissions (only for non-DM)
            if ALLOWED_CHANNELS:
                channel_id = str(message.channel.id)
                if channel_id not in ALLOWED_CHANNELS:
                    print(f"[discord] Channel {channel_id} not in allowed list")
                    return

            # Check role permissions (only for non-DM)
            if ALLOWED_ROLES and isinstance(message.author, discord.Member):
                user_roles = {str(r.id) for r in message.author.roles}
                if not user_roles.intersection(set(ALLOWED_ROLES)):
                    return
        else:
            print(f"[discord] DM from {message.author}")

        # Log the incoming message to monitor
        guild_name = message.guild.name if message.guild else None
        channel_name = getattr(message.channel, "name", "DM")
        event_type = "dm" if is_dm else "message"
        monitor.log(
            event_type,
            message.author.display_name,
            message.content,
            guild_name,
            channel_name,
        )

        # Process the message
        await self._handle_message(message, is_dm)

    async def _handle_message(self, message: DiscordMessage, is_dm: bool = False):
        """Process a message and generate a response."""
        content_preview = message.content[:50]
        print(f"{C.BLUE}[discord]{C.RESET} Handling message from {C.CYAN}{message.author}{C.RESET}: {C.GRAY}{content_preview!r}{C.RESET}")

        async with message.channel.typing():
            try:
                # Fetch context: channel history for channels, reply chain for DMs
                if not is_dm:
                    channel_id = f"discord-channel-{message.channel.id}"
                    all_channel_msgs = await self._fetch_channel_history(
                        message.channel
                    )
                    (
                        channel_summary,
                        recent_channel_msgs,
                    ) = await self._get_or_update_channel_summary(
                        channel_id, all_channel_msgs
                    )
                    n_recent = len(recent_channel_msgs)
                    n_sum = len(channel_summary)
                    print(f"[discord] Channel: {n_recent} recent, {n_sum}ch summary")
                else:
                    # DMs: use reply chain, no channel summary
                    recent_channel_msgs = await self._build_message_chain(message)
                    channel_summary = ""
                    print(f"[discord] DM chain: {len(recent_channel_msgs)} msgs")

                # Get thread (shared for channels, per-user for DMs)
                thread, thread_owner = await self._ensure_thread(message, is_dm)
                print(f"[discord] Thread: {thread.id} (owner: {thread_owner})")

                # User ID for memories - always per-user, even in shared channels
                user_id = f"discord-{message.author.id}"
                project_id = await self._ensure_project(user_id)
                print(f"[discord] User: {user_id}, Project: {project_id}")

                # Get the user's message content
                raw_content = self._clean_content(message.content)

                # Extract and append file attachments (also saves to local storage)
                attachments = await self._extract_attachments(message, user_id)
                if attachments:
                    attachment_text = []
                    for att in attachments:
                        if "content" in att:
                            attachment_text.append(
                                f"\n\n--- File: {att['filename']} ---\n{att['content']}"
                            )
                        elif "note" in att:
                            # File saved locally but not shown inline
                            fname, note = att["filename"], att["note"]
                            attachment_text.append(f"\n\n[Attachment: {fname}] {note}")
                        elif "error" in att:
                            fname, err = att["filename"], att["error"]
                            attachment_text.append(f"\n\n[File {fname}: {err}]")
                    raw_content += "".join(attachment_text)
                    print(f"[discord] Added {len(attachments)} file(s) to message")

                # For channels, prefix with username so Clara knows who's speaking
                if not is_dm:
                    display_name = message.author.display_name
                    user_content = f"[{display_name}]: {raw_content}"
                else:
                    user_content = raw_content

                print(f"[discord] Content length: {len(user_content)} chars")

                # Fetch memories
                db = SessionLocal()
                try:
                    user_mems, proj_mems = self.mm.fetch_mem0_context(
                        user_id, project_id, user_content
                    )
                    recent_msgs = self.mm.get_recent_messages(db, thread.id)
                finally:
                    db.close()

                # Build prompt with Clara's persona
                prompt_messages = self.mm.build_prompt(
                    user_mems,
                    proj_mems,
                    thread.session_summary,
                    recent_msgs,
                    user_content,
                )

                # Inject Discord-specific context after the base system prompt
                discord_context = self._build_discord_context(
                    message, user_mems, proj_mems, is_dm
                )
                # Insert as second system message (after Clara's persona)
                system_msg = {"role": "system", "content": discord_context}
                prompt_messages.insert(1, system_msg)

                # Add channel summary if available (for channels only)
                if channel_summary:
                    summary_content = (
                        f"## Earlier Channel Context (summarized)\n{channel_summary}"
                    )
                    summary_msg = {"role": "system", "content": summary_content}
                    prompt_messages.insert(2, summary_msg)

                # Add recent channel/DM messages as context
                if len(recent_channel_msgs) > 1:
                    channel_context = []
                    for msg in recent_channel_msgs[:-1]:  # All except current message
                        role = "assistant" if msg.is_bot else "user"
                        if not is_dm and not msg.is_bot:
                            # Prefix with username for channel messages
                            content = f"[{msg.username}]: {msg.content}"
                        else:
                            content = msg.content
                        channel_context.append({"role": role, "content": content})

                    # Insert before the last user message
                    prompt_messages = (
                        prompt_messages[:-1] + channel_context + [prompt_messages[-1]]
                    )

                # Debug: check E2B status
                e2b_available = E2B_ENABLED and get_sandbox_manager().is_available()
                print(f"[discord] E2B status: enabled={E2B_ENABLED}, available={e2b_available}")

                # Generate streaming response
                response = await self._generate_response(message, prompt_messages)

                # Store in Clara's memory system
                # Use thread_owner for message storage, user_id for memories
                if response:
                    await self._store_exchange(
                        thread_owner,  # For message storage in shared thread
                        user_id,  # For per-user memory extraction
                        project_id,
                        thread.id,
                        user_content,
                        response,
                    )

                    # Log response to monitor
                    guild_name = message.guild.name if message.guild else None
                    channel_name = getattr(message.channel, "name", "DM")
                    response_preview = (
                        response[:200] + "..." if len(response) > 200 else response
                    )
                    monitor.log(
                        "response", "Clara", response_preview, guild_name, channel_name
                    )

            except Exception as e:
                print(f"{C.RED}[error]{C.RESET} Handling message: {e}")
                import traceback

                traceback.print_exc()

                # Log error to monitor
                guild_name = message.guild.name if message.guild else None
                channel_name = getattr(message.channel, "name", "DM")
                monitor.log("error", "Bot", str(e), guild_name, channel_name)

                err_msg = f"Sorry, I encountered an error: {str(e)[:100]}"
                await message.reply(err_msg, mention_author=False)

    async def _build_message_chain(
        self, message: DiscordMessage
    ) -> list[CachedMessage]:
        """Build conversation chain from reply history."""
        chain: list[CachedMessage] = []
        current = message
        seen_ids: set[int] = set()

        while current and len(chain) < MAX_MESSAGES:
            if current.id in seen_ids:
                break
            seen_ids.add(current.id)

            # Get or cache message
            cached = await self._get_or_cache_message(current)
            chain.insert(0, cached)

            # Follow reply chain
            if current.reference and current.reference.message_id:
                try:
                    current = await message.channel.fetch_message(
                        current.reference.message_id
                    )
                except discord.NotFound:
                    break
            else:
                break

        return chain

    async def _get_or_cache_message(self, message: DiscordMessage) -> CachedMessage:
        """Get cached message or create new cache entry."""
        async with self.cache_lock:
            if message.id in self.msg_cache:
                return self.msg_cache[message.id]

            # Create new cache entry
            content = self._clean_content(message.content)

            # Truncate if too long
            if len(content) > MAX_CHARS:
                content = content[:MAX_CHARS] + "... [truncated]"

            cached = CachedMessage(
                content=content,
                user_id=str(message.author.id),
                username=message.author.display_name,
                is_bot=message.author.bot,
                timestamp=message.created_at,
            )

            # Cache management (limit size)
            if len(self.msg_cache) >= 500:
                # Remove oldest entries
                oldest = sorted(self.msg_cache.items(), key=lambda x: x[1].timestamp)[
                    :100
                ]
                for msg_id, _ in oldest:
                    del self.msg_cache[msg_id]

            self.msg_cache[message.id] = cached
            return cached

    def _clean_content(self, content: str) -> str:
        """Clean message content by removing bot mentions."""
        # Remove mentions of this bot
        if self.user:
            content = re.sub(rf"<@!?{self.user.id}>", "", content)
        return content.strip()

    async def _fetch_channel_history(
        self, channel, limit: int = CHANNEL_HISTORY_LIMIT
    ) -> list[CachedMessage]:
        """Fetch recent channel messages.

        Returns:
            list of CachedMessage in chronological order
        """
        messages = []
        async for msg in channel.history(limit=limit):
            cached = CachedMessage(
                content=self._clean_content(msg.content),
                user_id=str(msg.author.id),
                username=msg.author.display_name,
                is_bot=msg.author.bot,
                timestamp=msg.created_at,
            )
            messages.append(cached)

        messages.reverse()  # chronological order
        return messages

    async def _get_or_update_channel_summary(
        self,
        channel_id: str,
        messages: list[CachedMessage],
    ) -> tuple[str, list[CachedMessage]]:
        """Split messages into summary + recent based on time threshold.

        Returns:
            tuple: (summary_text, recent_messages_within_threshold)
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=SUMMARY_AGE_MINUTES)

        # Split messages by age
        old_messages = [m for m in messages if m.timestamp < cutoff]
        recent_messages = [m for m in messages if m.timestamp >= cutoff]

        db = SessionLocal()
        try:
            summary_record = (
                db.query(ChannelSummary).filter_by(channel_id=channel_id).first()
            )

            # Check if we need to update summary
            needs_update = False
            if not summary_record:
                summary_record = ChannelSummary(channel_id=channel_id)
                db.add(summary_record)
                needs_update = bool(old_messages)
            elif old_messages:
                # Check if there are new old messages since last summary
                last_old_ts = old_messages[-1].timestamp.replace(tzinfo=None)
                if (
                    not summary_record.summary_cutoff_at
                    or last_old_ts > summary_record.summary_cutoff_at
                ):
                    needs_update = True

            if needs_update and old_messages:
                # Generate new summary including old summary + new old messages
                existing_summary = summary_record.summary or ""
                new_summary = await self._summarize_messages(
                    existing_summary, old_messages
                )
                summary_record.summary = new_summary
                summary_record.summary_cutoff_at = old_messages[-1].timestamp.replace(
                    tzinfo=None
                )
                db.commit()
                print(f"[discord] Updated channel summary for {channel_id}")

            return summary_record.summary or "", recent_messages
        finally:
            db.close()

    async def _summarize_messages(
        self,
        existing_summary: str,
        messages: list[CachedMessage],
    ) -> str:
        """Generate a summary of messages, incorporating existing summary."""
        # Format messages for summarization
        formatted = []
        for msg in messages:
            role = "Clara" if msg.is_bot else msg.username
            content = msg.content[:500]  # truncate long messages
            formatted.append(f"{role}: {content}")

        conversation = "\n".join(formatted)

        if existing_summary:
            user_content = (
                f"Previous summary:\n{existing_summary}\n\n"
                f"New messages to incorporate:\n{conversation}\n\n"
                f"Provide an updated summary:"
            )
        else:
            user_content = f"Conversation:\n{conversation}\n\n" f"Provide a summary:"

        prompt = [
            {
                "role": "system",
                "content": (
                    "You are summarizing a Discord channel conversation. "
                    "Create a concise summary (3-5 sentences) capturing key topics, "
                    "decisions, and context. Write in past tense. "
                    "Focus on information that would help continue the conversation."
                ),
            },
            {"role": "user", "content": user_content},
        ]

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(None, lambda: self._sync_llm(prompt))
        return summary

    async def _ensure_project(self, user_id: str) -> str:
        """Ensure project exists and return its ID."""
        db = SessionLocal()
        try:
            proj = (
                db.query(Project)
                .filter_by(owner_id=user_id, name=DEFAULT_PROJECT)
                .first()
            )
            if not proj:
                proj = Project(owner_id=user_id, name=DEFAULT_PROJECT)
                db.add(proj)
                db.commit()
                db.refresh(proj)
            return proj.id
        finally:
            db.close()

    async def _ensure_thread(
        self, message: DiscordMessage, is_dm: bool
    ) -> tuple[Session, str]:
        """Get or create a thread based on context.

        For channels: One shared thread per channel (all users share context)
        For DMs: One thread per user (private conversations)

        Returns:
            tuple: (thread, thread_owner_id)
        """
        db = SessionLocal()
        try:
            if is_dm:
                # DMs: per-user thread
                thread_owner = f"discord-dm-{message.author.id}"
                thread_title = f"DM with {message.author.display_name}"
            else:
                # Channels: shared thread for the channel
                thread_owner = f"discord-channel-{message.channel.id}"
                guild_name = message.guild.name if message.guild else "Server"
                channel_name = getattr(message.channel, "name", "channel")
                thread_title = f"{guild_name} #{channel_name}"

            # Find existing active thread
            thread = (
                db.query(Session)
                .filter_by(user_id=thread_owner, title=thread_title)
                .filter(Session.archived != "true")
                .order_by(Session.last_activity_at.desc())
                .first()
            )

            if not thread:
                project_id = await self._ensure_project(thread_owner)
                thread = Session(
                    project_id=project_id,
                    user_id=thread_owner,
                    title=thread_title,
                    archived="false",
                )
                db.add(thread)
                db.commit()
                db.refresh(thread)
                print(f"[discord] Created thread: {thread_title}")

            return thread, thread_owner
        finally:
            db.close()

    async def _generate_response(
        self, message: DiscordMessage, prompt_messages: list[dict]
    ) -> str:
        """Generate response and send to Discord, handling tool calls."""
        print(f"{C.BLUE}[discord]{C.RESET} Generating response for {C.CYAN}{message.author}{C.RESET}...")
        user_id = f"discord-{message.author.id}"

        try:
            loop = asyncio.get_event_loop()
            full_response = ""

            # Determine if we should use tools
            # Local file tools are always available; E2B tools require setup
            sandbox_mgr = get_sandbox_manager()
            e2b_available = E2B_ENABLED and sandbox_mgr.is_available()

            # Always use tools (local file tools are always available)
            # Build the active tool list
            if e2b_available:
                print(f"{C.BLUE}[discord]{C.RESET} Using tool-calling mode {C.GREEN}(E2B + local files){C.RESET}")
                active_tools = ALL_TOOLS
            else:
                print(f"{C.BLUE}[discord]{C.RESET} Using tool-calling mode {C.YELLOW}(local files only){C.RESET}")
                active_tools = LOCAL_FILE_TOOLS

            # Generate with tools
            full_response, files_to_send = await self._generate_with_tools(
                message, prompt_messages, user_id, loop, active_tools
            )

            print(f"{C.GREEN}[discord]{C.RESET} Got response: {C.WHITE}{len(full_response)}{C.RESET} chars")

            if not full_response:
                print(f"{C.YELLOW}[warning]{C.RESET} Empty response from LLM")
                full_response = "I'm sorry, I didn't generate a response."

            # Extract any file attachments from the response text
            cleaned_response, inline_files = self._extract_file_attachments(full_response)
            temp_paths = []
            discord_files = []

            # Create Discord files from inline <<<file:>>> syntax
            if inline_files:
                inline_discord_files, temp_paths = self._create_discord_files(inline_files)
                discord_files.extend(inline_discord_files)
                print(f"[discord] Extracted {len(inline_files)} inline file(s)")

            # Add files from send_local_file tool calls
            if files_to_send:
                for file_path in files_to_send:
                    if file_path.exists():
                        discord_files.append(discord.File(fp=str(file_path), filename=file_path.name))
                        print(f"[discord] Adding local file: {file_path.name}")

            # Split the response into chunks and send each
            chunks = self._split_message(cleaned_response)
            print(f"[discord] Sending {len(chunks)} message(s)")

            try:
                response_msg = None
                for i, chunk in enumerate(chunks):
                    # Attach files to the first message only
                    chunk_files = discord_files if i == 0 else []

                    if i == 0:
                        # First message is a reply
                        if chunk_files:
                            n_files = len(chunk_files)
                            print(f"[discord] Sending reply with {n_files} file(s)")
                        response_msg = await message.reply(
                            chunk, mention_author=False, files=chunk_files
                        )
                    else:
                        # Subsequent messages are follow-ups in the channel
                        response_msg = await message.channel.send(chunk)

                print(f"{C.GREEN}[discord]{C.RESET} Sent reply to Discord")
            finally:
                # Clean up temp files after sending
                if temp_paths:
                    self._cleanup_temp_files(temp_paths)

            # Cache the bot's last response message
            if response_msg:
                async with self.cache_lock:
                    self.msg_cache[response_msg.id] = CachedMessage(
                        content=full_response,
                        user_id=str(self.user.id) if self.user else "",
                        username="Clara",
                        is_bot=True,
                    )

        except Exception as e:
            print(f"{C.RED}[error]{C.RESET} Generating response: {e}")
            import traceback

            traceback.print_exc()
            error_msg = f"I had trouble generating a response: {str(e)[:100]}"
            await message.reply(error_msg, mention_author=False)
            return ""

        return full_response

    async def _generate_with_tools(
        self,
        message: DiscordMessage,
        prompt_messages: list[dict],
        user_id: str,
        loop: asyncio.AbstractEventLoop,
        active_tools: list[dict],
    ) -> tuple[str, list]:
        """Generate response with tool calling support.

        Returns:
            tuple: (response_text, list of file paths to send)
        """
        from pathlib import Path

        sandbox_manager = get_sandbox_manager()
        file_manager = get_file_manager()
        messages = list(prompt_messages)  # Copy to avoid mutation

        # Track files to send to Discord
        files_to_send: list[Path] = []

        # Add explicit tool instruction at the start for the tool model
        tool_instruction = {
            "role": "system",
            "content": (
                "You have access to tools for code execution and file management. "
                "When the user asks you to calculate, run code, analyze data, "
                "fetch URLs, install packages, or do anything computational - "
                "USE THE TOOLS. Do not just explain what you would do - actually "
                "call the execute_python or other tools to do it. "
                "For any math beyond basic arithmetic, USE execute_python. "
                "You can also save files locally with save_to_local and send "
                "them to chat with send_local_file."
            ),
        }
        messages.insert(0, tool_instruction)

        # Tool execution tracking
        total_tools_run = 0

        # Tool status messages (E2B + local file tools)
        tool_status = {
            # E2B tools
            "execute_python": ("🐍", "Running Python code"),
            "install_package": ("📦", "Installing package"),
            "read_file": ("📖", "Reading sandbox file"),
            "write_file": ("💾", "Writing sandbox file"),
            "list_files": ("📁", "Listing sandbox files"),
            "run_shell": ("💻", "Running shell command"),
            "unzip_file": ("📂", "Extracting archive"),
            "web_search": ("🔍", "Searching the web"),
            "run_claude_code": ("🤖", "Running Claude Code agent"),
            # Local file tools
            "save_to_local": ("💾", "Saving locally"),
            "list_local_files": ("📁", "Listing saved files"),
            "read_local_file": ("📖", "Reading local file"),
            "delete_local_file": ("🗑️", "Deleting file"),
            "download_from_sandbox": ("⬇️", "Downloading from sandbox"),
            "upload_to_sandbox": ("⬆️", "Uploading to sandbox"),
            "send_local_file": ("📤", "Preparing file"),
            # Chat history tools
            "search_chat_history": ("🔎", "Searching chat history"),
            "get_chat_history": ("📜", "Retrieving chat history"),
        }

        for iteration in range(MAX_TOOL_ITERATIONS):
            print(f"{C.MAGENTA}[tools]{C.RESET} Iteration {C.WHITE}{iteration + 1}{C.RESET}/{MAX_TOOL_ITERATIONS}")

            # Call LLM with tools
            def call_llm():
                llm = make_llm_with_tools(active_tools)
                return llm(messages)

            completion = await loop.run_in_executor(None, call_llm)
            response_message = completion.choices[0].message

            # Check if there are tool calls
            if not response_message.tool_calls:
                if iteration == 0:
                    # First iteration with no tools - fall back to main chat LLM
                    # This preserves the main LLM's personality for regular chat
                    print(f"{C.BLUE}[discord]{C.RESET} No tools needed, using {C.CYAN}main chat LLM{C.RESET}")

                    # Remove the tool instruction we added
                    original_messages = [m for m in messages if m.get("content") != tool_instruction["content"]]

                    def main_llm_call():
                        llm = make_llm()
                        return llm(original_messages)

                    result = await loop.run_in_executor(None, main_llm_call)
                    return result or "", files_to_send
                else:
                    # Tools were used in previous iterations, return tool model's response
                    return response_message.content or "", files_to_send

            # Process tool calls
            tool_count = len(response_message.tool_calls)
            print(f"{C.MAGENTA}[tools]{C.RESET} Processing {C.YELLOW}{tool_count}{C.RESET} tool call(s)")

            # Add assistant message with tool calls to conversation
            messages.append(
                {
                    "role": "assistant",
                    "content": response_message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in response_message.tool_calls
                    ],
                }
            )

            # Execute each tool call and add results
            for tool_call in response_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    raw_args = tool_call.function.arguments
                    arguments = json.loads(raw_args) if raw_args else {}
                except (json.JSONDecodeError, TypeError):
                    arguments = {}

                print(f"{C.MAGENTA}[tools]{C.RESET} Executing: {C.CYAN}{tool_name}{C.RESET}")

                # Get friendly status for this tool
                emoji, action = tool_status.get(tool_name, ("⚙️", "Working"))

                # Build status text with context
                if tool_name == "execute_python":
                    desc = arguments.get("description", "")
                    status_text = f"{emoji} {action}..." if not desc else f"{emoji} {desc}..."
                elif tool_name == "install_package":
                    pkg = arguments.get("package", "package")
                    status_text = f"{emoji} Installing `{pkg}`..."
                elif tool_name in ("read_file", "write_file", "unzip_file"):
                    path = arguments.get("path", "file")
                    filename = path.split("/")[-1] if "/" in path else path
                    status_text = f"{emoji} {action}: `{filename}`..."
                elif tool_name == "run_shell":
                    cmd = arguments.get("command", "")[:30]
                    status_text = f"{emoji} Running: `{cmd}`..."
                elif tool_name == "web_search":
                    query = arguments.get("query", "")[:40]
                    status_text = f"{emoji} Searching: `{query}`..."
                elif tool_name in ("save_to_local", "read_local_file", "delete_local_file", "send_local_file"):
                    filename = arguments.get("filename", "file")
                    status_text = f"{emoji} {action}: `{filename}`..."
                elif tool_name == "download_from_sandbox":
                    path = arguments.get("sandbox_path", "file")
                    filename = path.split("/")[-1] if "/" in path else path
                    status_text = f"{emoji} Downloading: `{filename}`..."
                elif tool_name == "upload_to_sandbox":
                    filename = arguments.get("local_filename", "file")
                    status_text = f"{emoji} Uploading: `{filename}`..."
                elif tool_name == "search_chat_history":
                    query = arguments.get("query", "")[:30]
                    status_text = f"{emoji} Searching for: `{query}`..."
                elif tool_name == "get_chat_history":
                    count = arguments.get("count", 50)
                    status_text = f"{emoji} Retrieving {count} messages..."
                else:
                    status_text = f"{emoji} {action}..."

                # Send status message as an interrupt (stays in chat)
                total_tools_run += 1
                step_label = f" (step {total_tools_run})" if total_tools_run > 1 else ""
                try:
                    await message.channel.send(
                        f"-# {status_text}{step_label}",
                        silent=True,
                    )
                except Exception as e:
                    print(f"[discord] Failed to send status: {e}")

                # Execute the tool - handle both E2B and local file tools
                tool_output = await self._execute_tool(
                    tool_name, arguments, user_id, sandbox_manager, file_manager,
                    files_to_send, message.channel
                )

                # Add tool result to conversation
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_output,
                    }
                )

                success = not tool_output.startswith("Error:")
                status = f"{C.GREEN}success{C.RESET}" if success else f"{C.RED}failed{C.RESET}"
                print(f"{C.MAGENTA}[tools]{C.RESET} {C.CYAN}{tool_name}{C.RESET} → {status}")

            # Show typing indicator while processing
            async with message.channel.typing():
                await asyncio.sleep(0.1)  # Brief pause

        # Max iterations reached - send status and ask LLM to summarize
        print(f"{C.YELLOW}[tools]{C.RESET} Max iterations reached, requesting summary")

        try:
            await message.channel.send("-# ⏳ Wrapping up...", silent=True)
        except Exception:
            pass

        messages.append(
            {
                "role": "user",
                "content": (
                    "You've reached the maximum number of tool calls. "
                    "Please summarize what you've accomplished."
                ),
            }
        )

        def final_call():
            from llm_backends import TOOL_FORMAT, _convert_messages_to_claude_format
            llm = make_llm()  # Use simple LLM for final response
            # Convert messages if using Claude format
            if TOOL_FORMAT == "claude":
                converted = _convert_messages_to_claude_format(messages)
                return llm(converted)
            return llm(messages)

        result = await loop.run_in_executor(None, final_call)
        return result, files_to_send

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: dict,
        user_id: str,
        sandbox_manager,
        file_manager,
        files_to_send: list,
        channel=None,
    ) -> str:
        """Execute a tool and return the output string.

        Handles E2B sandbox tools, local file tools, and chat history tools.
        """
        from pathlib import Path

        # Get channel_id for file storage organization
        channel_id = str(channel.id) if channel else None

        # E2B sandbox tools (including web_search which uses Tavily)
        e2b_tools = {
            "execute_python", "install_package", "read_file",
            "write_file", "list_files", "run_shell", "unzip_file",
            "web_search", "run_claude_code"
        }

        if tool_name in e2b_tools:
            # Use E2B sandbox manager
            result = await sandbox_manager.handle_tool_call(user_id, tool_name, arguments)
            if result.success:
                return result.output
            else:
                return f"Error: {result.error}"

        # Chat history tools (require channel access)
        elif tool_name == "search_chat_history":
            if not channel:
                return "Error: No channel available for history search"
            return await self._search_chat_history(
                channel,
                arguments.get("query", ""),
                arguments.get("limit", 200),
                arguments.get("from_user"),
            )

        elif tool_name == "get_chat_history":
            if not channel:
                return "Error: No channel available for history retrieval"
            return await self._get_chat_history(
                channel,
                arguments.get("count", 50),
                arguments.get("before_hours"),
                arguments.get("user_filter"),
            )

        # Local file tools
        elif tool_name == "save_to_local":
            filename = arguments.get("filename", "unnamed.txt")
            content = arguments.get("content", "")
            result = file_manager.save_file(user_id, filename, content, channel_id)
            return result.message

        elif tool_name == "list_local_files":
            files = file_manager.list_files(user_id, channel_id)
            if not files:
                return "No files saved yet."
            lines = []
            for f in files:
                size = f"{f.size} bytes" if f.size < 1024 else f"{f.size / 1024:.1f} KB"
                lines.append(f"- {f.name} ({size})")
            return "Saved files:\n" + "\n".join(lines)

        elif tool_name == "read_local_file":
            filename = arguments.get("filename", "")
            result = file_manager.read_file(user_id, filename, channel_id)
            return result.message

        elif tool_name == "delete_local_file":
            filename = arguments.get("filename", "")
            result = file_manager.delete_file(user_id, filename, channel_id)
            return result.message

        elif tool_name == "download_from_sandbox":
            sandbox_path = arguments.get("sandbox_path", "")
            local_filename = arguments.get("local_filename", "")
            if not local_filename:
                local_filename = sandbox_path.split("/")[-1] if "/" in sandbox_path else sandbox_path

            # Read from sandbox
            read_result = await sandbox_manager.read_file(user_id, sandbox_path)
            if not read_result.success:
                return f"Error reading from sandbox: {read_result.error}"

            # Save locally (organized by user/channel)
            content = read_result.output
            save_result = file_manager.save_file(user_id, local_filename, content, channel_id)
            return save_result.message

        elif tool_name == "upload_to_sandbox":
            local_filename = arguments.get("local_filename", "")
            sandbox_path = arguments.get("sandbox_path", "")

            # Read from local storage as bytes (preserves binary files)
            content, error = file_manager.read_file_bytes(user_id, local_filename, channel_id)
            if content is None:
                return f"Error: {error}"

            # Determine sandbox path
            if not sandbox_path:
                sandbox_path = f"/home/user/{local_filename}"

            # Write to sandbox (bytes supported)
            write_result = await sandbox_manager.write_file(user_id, sandbox_path, content)
            if write_result.success:
                size_kb = len(content) / 1024
                return f"Uploaded '{local_filename}' ({size_kb:.1f} KB) to sandbox at {sandbox_path}"
            else:
                return f"Error uploading to sandbox: {write_result.error}"

        elif tool_name == "send_local_file":
            filename = arguments.get("filename", "")
            file_path = file_manager.get_file_path(user_id, filename, channel_id)
            if file_path:
                files_to_send.append(file_path)
                return f"File '{filename}' will be sent to chat."
            else:
                return f"File not found: {filename}"

        else:
            return f"Unknown tool: {tool_name}"

    async def _search_chat_history(
        self,
        channel,
        query: str,
        limit: int = 200,
        from_user: str | None = None,
    ) -> str:
        """Search through channel message history for matching messages."""
        if not query:
            return "Error: No search query provided"

        limit = min(max(10, limit), 1000)  # Clamp to 10-1000
        query_lower = query.lower()
        matches = []

        try:
            async for msg in channel.history(limit=limit):
                # Skip bot's own messages if searching for user content
                content = msg.content.lower()

                # Check user filter
                if from_user:
                    if from_user.lower() not in msg.author.display_name.lower():
                        continue

                # Check if query matches
                if query_lower in content:
                    timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                    author = msg.author.display_name
                    # Truncate long messages
                    text = msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
                    matches.append(f"[{timestamp}] {author}: {text}")

                    # Limit results
                    if len(matches) >= 20:
                        break

            if not matches:
                return f"No messages found matching '{query}'"

            result = f"Found {len(matches)} message(s) matching '{query}':\n\n"
            result += "\n\n".join(matches)
            return result

        except Exception as e:
            return f"Error searching history: {str(e)}"

    async def _get_chat_history(
        self,
        channel,
        count: int = 50,
        before_hours: float | None = None,
        user_filter: str | None = None,
    ) -> str:
        """Retrieve chat history from the channel."""
        count = min(max(10, count), 200)  # Clamp to 10-200
        messages = []

        try:
            # Calculate before timestamp if specified
            before = None
            if before_hours:
                before = datetime.now(UTC) - timedelta(hours=before_hours)

            async for msg in channel.history(limit=count * 2, before=before):
                # Check user filter
                if user_filter:
                    if user_filter.lower() not in msg.author.display_name.lower():
                        continue

                timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                author = msg.author.display_name
                is_bot = " [Clara]" if msg.author == self.user else ""
                # Truncate long messages
                text = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
                messages.append(f"[{timestamp}] {author}{is_bot}: {text}")

                if len(messages) >= count:
                    break

            if not messages:
                return "No messages found in the specified range"

            # Reverse to chronological order
            messages.reverse()

            time_desc = ""
            if before_hours:
                time_desc = f" (older than {before_hours} hours)"

            result = f"Chat history ({len(messages)} messages){time_desc}:\n\n"
            result += "\n\n".join(messages)
            return result

        except Exception as e:
            return f"Error retrieving history: {str(e)}"

    def _split_message(self, text: str, max_len: int = DISCORD_MSG_LIMIT) -> list[str]:
        """Split a long message into multiple chunks at logical boundaries."""
        if len(text) <= max_len:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break

            # Find the best split point within max_len
            chunk = remaining[:max_len]
            split_point = max_len

            # Try to split at code block boundary first (```)
            # Don't split in the middle of a code block
            code_block_count = chunk.count("```")
            if code_block_count % 2 == 1:
                # We're in the middle of a code block, find the start
                last_fence = chunk.rfind("```")
                if last_fence > 0:
                    split_point = last_fence

            # If not in code block, try paragraph break
            if split_point == max_len:
                para_break = chunk.rfind("\n\n")
                if para_break > max_len // 2:  # Only if reasonably far in
                    split_point = para_break + 2

            # Try single newline
            if split_point == max_len:
                newline = chunk.rfind("\n")
                if newline > max_len // 2:
                    split_point = newline + 1

            # Try sentence boundary (. ! ?)
            if split_point == max_len:
                for punct in [". ", "! ", "? "]:
                    pos = chunk.rfind(punct)
                    if pos > max_len // 2:
                        split_point = pos + len(punct)
                        break

            # Try space (word boundary)
            if split_point == max_len:
                space = chunk.rfind(" ")
                if space > max_len // 2:
                    split_point = space + 1

            # Last resort: hard cut
            if split_point == max_len:
                split_point = max_len

            chunks.append(remaining[:split_point].rstrip())
            remaining = remaining[split_point:].lstrip()

        return chunks

    def _extract_file_attachments(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """Extract file attachments from response text.

        Returns:
            tuple: (cleaned_text, list of (filename, content) tuples)
        """
        # Pattern to match <<<file:filename>>>content<<</file>>>
        pattern = r"<<<file:([^>]+)>>>(.*?)<<</file>>>"
        files = []

        def replace_file(match):
            filename = match.group(1).strip()
            content = match.group(2).strip()
            print(f"[discord] Matched file: {filename} ({len(content)} chars)")
            files.append((filename, content))
            return f"📎 *Attached: {filename}*"

        cleaned = re.sub(pattern, replace_file, text, flags=re.DOTALL)

        # Debug: check if pattern might be slightly different
        if not files and "<<<file:" in text:
            print("[discord] WARNING: Found <<<file: but pattern didn't match")
            print(f"[discord] Text snippet: {text[:500]}")

        return cleaned, files

    def _create_discord_files(
        self, files: list[tuple[str, str]]
    ) -> tuple[list[discord.File], list[str]]:
        """Create discord.File objects from extracted files using temp files.

        Returns:
            tuple: (list of discord.File objects, list of temp file paths to clean up)
        """
        import tempfile

        discord_files = []
        temp_paths = []

        for filename, content in files:
            if not content:
                print(f"[discord] Skipping empty file: {filename}")
                continue
            try:
                # Get file extension for proper temp file naming
                ext = ""
                if "." in filename:
                    ext = "." + filename.rsplit(".", 1)[-1]

                # Write to temp file (more robust for large files)
                fd, temp_path = tempfile.mkstemp(suffix=ext, prefix="clara_")
                temp_paths.append(temp_path)

                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)

                # Create discord.File from the temp file path
                discord_file = discord.File(fp=temp_path, filename=filename)
                discord_files.append(discord_file)
                print(f"[discord] Created file: {filename} ({len(content)} chars)")

            except Exception as e:
                print(f"[discord] Error creating file {filename}: {e}")

        return discord_files, temp_paths

    def _cleanup_temp_files(self, temp_paths: list[str]):
        """Clean up temporary files after sending."""
        for path in temp_paths:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception as e:
                print(f"[discord] Error cleaning up temp file {path}: {e}")

    async def _store_exchange(
        self,
        thread_owner_id: str,
        memory_user_id: str,
        project_id: str,
        thread_id: str,
        user_message: str,
        assistant_reply: str,
    ):
        """Store the exchange in Clara's memory system.

        Args:
            thread_owner_id: ID for message storage (channel or DM owner)
            memory_user_id: ID for mem0 memory extraction (always per-user)
            project_id: Project ID for memory organization
            thread_id: Thread ID for message storage
            user_message: The user's message
            assistant_reply: Clara's response
        """
        db = SessionLocal()
        try:
            thread = self.mm.get_thread(db, thread_id)
            if not thread:
                return

            recent_msgs = self.mm.get_recent_messages(db, thread_id)

            # Store messages under thread owner (shared for channels)
            self.mm.store_message(db, thread_id, thread_owner_id, "user", user_message)
            self.mm.store_message(
                db, thread_id, thread_owner_id, "assistant", assistant_reply
            )
            thread.last_activity_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()

            # Update summary periodically
            if self.mm.should_update_summary(db, thread_id):
                self.mm.update_thread_summary(db, thread)

            # Add to mem0 for per-user memory extraction
            self.mm.add_to_mem0(
                memory_user_id, project_id, recent_msgs, user_message, assistant_reply
            )
            print(f"[discord] Stored exchange (thread: {thread_owner_id[:20]}...)")

        finally:
            db.close()


# ============== FastAPI Monitor Dashboard ==============

monitor_app = FastAPI(title="Clara Discord Monitor")

monitor_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@monitor_app.get("/api/stats")
def get_stats():
    """Get bot statistics."""
    return monitor.get_stats()


@monitor_app.get("/api/guilds")
def get_guilds():
    """Get list of guilds."""
    return {"guilds": list(monitor.guilds.values())}


@monitor_app.get("/api/logs")
def get_logs(limit: int = 50, event_type: str | None = None):
    """Get recent log entries."""
    logs = list(monitor.logs)
    if event_type:
        logs = [entry for entry in logs if entry.event_type == event_type]
    return {"logs": [entry.to_dict() for entry in logs[:limit]]}


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clara Discord Monitor</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: system-ui, -apple-system, sans-serif;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
            min-height: 100vh;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            color: #7289da;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        h1 .status {
            width: 12px;
            height: 12px;
            background: #43b581;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
            text-align: center;
        }
        .stat-card .value {
            font-size: 2.5em;
            font-weight: bold;
            color: #7289da;
        }
        .stat-card .label { color: #888; margin-top: 5px; }
        .section {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .section h2 {
            color: #7289da;
            margin-bottom: 15px;
            font-size: 1.2em;
        }
        .guild-list { display: flex; flex-wrap: wrap; gap: 10px; }
        .guild {
            background: #1a1a2e;
            border-radius: 8px;
            padding: 10px 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .guild img { width: 32px; height: 32px; border-radius: 50%; }
        .guild .icon-placeholder {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #7289da;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
        }
        .guild .info .name { font-weight: 500; }
        .guild .info .members { font-size: 0.85em; color: #888; }
        .tabs { display: flex; gap: 10px; margin-bottom: 15px; }
        .tab {
            padding: 8px 16px;
            background: #1a1a2e;
            border: none;
            border-radius: 5px;
            color: #888;
            cursor: pointer;
            transition: all 0.2s;
        }
        .tab:hover { color: #eee; }
        .tab.active { background: #7289da; color: white; }
        .log-list { max-height: 500px; overflow-y: auto; }
        .log-entry {
            padding: 12px;
            border-bottom: 1px solid #1a1a2e;
            display: grid;
            grid-template-columns: 100px 80px 1fr;
            gap: 10px;
            align-items: start;
        }
        .log-entry:hover { background: #1a1a2e; }
        .log-entry .time { color: #666; font-size: 0.85em; }
        .log-entry .type {
            font-size: 0.75em;
            padding: 3px 8px;
            border-radius: 3px;
            text-transform: uppercase;
            font-weight: 600;
        }
        .log-entry .type.message { background: #3ba55d; }
        .log-entry .type.dm { background: #5865f2; }
        .log-entry .type.response { background: #faa61a; color: #000; }
        .log-entry .type.error { background: #ed4245; }
        .log-entry .type.system { background: #747f8d; }
        .log-entry .content { display: flex; flex-direction: column; gap: 3px; }
        .log-entry .meta { color: #888; font-size: 0.85em; }
        .log-entry .text { word-break: break-word; }
        .uptime { color: #888; font-size: 0.9em; margin-left: auto; }
        .refresh-note { color: #666; font-size: 0.85em; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>
            <span class="status"></span>
            Clara Discord Monitor
            <span class="uptime" id="uptime"></span>
        </h1>
        <div class="grid" id="stats"></div>
        <div class="section">
            <h2>Servers</h2>
            <div class="guild-list" id="guilds"></div>
        </div>
        <div class="section">
            <h2>Activity Log</h2>
            <div class="tabs">
                <button class="tab active" data-filter="">All</button>
                <button class="tab" data-filter="message">Messages</button>
                <button class="tab" data-filter="dm">DMs</button>
                <button class="tab" data-filter="response">Responses</button>
                <button class="tab" data-filter="error">Errors</button>
            </div>
            <div class="log-list" id="logs"></div>
            <div class="refresh-note">Auto-refreshes every 3 seconds</div>
        </div>
    </div>
    <script>
        let currentFilter = '';
        function formatUptime(seconds) {
            if (!seconds) return '';
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = Math.floor(seconds % 60);
            if (h > 0) return `Uptime: ${h}h ${m}m`;
            if (m > 0) return `Uptime: ${m}m ${s}s`;
            return `Uptime: ${s}s`;
        }
        function formatTime(isoString) {
            return new Date(isoString).toLocaleTimeString();
        }
        async function fetchStats() {
            const res = await fetch('/api/stats');
            const data = await res.json();
            document.getElementById('uptime').textContent =
                formatUptime(data.uptime_seconds);
            document.getElementById('stats').innerHTML = `
                <div class="stat-card">
                    <div class="value">${data.guild_count}</div>
                    <div class="label">Servers</div>
                </div>
                <div class="stat-card">
                    <div class="value">${data.message_count}</div>
                    <div class="label">Messages</div>
                </div>
                <div class="stat-card">
                    <div class="value">${data.dm_count}</div>
                    <div class="label">DMs</div>
                </div>
                <div class="stat-card">
                    <div class="value">${data.response_count}</div>
                    <div class="label">Responses</div>
                </div>
                <div class="stat-card">
                    <div class="value">${data.error_count}</div>
                    <div class="label">Errors</div>
                </div>
            `;
        }
        async function fetchGuilds() {
            const res = await fetch('/api/guilds');
            const data = await res.json();
            document.getElementById('guilds').innerHTML = data.guilds.map(g => `
                <div class="guild">
                    ${g.icon
                        ? `<img src="${g.icon}" alt="${g.name}">`
                        : `<div class="icon-placeholder">${g.name[0]}</div>`
                    }
                    <div class="info">
                        <div class="name">${g.name}</div>
                        <div class="members">${g.member_count || '?'} members</div>
                    </div>
                </div>
            `).join('') || '<div style="color:#666">No servers yet</div>';
        }
        async function fetchLogs() {
            const url = currentFilter
                ? `/api/logs?limit=50&event_type=${currentFilter}`
                : '/api/logs?limit=50';
            const res = await fetch(url);
            const data = await res.json();
            document.getElementById('logs').innerHTML = data.logs.map(l => `
                <div class="log-entry">
                    <div class="time">${formatTime(l.timestamp)}</div>
                    <div class="type ${l.event_type}">${l.event_type}</div>
                    <div class="content">
                        <div class="meta">
                            ${l.guild ? `<b>${l.guild}</b> #${l.channel} - ` : ''}
                            <strong>${l.user}</strong>
                        </div>
                        <div class="text">${l.content.replace(/</g, '&lt;')}</div>
                    </div>
                </div>
            `).join('') || '<div style="padding:20px;color:#666">No activity</div>';
        }
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t =>
                    t.classList.remove('active'));
                tab.classList.add('active');
                currentFilter = tab.dataset.filter;
                fetchLogs();
            });
        });
        fetchStats(); fetchGuilds(); fetchLogs();
        setInterval(() => { fetchStats(); fetchGuilds(); fetchLogs(); }, 3000);
    </script>
</body>
</html>
"""


@monitor_app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the monitoring dashboard."""
    return DASHBOARD_HTML


# ============== Main Entry Point ==============


async def run_bot():
    """Run the Discord bot."""
    bot = ClaraDiscordBot()
    await bot.start(BOT_TOKEN)


async def run_monitor_server():
    """Run the FastAPI monitoring server."""
    config = uvicorn.Config(
        monitor_app, host="0.0.0.0", port=MONITOR_PORT, log_level="warning"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def async_main():
    """Run both bot and monitoring server."""
    if not BOT_TOKEN:
        print(f"{C.RED}[error] DISCORD_BOT_TOKEN environment variable is required{C.RESET}")
        print(f"{C.GRAY}Get your token from: https://discord.com/developers/applications{C.RESET}")
        return

    print(f"\n{C.BOLD}{C.MAGENTA}╔══════════════════════════════════════╗{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}║      Clara Discord Bot Starting      ║{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}╚══════════════════════════════════════╝{C.RESET}\n")

    print(f"{C.CYAN}[config]{C.RESET} Max message chain: {C.WHITE}{MAX_MESSAGES}{C.RESET}")
    if ALLOWED_CHANNELS:
        print(f"{C.CYAN}[config]{C.RESET} Allowed channels ({len(ALLOWED_CHANNELS)}):")
        for ch in ALLOWED_CHANNELS:
            print(f"{C.GRAY}  - {ch}{C.RESET}")
    else:
        print(f"{C.CYAN}[config]{C.RESET} Allowed channels: {C.WHITE}ALL{C.RESET}")
    print(f"{C.CYAN}[config]{C.RESET} Allowed roles: {C.WHITE}{ALLOWED_ROLES or 'all'}{C.RESET}")

    # Tool calling status check
    from llm_backends import TOOL_FORMAT, TOOL_MODEL

    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    # Determine effective tool endpoint based on provider
    if os.getenv("TOOL_BASE_URL"):
        tool_base_url = os.getenv("TOOL_BASE_URL")
        tool_source = "explicit"
    elif provider == "openai":
        tool_base_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")
        tool_source = "main LLM"
    elif provider == "nanogpt":
        tool_base_url = "https://nano-gpt.com/api/v1"
        tool_source = "main LLM"
    else:
        tool_base_url = "https://openrouter.ai/api/v1"
        tool_source = "main LLM"

    print(f"{C.GREEN}[tools] ✓ Tool calling ENABLED{C.RESET}")
    print(f"{C.GRAY}[tools]   Model: {C.CYAN}{TOOL_MODEL}{C.RESET}")
    print(f"{C.GRAY}[tools]   Endpoint: {C.CYAN}{tool_base_url}{C.RESET} {C.GRAY}({tool_source}){C.RESET}")
    print(f"{C.GRAY}[tools]   Format: {C.CYAN}{TOOL_FORMAT}{C.RESET}")

    # E2B status check
    from e2b_tools import E2B_AVAILABLE, E2B_API_KEY

    if E2B_ENABLED and E2B_AVAILABLE and E2B_API_KEY:
        print(f"{C.GREEN}[e2b] ✓ Code execution ENABLED{C.RESET}")
    else:
        print(f"{C.RED}[e2b] ✗ Code execution DISABLED{C.RESET}")
        if not E2B_API_KEY:
            print(f"{C.GRAY}[e2b]   - E2B_API_KEY not set{C.RESET}")
        if not E2B_AVAILABLE:
            print(f"{C.GRAY}[e2b]   - e2b_code_interpreter package not installed{C.RESET}")
            print(f"{C.GRAY}[e2b]   - Run: poetry add e2b-code-interpreter{C.RESET}")

    if MONITOR_ENABLED:
        print(f"{C.CYAN}[monitor]{C.RESET} Dashboard at {C.BLUE}http://localhost:{MONITOR_PORT}{C.RESET}")
        await asyncio.gather(run_bot(), run_monitor_server())
    else:
        await run_bot()


def main():
    """Run the Discord bot with optional monitoring."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}[discord]{C.RESET} Shutting down...")


if __name__ == "__main__":
    main()
