"""
Discord bot for Clarissa - Multi-user AI assistant with memory.

Inspired by llmcord's clean design, but integrates directly with Clarissa's
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
import io
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

from db import SessionLocal
from db.models import ChannelSummary, Project, Session
from sandbox.docker import get_sandbox_manager
from storage.local_files import get_file_manager
from email_monitor import (
    handle_email_tool,
    email_check_loop,
)
from config.logging import init_logging, get_logger, set_db_session_factory

# Import modular tools system for GitHub, ADO, etc.
from tools import init_tools, get_registry, ToolContext

# Import from clarissa_core for unified platform
from clarissa_core import (
    init_platform,
    MemoryManager,
    make_llm,
    make_llm_with_tools,
    ModelTier,
    get_model_for_tier,
    # KIRA-inspired pipeline
    detect_intent,
    select_tier,
    get_tier_display,
)

# Initialize logging system
init_logging()
logger = get_logger("discord")
tools_logger = get_logger("tools")

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
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

# Docker sandbox configuration
DOCKER_ENABLED = True  # Docker sandbox is always available if Docker is running
MAX_TOOL_ITERATIONS = 75  # Max tool call rounds per response

# Auto-continue configuration
# When Clarissa ends with a permission-seeking question, auto-continue without waiting
AUTO_CONTINUE_ENABLED = os.getenv("DISCORD_AUTO_CONTINUE", "true").lower() == "true"
AUTO_CONTINUE_MAX = int(os.getenv("DISCORD_AUTO_CONTINUE_MAX", "3"))  # Max auto-continues per conversation

# KIRA-inspired auto tier selection
# Automatically select model tier based on task complexity
AUTO_TIER_ENABLED = os.getenv("AUTO_TIER_ENABLED", "true").lower() == "true"
AUTO_TIER_SHOW_SELECTION = os.getenv("AUTO_TIER_SHOW_SELECTION", "false").lower() == "true"  # Show auto-selected tier to user

# Patterns that trigger auto-continue (case-insensitive, checked at end of response)
AUTO_CONTINUE_PATTERNS = [
    "want me to do it?",
    "want me to proceed?",
    "want me to continue?",
    "want me to go ahead?",
    "want me to start?",
    "want me to try?",
    "want me to implement",
    "want me to fix",
    "want me to create",
    "want me to build",
    "want me to run",
    "shall i proceed?",
    "shall i continue?",
    "shall i go ahead?",
    "shall i do it?",
    "shall i start?",
    "should i proceed?",
    "should i continue?",
    "should i go ahead?",
    "should i do it?",
    "ready to proceed?",
    "ready when you are",
    "let me know if you want",
    "let me know when you're ready",
    "just say the word",
    "give me the go-ahead",
]

# Track whether modular tools have been initialized
_modular_tools_initialized = False


def _should_auto_continue(response: str) -> bool:
    """Check if response ends with a pattern that should trigger auto-continue."""
    if not AUTO_CONTINUE_ENABLED or not response:
        return False

    # Check the last 200 chars of the response (lowercased)
    response_end = response[-200:].lower().strip()

    for pattern in AUTO_CONTINUE_PATTERNS:
        if pattern in response_end:
            return True

    return False


def _get_current_time() -> str:
    """Get the current time formatted for Clarissa's context."""
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        # Format: "Thursday, December 26, 2024 at 6:28 PM EST"
        time_str = now.strftime("%A, %B %d, %Y at %-I:%M %p %Z")
        return time_str
    except Exception as e:
        logger.warning(f"Failed to get timezone {DEFAULT_TIMEZONE}: {e}")
        # Fallback to UTC
        now = datetime.now(UTC)
        return now.strftime("%A, %B %d, %Y at %H:%M UTC")


async def init_modular_tools() -> None:
    """Initialize the modular tools system (all tools including Docker, local files, GitHub, ADO, etc.)."""
    global _modular_tools_initialized
    if _modular_tools_initialized:
        return

    try:
        results = await init_tools(hot_reload=False)
        loaded = [name for name, success in results.items() if success]
        failed = [name for name, success in results.items() if not success]

        if loaded:
            tools_logger.info(f"Loaded tool modules: {', '.join(loaded)}")
        if failed:
            tools_logger.warning(f"Failed to load: {', '.join(failed)}")

        _modular_tools_initialized = True
    except Exception as e:
        tools_logger.error(f"Failed to initialize modular tools: {e}")


def get_all_tools(include_docker: bool = True) -> list[dict]:
    """Get all available tools from the modular registry.

    Args:
        include_docker: Whether to include Docker sandbox tools (for capability filtering)

    Returns:
        List of tool definitions in OpenAI format
    """
    if not _modular_tools_initialized:
        tools_logger.warning("Tools not initialized, returning empty list")
        return []

    registry = get_registry()
    capabilities = {"docker": include_docker}
    return registry.get_tools(platform="discord", capabilities=capabilities, format="openai")

# Discord message limit
DISCORD_MSG_LIMIT = 2000

# Monitor configuration
MONITOR_PORT = int(os.getenv("DISCORD_MONITOR_PORT", "8001"))
MONITOR_ENABLED = os.getenv("DISCORD_MONITOR_ENABLED", "true").lower() == "true"
MAX_LOG_ENTRIES = 100

# Model tier prefixes
TIER_PREFIXES = {
    "!high": "high",
    "!opus": "high",
    "!mid": "mid",
    "!sonnet": "mid",
    "!low": "low",
    "!haiku": "low",
    "!fast": "low",
}

# Tier display names and emojis
TIER_DISPLAY = {
    "high": ("🔴", "High (Opus-class)"),
    "mid": ("🟡", "Mid (Sonnet-class)"),
    "low": ("🟢", "Low (Haiku-class)"),
}


def detect_tier_from_message(content: str) -> tuple[ModelTier | None, str]:
    """Detect model tier from message prefix and return cleaned content.

    Supported prefixes:
        !high, !opus     -> high tier
        !mid, !sonnet    -> mid tier (default)
        !low, !haiku, !fast -> low tier

    Returns:
        (tier, cleaned_content): The detected tier (or None for default) and
        the message content with the prefix removed.
    """
    content_lower = content.lower().strip()
    for prefix, tier in TIER_PREFIXES.items():
        if content_lower.startswith(prefix):
            # Remove the prefix and any leading whitespace
            cleaned = content[len(prefix) :].lstrip()
            return tier, cleaned  # type: ignore
    return None, content


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


@dataclass
class QueuedTask:
    """A queued task waiting to be processed."""

    message: DiscordMessage
    is_dm: bool
    queued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    position: int = 0  # Position in queue when added


class TaskQueue:
    """Manages task queuing per channel to prevent concurrent tool usage."""

    def __init__(self):
        # Active tasks: channel_id -> message being processed
        self._active: dict[int, DiscordMessage] = {}
        # Queued tasks: channel_id -> list of queued tasks
        self._queues: dict[int, list[QueuedTask]] = {}
        self._lock = asyncio.Lock()

    async def try_acquire(
        self, message: DiscordMessage, is_dm: bool
    ) -> tuple[bool, int]:
        """Try to acquire the channel for processing.

        Returns:
            (acquired, queue_position): If acquired is True, proceed with task.
            If False, queue_position indicates position in queue (1-indexed).
        """
        channel_id = message.channel.id

        async with self._lock:
            if channel_id not in self._active:
                # No active task, acquire immediately
                self._active[channel_id] = message
                return True, 0

            # Channel is busy, add to queue
            if channel_id not in self._queues:
                self._queues[channel_id] = []

            queue = self._queues[channel_id]
            position = len(queue) + 1  # 1-indexed position
            task = QueuedTask(message=message, is_dm=is_dm, position=position)
            queue.append(task)

            logger.info(f"Queued task for channel {channel_id}, position {position}")
            return False, position

    async def release(self, channel_id: int) -> QueuedTask | None:
        """Release the channel and return the next queued task if any."""
        async with self._lock:
            if channel_id in self._active:
                del self._active[channel_id]

            # Check for queued tasks
            if channel_id in self._queues and self._queues[channel_id]:
                next_task = self._queues[channel_id].pop(0)
                self._active[channel_id] = next_task.message
                logger.info(
                    f"Dequeued task for channel {channel_id}, {len(self._queues[channel_id])} remaining"
                )
                return next_task

            return None

    async def get_queue_length(self, channel_id: int) -> int:
        """Get the number of queued tasks for a channel."""
        async with self._lock:
            if channel_id in self._queues:
                return len(self._queues[channel_id])
            return 0

    async def is_busy(self, channel_id: int) -> bool:
        """Check if a channel has an active task."""
        async with self._lock:
            return channel_id in self._active

    async def get_stats(self) -> dict:
        """Get queue statistics (async version)."""
        async with self._lock:
            return self._get_stats_sync()

    def _get_stats_sync(self) -> dict:
        """Get queue statistics (sync version, call within lock)."""
        total_queued = sum(len(q) for q in self._queues.values())
        return {
            "active_tasks": len(self._active),
            "total_queued": total_queued,
            "channels_busy": list(self._active.keys()),
        }

    def get_stats_unsafe(self) -> dict:
        """Get queue statistics without lock (for sync callers, may be slightly stale)."""
        return self._get_stats_sync()


# Global task queue instance
task_queue = TaskQueue()


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
        from clarissa_core import __version__

        uptime = None
        if self.start_time:
            uptime = (datetime.now(UTC) - self.start_time).total_seconds()

        # Get queue stats
        queue_stats = task_queue.get_stats_unsafe()

        return {
            "version": __version__,
            "bot_user": self.bot_user,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "uptime_seconds": uptime,
            "guild_count": len(self.guilds),
            "message_count": self.message_count,
            "dm_count": self.dm_count,
            "response_count": self.response_count,
            "error_count": self.error_count,
            "queue": queue_stats,
        }


# Global monitor instance
monitor = BotMonitor()


class ClarissaDiscordBot(discord.Client):
    """Discord bot that integrates Clarissa's memory-enhanced AI."""

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

        # Initialize Clarissa's unified platform (DB, LLM, MemoryManager, ToolRegistry)
        init_platform()
        self.mm = MemoryManager.get_instance()

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
        """Build Discord-specific system context.

        Organized for prompt caching: static content first, dynamic content last.
        """
        # === STATIC CONTENT (cacheable) ===
        static_parts = [
            """## Discord Guidelines
- Use Discord markdown (bold, italic, code blocks)
- Keep responses concise - Discord is conversational
- Use `create_file_attachment` for sharing files - NEVER paste large content
- Long responses are split automatically

## Memory System
You have persistent memory via mem0. Use memories naturally without announcing "checking memories."
"""
        ]

        # Add tool prompts (static)
        if _modular_tools_initialized:
            registry = get_registry()
            tool_prompts = registry.get_system_prompts(platform="discord")
            if tool_prompts:
                static_parts.append(tool_prompts)

        # === DYNAMIC CONTENT ===
        author = message.author
        display_name = author.display_name
        username = author.name
        user_id = author.id
        channel_name = getattr(message.channel, "name", "DM")
        guild_name = message.guild.name if message.guild else "Direct Message"
        current_time = _get_current_time()

        if is_dm:
            dynamic_context = f"""## Current Context
Time: {current_time}
Environment: Private DM with {display_name} (one-on-one)
User: {display_name} (@{username}, discord-{user_id})
Memories: {len(user_mems)} user, {len(proj_mems)} project"""
        else:
            dynamic_context = f"""## Current Context
Time: {current_time}
Environment: {guild_name} server, #{channel_name} (shared channel)
Speaker: {display_name} (@{username}, discord-{user_id})
Memories: {len(user_mems)} user, {len(proj_mems)} project

Note: Messages prefixed with [Username] are from other users. Address people by name."""

        # Combine: static first (cacheable), then dynamic
        return "\n\n".join(static_parts) + "\n\n" + dynamic_context

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
                        logger.debug(
                            f" Saved attachment to storage: {original_filename}"
                        )
                except Exception as e:
                    logger.debug(f" Failed to save attachment locally: {e}")

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
                logger.debug(f" Large file saved locally: {filename} ({size} bytes)")
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
                    content = (
                        content[:MAX_CHARS]
                        + "\n... [truncated, full file saved locally]"
                    )

                attachments.append(
                    {
                        "filename": attachment.filename,
                        "content": content,
                    }
                )
                logger.debug(f" Read attachment: {filename} ({len(content)} chars)")

            except Exception as e:
                logger.debug(f" Error reading attachment {filename}: {e}")
                attachments.append(
                    {
                        "filename": attachment.filename,
                        "error": str(e),
                    }
                )

        return attachments

    async def on_ready(self):
        """Called when bot is ready."""
        logger.info(f"Logged in as {self.user}")
        if CLIENT_ID:
            invite = f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}&permissions=274877991936&scope=bot"
            logger.info(f"Invite URL: {invite}")

        # Initialize modular tools system (GitHub, ADO, etc.)
        await init_modular_tools()

        # Update monitor
        monitor.bot_user = str(self.user)
        monitor.start_time = datetime.now(UTC)
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Logged in as {self.user}")
        # Start email monitoring background task
        self.loop.create_task(email_check_loop(self))
        logger.info("Email monitoring task started")

        # Start KIRA-inspired proactive checkers
        try:
            from checkers import get_scheduler
            scheduler = get_scheduler(bot=self, db_session_factory=SessionLocal)
            await scheduler.start()
            logger.info("Proactive checkers started")
        except Exception as e:
            logger.warning(f"Failed to start proactive checkers: {e}")

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
        logger.debug(f"Message from {message.author}: {message.content[:50]!r}")

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

            logger.debug(f"mentioned={is_mentioned}, reply_to_bot={is_reply_to_bot}")

            if not is_mentioned and not is_reply_to_bot:
                return

            # Check channel permissions (only for non-DM)
            if ALLOWED_CHANNELS:
                channel_id = str(message.channel.id)
                if channel_id not in ALLOWED_CHANNELS:
                    logger.debug(f"Channel {channel_id} not in allowed list")
                    return

            # Check role permissions (only for non-DM)
            if ALLOWED_ROLES and isinstance(message.author, discord.Member):
                user_roles = {str(r.id) for r in message.author.roles}
                if not user_roles.intersection(set(ALLOWED_ROLES)):
                    return
        else:
            logger.info(f"DM from {message.author}")

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

        # Try to acquire channel for processing (queue if busy)
        await self._process_with_queue(message, is_dm)

    async def _process_with_queue(self, message: DiscordMessage, is_dm: bool):
        """Process message with queue management."""
        channel_id = message.channel.id

        # Try to acquire the channel
        acquired, queue_position = await task_queue.try_acquire(message, is_dm)

        if not acquired:
            # Channel is busy, notify user their request is queued
            queue_msg = f"-# ⏳ I'm working on something else right now. Your request is queued (position {queue_position})."
            try:
                await message.reply(queue_msg, mention_author=False)
            except Exception as e:
                logger.warning(f"Failed to send queue notification: {e}")
            return  # The task will be processed when dequeued

        # We have the channel, process the message
        try:
            await self._handle_message(message, is_dm)
        finally:
            # Release channel and check for queued tasks
            await self._process_queued_tasks(channel_id)

    async def _process_queued_tasks(self, channel_id: int):
        """Process any queued tasks for the channel after releasing."""
        while True:
            next_task = await task_queue.release(channel_id)
            if not next_task:
                break

            # Notify user their queued request is starting
            wait_time = (datetime.now(UTC) - next_task.queued_at).total_seconds()
            start_msg = (
                f"-# ▶️ Starting your queued request (waited {wait_time:.0f}s)..."
            )
            try:
                await next_task.message.reply(start_msg, mention_author=False)
            except Exception as e:
                logger.warning(f"Failed to send start notification: {e}")

            # Process the queued task
            try:
                await self._handle_message(next_task.message, next_task.is_dm)
            except Exception as e:
                logger.exception(f"Error processing queued task: {e}")
                try:
                    err_msg = f"Sorry, I encountered an error processing your queued request: {str(e)[:100]}"
                    await next_task.message.reply(err_msg, mention_author=False)
                except Exception:
                    pass

    async def _handle_message(
        self,
        message: DiscordMessage,
        is_dm: bool = False,
        auto_continue_count: int = 0,
        auto_continue_content: str | None = None,
    ):
        """Process a message and generate a response.

        Args:
            message: The Discord message to respond to
            is_dm: Whether this is a DM (vs channel message)
            auto_continue_count: How many auto-continues have happened (to prevent loops)
            auto_continue_content: If set, use this as the user message instead of message.content
        """
        content_preview = (auto_continue_content or message.content)[:50]
        logger.info(f"Handling message from {message.author}: {content_preview!r}")

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
                    logger.debug(f" Channel: {n_recent} recent, {n_sum}ch summary")
                else:
                    # DMs: use reply chain, no channel summary
                    recent_channel_msgs = await self._build_message_chain(message)
                    channel_summary = ""
                    logger.debug(f" DM chain: {len(recent_channel_msgs)} msgs")

                # Get thread (shared for channels, per-user for DMs)
                thread, thread_owner = await self._ensure_thread(message, is_dm)
                logger.debug(f" Thread: {thread.id} (owner: {thread_owner})")

                # User ID for memories - always per-user, even in shared channels
                user_id = f"discord-{message.author.id}"
                project_id = await self._ensure_project(user_id)
                logger.debug(f" User: {user_id}, Project: {project_id}")

                # Get the user's message content (or use auto-continue content)
                if auto_continue_content:
                    raw_content = auto_continue_content
                    tier_override = None  # Don't change tier on auto-continue
                    auto_tier_selected = False
                else:
                    raw_content = self._clean_content(message.content)
                    # Detect tier override from message prefix (!high, !mid, !low, etc.)
                    tier_override, raw_content = detect_tier_from_message(raw_content)
                    auto_tier_selected = False

                    # KIRA-inspired: Auto tier selection when no manual tier specified
                    if tier_override is None and AUTO_TIER_ENABLED:
                        # Analyze intent for complexity
                        intent_context = {
                            "messages": recent_channel_msgs if not is_dm else [],
                            "is_dm": is_dm,
                            "has_attachments": bool(message.attachments),
                        }
                        intent_result = detect_intent(raw_content, intent_context)
                        logger.debug(f" Intent: {intent_result}")

                        # Select tier based on intent
                        tier_context = {
                            "message": raw_content,
                            "messages": recent_channel_msgs if not is_dm else [],
                        }
                        tier_override = select_tier(
                            intent=intent_result,
                            context=tier_context,
                        )
                        auto_tier_selected = True
                        logger.debug(f" Auto-selected tier: {tier_override}")

                        # Optionally show auto-selected tier to user
                        if AUTO_TIER_SHOW_SELECTION:
                            emoji, display = get_tier_display(tier_override)
                            await message.channel.send(
                                f"-# {emoji} Auto-selected {display} (complexity: {intent_result.complexity})",
                                silent=True,
                            )

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
                    logger.debug(f" Added {len(attachments)} file(s) to message")

                # For channels, prefix with username so Clarissa knows who's speaking
                if not is_dm:
                    display_name = message.author.display_name
                    user_content = f"[{display_name}]: {raw_content}"
                else:
                    user_content = raw_content

                logger.debug(f" Content length: {len(user_content)} chars")

                # Extract participants from conversation for cross-user memory
                participants = self._extract_participants(
                    recent_channel_msgs, message.author
                )
                if len(participants) > 1:
                    names = [p["name"] for p in participants]
                    logger.debug(f" Participants: {', '.join(names)}")

                # Fetch memories
                db = SessionLocal()
                try:
                    user_mems, proj_mems = self.mm.fetch_mem0_context(
                        user_id, project_id, user_content, participants=participants
                    )
                    recent_msgs = self.mm.get_recent_messages(db, thread.id)
                finally:
                    db.close()

                # Build prompt with Clarissa's persona
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
                # Insert as second system message (after Clarissa's persona)
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

                # Debug: check Docker sandbox status
                docker_available = (
                    DOCKER_ENABLED and get_sandbox_manager().is_available()
                )
                logger.debug(
                    f" Docker sandbox: enabled={DOCKER_ENABLED}, available={docker_available}"
                )

                # Generate streaming response (with optional tier override)
                response = await self._generate_response(
                    message, prompt_messages, tier_override
                )

                # Store in Clarissa's memory system
                # Use thread_owner for message storage, user_id for memories
                if response:
                    await self._store_exchange(
                        thread_owner,  # For message storage in shared thread
                        user_id,  # For per-user memory extraction
                        project_id,
                        thread.id,
                        user_content,
                        response,
                        participants=participants,
                    )

                    # Log response to monitor
                    guild_name = message.guild.name if message.guild else None
                    channel_name = getattr(message.channel, "name", "DM")
                    response_preview = (
                        response[:200] + "..." if len(response) > 200 else response
                    )
                    monitor.log(
                        "response", "Clarissa", response_preview, guild_name, channel_name
                    )

                    # Check for auto-continue (Clarissa asking permission to proceed)
                    if (
                        _should_auto_continue(response)
                        and auto_continue_count < AUTO_CONTINUE_MAX
                    ):
                        logger.info(
                            f"Auto-continuing ({auto_continue_count + 1}/{AUTO_CONTINUE_MAX})"
                        )
                        # Send a subtle indicator that we're auto-continuing
                        await message.channel.send(
                            "-# ▶️ Proceeding automatically...", silent=True
                        )
                        # Recursively handle with "yes, go ahead" as the user message
                        await self._handle_message(
                            message,
                            is_dm,
                            auto_continue_count=auto_continue_count + 1,
                            auto_continue_content="Yes, go ahead.",
                        )

            except Exception as e:
                logger.exception(f"Error handling message: {e}")

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

    def _extract_participants(
        self,
        messages: list[CachedMessage],
        current_author: discord.User | discord.Member | None = None,
    ) -> list[dict]:
        """Extract unique participants from a message chain.

        Args:
            messages: List of CachedMessage from the conversation
            current_author: The author of the current message (to ensure they're included)

        Returns:
            List of {"id": str, "name": str} for each participant (excludes bots)
        """
        seen_ids = set()
        participants = []

        # Add current author first if provided
        if current_author and not current_author.bot:
            author_id = str(current_author.id)
            if author_id not in seen_ids:
                seen_ids.add(author_id)
                participants.append(
                    {
                        "id": author_id,
                        "name": current_author.display_name,
                    }
                )

        # Extract from cached messages
        for msg in messages:
            if msg.is_bot or not msg.user_id:
                continue
            if msg.user_id not in seen_ids:
                seen_ids.add(msg.user_id)
                participants.append(
                    {
                        "id": msg.user_id,
                        "name": msg.username or msg.user_id,
                    }
                )

        return participants

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
                logger.debug(f" Updated channel summary for {channel_id}")

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
            role = "Clarissa" if msg.is_bot else msg.username
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
                logger.debug(f" Created thread: {thread_title}")

            return thread, thread_owner
        finally:
            db.close()

    async def _generate_response(
        self,
        message: DiscordMessage,
        prompt_messages: list[dict],
        tier: ModelTier | None = None,
    ) -> str:
        """Generate response and send to Discord, handling tool calls.

        Args:
            message: The Discord message to respond to
            prompt_messages: The conversation history and context
            tier: Optional model tier override (high/mid/low)
        """
        # Log tier info if specified
        if tier:
            emoji, display = TIER_DISPLAY.get(tier, ("", tier))
            logger.info(f"Generating response for {message.author} using {display}...")
        else:
            logger.info(f"Generating response for {message.author}...")
        user_id = f"discord-{message.author.id}"

        try:
            # Send tier indicator if tier was explicitly selected
            if tier:
                emoji, display = TIER_DISPLAY.get(tier, ("⚙️", tier))
                model_name = get_model_for_tier(tier)
                # Extract just the model name without provider prefix
                short_model = (
                    model_name.split("/")[-1] if "/" in model_name else model_name
                )
                await message.channel.send(
                    f"-# {emoji} Using {display} ({short_model})", silent=True
                )
            loop = asyncio.get_event_loop()
            full_response = ""

            # Determine if we should use tools
            # Local file tools are always available; Docker tools require Docker running
            sandbox_mgr = get_sandbox_manager()
            docker_available = DOCKER_ENABLED and sandbox_mgr.is_available()

            # Always use tools (local file tools are always available)
            # Build the active tool list dynamically (includes modular tools like GitHub, ADO)
            if docker_available:
                tools_logger.info("Using tool-calling mode (Docker + local files + modular)")
                active_tools = get_all_tools(include_docker=True)
            else:
                tools_logger.info("Using tool-calling mode (local files + modular only)")
                active_tools = get_all_tools(include_docker=False)

            # Generate with tools
            full_response, files_to_send = await self._generate_with_tools(
                message, prompt_messages, user_id, loop, active_tools, tier
            )

            logger.info(f"Got response: {len(full_response)} chars")

            if not full_response:
                logger.warning("Empty response from LLM")
                full_response = "I'm sorry, I didn't generate a response."

            # Extract any file attachments from the response text
            cleaned_response, inline_files = self._extract_file_attachments(
                full_response
            )
            discord_files = []

            # Create Discord files from inline <<<file:>>> syntax
            if inline_files:
                inline_discord_files = self._create_discord_files(inline_files)
                discord_files.extend(inline_discord_files)
                logger.debug(f" Extracted {len(inline_files)} inline file(s)")

            # Add files from send_local_file tool calls
            if files_to_send:
                for file_path in files_to_send:
                    if file_path.exists():
                        try:
                            # Read file content into memory to avoid timing/handle issues
                            content = file_path.read_bytes()
                            if content:
                                discord_files.append(
                                    discord.File(
                                        fp=io.BytesIO(content),
                                        filename=file_path.name
                                    )
                                )
                                logger.debug(f" Adding local file: {file_path.name} ({len(content)} bytes)")
                            else:
                                logger.warning(f" Local file is empty: {file_path.name}")
                        except Exception as e:
                            logger.error(f" Failed to read local file {file_path.name}: {e}")

            # Split the response into chunks and send each
            chunks = self._split_message(cleaned_response)
            logger.debug(f" Sending {len(chunks)} message(s)")

            try:
                response_msg = None
                for i, chunk in enumerate(chunks):
                    # Attach files to the first message only
                    chunk_files = discord_files if i == 0 else []

                    if i == 0:
                        # First message is a reply
                        if chunk_files:
                            n_files = len(chunk_files)
                            logger.debug(f" Sending reply with {n_files} file(s)")
                        response_msg = await message.reply(
                            chunk, mention_author=False, files=chunk_files
                        )
                    else:
                        # Subsequent messages are follow-ups in the channel
                        response_msg = await message.channel.send(chunk)

                logger.info("Sent reply to Discord")

                # Cache the bot's last response message
                if response_msg:
                    async with self.cache_lock:
                        self.msg_cache[response_msg.id] = CachedMessage(
                            content=full_response,
                            user_id=str(self.user.id) if self.user else "",
                            username="Clarissa",
                            is_bot=True,
                        )

            except Exception as e:
                logger.exception(f"Sending response: {e}")
                error_msg = f"I had trouble sending my response: {str(e)[:100]}"
                await message.reply(error_msg, mention_author=False)
                return ""

        except Exception as e:
            logger.exception(f"Generating response: {e}")
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
        tier: ModelTier | None = None,
    ) -> tuple[str, list]:
        """Generate response with tool calling support.

        Args:
            message: The Discord message to respond to
            prompt_messages: The conversation history and context
            user_id: The user ID for sandbox management
            loop: The event loop for running blocking calls
            active_tools: List of tool definitions to use
            tier: Optional model tier override (high/mid/low)

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
                "CRITICAL FILE ATTACHMENT RULES:\n"
                "To share files (HTML, JSON, code, etc.) use `create_file_attachment` tool.\n"
                "This is the MOST RELIABLE method - it saves AND attaches in one step.\n"
                "NEVER paste raw HTML, large JSON, or long code directly into chat.\n\n"
                "You have access to tools for code execution, file management, and developer integrations. "
                "When the user asks you to calculate, run code, analyze data, "
                "fetch URLs, install packages, or do anything computational - "
                "USE THE TOOLS. Do not just explain what you would do - actually "
                "call the execute_python or other tools to do it. "
                "For any math beyond basic arithmetic, USE execute_python. "
                "For GitHub tasks (repos, issues, PRs, workflows), use the github_* tools. "
                "For Azure DevOps tasks (work items, PRs, pipelines, repos), use the ado_* tools. "
                "Summarize results conversationally and attach full output as a file."
            ),
        }
        messages.insert(0, tool_instruction)

        # Tool execution tracking
        total_tools_run = 0

        # Tool status messages (Docker + local file + modular tools)
        tool_status = {
            # Docker sandbox tools
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
            "create_file_attachment": ("📎", "Creating file attachment"),
            # Chat history tools
            "search_chat_history": ("🔎", "Searching chat history"),
            "get_chat_history": ("📜", "Retrieving chat history"),
            # Email tools
            "check_email": ("📬", "Checking email"),
            "search_email": ("🔎", "Searching email"),
            "send_email": ("📤", "Sending email"),
            # GitHub tools
            "github_get_me": ("🐙", "Getting GitHub profile"),
            "github_search_repositories": ("🔍", "Searching GitHub repos"),
            "github_get_repository": ("📂", "Getting repo details"),
            "github_list_issues": ("📋", "Listing issues"),
            "github_get_issue": ("🔖", "Getting issue details"),
            "github_create_issue": ("➕", "Creating issue"),
            "github_list_pull_requests": ("🔀", "Listing pull requests"),
            "github_get_pull_request": ("📑", "Getting PR details"),
            "github_create_pull_request": ("🔀", "Creating pull request"),
            "github_list_commits": ("📝", "Listing commits"),
            "github_get_file_contents": ("📄", "Reading GitHub file"),
            "github_search_code": ("🔎", "Searching GitHub code"),
            "github_list_workflow_runs": ("⚙️", "Listing workflow runs"),
            "github_run_workflow": ("▶️", "Triggering workflow"),
            # Azure DevOps tools
            "ado_list_projects": ("🏢", "Listing ADO projects"),
            "ado_list_repos": ("📂", "Listing ADO repos"),
            "ado_list_pull_requests": ("🔀", "Listing ADO pull requests"),
            "ado_get_pull_request": ("📑", "Getting ADO PR details"),
            "ado_create_pull_request": ("🔀", "Creating ADO pull request"),
            "ado_list_work_items": ("📋", "Listing work items"),
            "ado_get_work_item": ("🔖", "Getting work item details"),
            "ado_create_work_item": ("➕", "Creating work item"),
            "ado_search_work_items": ("🔎", "Searching work items"),
            "ado_my_work_items": ("📋", "Getting my work items"),
            "ado_list_pipelines": ("⚙️", "Listing pipelines"),
            "ado_list_builds": ("🔨", "Listing builds"),
            "ado_run_pipeline": ("▶️", "Running pipeline"),
        }

        for iteration in range(MAX_TOOL_ITERATIONS):
            tools_logger.info(f"Iteration {iteration + 1}/{MAX_TOOL_ITERATIONS}")

            # Call LLM with tools
            def call_llm():
                llm = make_llm_with_tools(active_tools, tier=tier)
                return llm(messages)

            completion = await loop.run_in_executor(None, call_llm)
            response_message = completion.choices[0].message

            # Check if there are tool calls
            if not response_message.tool_calls:
                if iteration == 0:
                    # First iteration with no tools - fall back to main chat LLM
                    # This preserves the main LLM's personality for regular chat
                    logger.info("No tools needed, using main chat LLM")

                    # Remove the tool instruction we added
                    original_messages = [
                        m
                        for m in messages
                        if m.get("content") != tool_instruction["content"]
                    ]

                    def main_llm_call():
                        llm = make_llm(tier=tier)
                        return llm(original_messages)

                    result = await loop.run_in_executor(None, main_llm_call)
                    return result or "", files_to_send
                else:
                    # Tools were used in previous iterations, return tool model's response
                    return response_message.content or "", files_to_send

            # Process tool calls
            tool_count = len(response_message.tool_calls)
            tools_logger.info(f"Processing {tool_count} tool call(s)")

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
                    # Debug: log raw arguments
                    if raw_args:
                        tools_logger.debug(
                            f"Raw args type: {type(raw_args).__name__}, len: {len(raw_args)}"
                        )
                        preview = (
                            raw_args[:200] + "..." if len(raw_args) > 200 else raw_args
                        )
                        tools_logger.debug(f"Raw args preview: {preview}")
                    else:
                        tools_logger.warning("raw_args is empty/None")

                    arguments = json.loads(raw_args) if raw_args else {}
                except (json.JSONDecodeError, TypeError) as e:
                    tools_logger.error(f"JSON parse error: {e}")
                    tools_logger.error(f"Raw value: {repr(raw_args)[:500]}")
                    arguments = {}

                tools_logger.info(
                    f"Executing: {tool_name} with {len(arguments)} args: {list(arguments.keys())}"
                )

                # Get friendly status for this tool
                emoji, action = tool_status.get(tool_name, ("⚙️", "Working"))

                # Build status text with context
                if tool_name == "execute_python":
                    desc = arguments.get("description", "")
                    status_text = (
                        f"{emoji} {action}..." if not desc else f"{emoji} {desc}..."
                    )
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
                elif tool_name in (
                    "save_to_local",
                    "read_local_file",
                    "delete_local_file",
                    "send_local_file",
                ):
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
                    logger.debug(f" Failed to send status: {e}")

                # Execute the tool - handle both Docker sandbox and local file tools
                tool_output = await self._execute_tool(
                    tool_name,
                    arguments,
                    user_id,
                    sandbox_manager,
                    file_manager,
                    files_to_send,
                    message.channel,
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
                status = "success" if success else "failed"
                tools_logger.info(f"{tool_name} → {status}")

            # Show typing indicator while processing
            async with message.channel.typing():
                await asyncio.sleep(0.1)  # Brief pause

        # Max iterations reached - send status and ask LLM to summarize
        tools_logger.warning("Max iterations reached, requesting summary")

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
            from clarissa_core.llm import TOOL_FORMAT, _convert_messages_to_claude_format

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

        Handles Docker sandbox tools, local file tools, and chat history tools.
        """
        from pathlib import Path

        # Get channel_id for file storage organization
        channel_id = str(channel.id) if channel else None

        # Docker sandbox tools (including web_search which uses Tavily)
        docker_tools = {
            "execute_python",
            "install_package",
            "read_file",
            "write_file",
            "list_files",
            "run_shell",
            "unzip_file",
            "web_search",
            "run_claude_code",
        }

        # Email tools
        email_tools = {"check_email", "send_email"}

        if tool_name in docker_tools:
            # Use Docker sandbox manager
            result = await sandbox_manager.handle_tool_call(
                user_id, tool_name, arguments
            )
            if result.success:
                return result.output
            else:
                return f"Error: {result.error}"

        # Email tools
        elif tool_name in email_tools:
            return await handle_email_tool(tool_name, arguments)

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
                local_filename = (
                    sandbox_path.split("/")[-1] if "/" in sandbox_path else sandbox_path
                )

            # Read from sandbox
            read_result = await sandbox_manager.read_file(user_id, sandbox_path)
            if not read_result.success:
                return f"Error reading from sandbox: {read_result.error}"

            # Save locally (organized by user/channel)
            content = read_result.output
            save_result = file_manager.save_file(
                user_id, local_filename, content, channel_id
            )
            return save_result.message

        elif tool_name == "upload_to_sandbox":
            local_filename = arguments.get("local_filename", "")
            sandbox_path = arguments.get("sandbox_path", "")

            # Read from local storage as bytes (preserves binary files)
            content, error = file_manager.read_file_bytes(
                user_id, local_filename, channel_id
            )
            if content is None:
                return f"Error: {error}"

            # Determine sandbox path
            if not sandbox_path:
                sandbox_path = f"/home/user/{local_filename}"

            # Write to sandbox (bytes supported)
            write_result = await sandbox_manager.write_file(
                user_id, sandbox_path, content
            )
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
            # Try modular tools from registry (GitHub, ADO, etc.)
            if _modular_tools_initialized:
                registry = get_registry()
                if tool_name in registry:
                    # Build tool context for modular tools
                    ctx = ToolContext(
                        user_id=user_id,
                        channel_id=channel_id,
                        platform="discord",
                        extra={"channel": channel, "files_to_send": files_to_send},
                    )
                    try:
                        return await registry.execute(tool_name, arguments, ctx)
                    except Exception as e:
                        tools_logger.error(f"Modular tool {tool_name} failed: {e}")
                        return f"Error executing {tool_name}: {e}"

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
                    text = (
                        msg.content[:200] + "..."
                        if len(msg.content) > 200
                        else msg.content
                    )
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
                is_bot = " [Clarissa]" if msg.author == self.user else ""
                # Truncate long messages
                text = (
                    msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
                )
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

        Supports multiple formats:
        - <<<file:name>>>content<<</file>>>
        - <<<file:name>>>content<<<end>>> or <<<endfile>>>
        - Markdown code blocks with file hints

        Returns:
            tuple: (cleaned_text, list of (filename, content) tuples)
        """
        files = []
        cleaned = text

        # Primary pattern: <<<file:filename>>>content<<</file>>>
        # Also handles <<</file:filename>>> closing variant
        primary_pattern = r"<<<\s*file\s*:\s*([^>]+?)\s*>>>(.*?)<<<\s*/\s*file\s*(?::\s*[^>]*)?\s*>>>"

        def replace_file(match):
            filename = match.group(1).strip()
            content = match.group(2).strip()
            logger.debug(f" Matched file: {filename} ({len(content)} chars)")
            files.append((filename, content))
            return f"📎 *Attached: {filename}*"

        cleaned = re.sub(primary_pattern, replace_file, cleaned, flags=re.DOTALL | re.IGNORECASE)

        # Fallback pattern: <<<file:filename>>>content<<<end>>> or <<<endfile>>>
        fallback_pattern = r"<<<\s*file\s*:\s*([^>]+?)\s*>>>(.*?)<<<\s*(?:end|endfile)\s*>>>"
        cleaned = re.sub(fallback_pattern, replace_file, cleaned, flags=re.DOTALL | re.IGNORECASE)

        # Last resort: <<<file:filename>>> followed by content until next <<< or end of major section
        # This catches cases where Clarissa forgets the closing tag entirely
        if "<<<file:" in cleaned.lower() or "<<< file:" in cleaned.lower():
            unclosed_pattern = r"<<<\s*file\s*:\s*([^>]+?)\s*>>>(.*?)(?=<<<|\Z)"

            def replace_unclosed(match):
                filename = match.group(1).strip()
                content = match.group(2).strip()
                # Only extract if there's substantial content and it looks like a file
                if len(content) > 10 and not content.startswith("<<<"):
                    # Don't re-extract if we already got this file
                    if not any(f[0] == filename for f in files):
                        logger.debug(f" Matched unclosed file: {filename} ({len(content)} chars)")
                        files.append((filename, content))
                        return f"📎 *Attached: {filename}*"
                return match.group(0)

            cleaned = re.sub(unclosed_pattern, replace_unclosed, cleaned, flags=re.DOTALL | re.IGNORECASE)

        # Debug: check if we still have unmatched file tags
        remaining_tags = re.findall(r"<<<\s*file\s*:", cleaned, re.IGNORECASE)
        if remaining_tags:
            logger.warning(f"Found {len(remaining_tags)} unmatched <<<file: tag(s) after extraction")
            logger.debug(f"Text snippet: {cleaned[:500]}")

        return cleaned, files

    def _create_discord_files(
        self, files: list[tuple[str, str]]
    ) -> list[discord.File]:
        """Create discord.File objects from extracted file content.

        Uses BytesIO for in-memory file handling (no temp files needed).

        Returns:
            list of discord.File objects
        """
        discord_files = []

        for filename, content in files:
            if not content:
                logger.debug(f" Skipping empty file: {filename}")
                continue
            try:
                # Encode content to bytes and wrap in BytesIO
                content_bytes = content.encode("utf-8")
                discord_file = discord.File(
                    fp=io.BytesIO(content_bytes),
                    filename=filename
                )
                discord_files.append(discord_file)
                logger.debug(f" Created file: {filename} ({len(content_bytes)} bytes)")

            except Exception as e:
                logger.debug(f" Error creating file {filename}: {e}")

        return discord_files


    async def _store_exchange(
        self,
        thread_owner_id: str,
        memory_user_id: str,
        project_id: str,
        thread_id: str,
        user_message: str,
        assistant_reply: str,
        participants: list[dict] | None = None,
    ):
        """Store the exchange in Clarissa's memory system.

        Args:
            thread_owner_id: ID for message storage (channel or DM owner)
            memory_user_id: ID for mem0 memory extraction (always per-user)
            project_id: Project ID for memory organization
            thread_id: Thread ID for message storage
            user_message: The user's message
            assistant_reply: Clarissa's response
            participants: List of {"id": str, "name": str} for people in the conversation
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
                memory_user_id,
                project_id,
                recent_msgs,
                user_message,
                assistant_reply,
                participants=participants,
            )
            logger.debug(f" Stored exchange (thread: {thread_owner_id[:20]}...)")

        finally:
            db.close()


# ============== FastAPI Monitor Dashboard ==============

monitor_app = FastAPI(title="Clarissa Discord Monitor")

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


@monitor_app.get("/api/version")
def get_version():
    """Get platform version information."""
    from clarissa_core import __version__

    return {
        "version": __version__,
        "platform": "mypalclarissa",
        "component": "discord-bot",
    }


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
    <title>Clarissa Discord Monitor</title>
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
            Clarissa Discord Monitor
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
    bot = ClarissaDiscordBot()
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
    # Initialize database logging
    set_db_session_factory(SessionLocal)

    config_logger = get_logger("config")
    sandbox_logger = get_logger("sandbox")

    if not BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN environment variable is required")
        logger.info("Get your token from: https://discord.com/developers/applications")
        return

    logger.info("Clarissa Discord Bot Starting")

    config_logger.info(f"Max message chain: {MAX_MESSAGES}")
    if ALLOWED_CHANNELS:
        config_logger.info(
            f"Allowed channels ({len(ALLOWED_CHANNELS)}): {', '.join(ALLOWED_CHANNELS)}"
        )
    else:
        config_logger.info("Allowed channels: ALL")
    config_logger.info(f"Allowed roles: {ALLOWED_ROLES or 'all'}")

    # Tool calling status check
    from clarissa_core.llm import TOOL_FORMAT, TOOL_MODEL

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
    elif provider == "anthropic":
        tool_base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        tool_source = "main LLM"
    else:
        tool_base_url = "https://openrouter.ai/api/v1"
        tool_source = "main LLM"

    tools_logger.info("Tool calling ENABLED")
    tools_logger.info(f"Model: {TOOL_MODEL}")
    tools_logger.info(f"Endpoint: {tool_base_url} ({tool_source})")
    tools_logger.info(f"Format: {TOOL_FORMAT}")

    # Docker sandbox status check
    from sandbox.docker import DOCKER_AVAILABLE

    sandbox_mgr = get_sandbox_manager()
    if DOCKER_ENABLED and DOCKER_AVAILABLE and sandbox_mgr.is_available():
        sandbox_logger.info("Code execution ENABLED")
    else:
        sandbox_logger.warning("Code execution DISABLED")
        if not DOCKER_AVAILABLE:
            sandbox_logger.info(
                "  - docker package not installed (run: poetry add docker)"
            )
        elif not sandbox_mgr.is_available():
            sandbox_logger.info(
                "  - Docker daemon not running (start Docker Desktop or dockerd)"
            )

    if MONITOR_ENABLED:
        logger.info(f"Dashboard at http://localhost:{MONITOR_PORT}")
        await asyncio.gather(run_bot(), run_monitor_server())
    else:
        await run_bot()


def main():
    """Run the Discord bot with optional monitoring."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
