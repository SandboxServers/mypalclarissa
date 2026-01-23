"""
Discord bot for Clara - Multi-user AI assistant with memory.

Inspired by llmcord's clean design, but integrates directly with Clara's
MemoryManager for full mem0 memory support.

Usage:
    poetry run python discord_bot.py [options]

Options:
    -d, --daemon      Run as a background daemon (Unix only)
    --pidfile FILE    Write PID to FILE (default: /tmp/clara-discord.pid)
    --logfile FILE    Log output to FILE when daemonized (default: stdout)
    --stop            Stop a running daemon (uses pidfile)

Environment variables:
    DISCORD_BOT_TOKEN - Discord bot token (required)
    DISCORD_CLIENT_ID - Discord client ID (for invite link)
    DISCORD_MAX_MESSAGES - Max messages in conversation chain (default: 25)
    DISCORD_MAX_CHARS - Max chars per message content (default: 100000)
    DISCORD_ALLOWED_SERVERS - Comma-separated server IDs (supersedes channels)
    DISCORD_ALLOWED_CHANNELS - Comma-separated channel IDs (optional, empty = all)
    DISCORD_ALLOWED_ROLES - Comma-separated role IDs (optional, empty = all)
"""

from __future__ import annotations

import os

# Load .env BEFORE other imports that read env vars at module level
from dotenv import load_dotenv

load_dotenv()

import asyncio
import base64
import io
import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import discord
import uvicorn
from discord import Message as DiscordMessage
from discord.ext import commands as discord_commands
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# Import from clarissa_core for unified platform
from clarissa_core import (
    MemoryManager,
    ModelTier,
    anthropic_to_openai_response,
    generate_tool_description,
    get_model_for_tier,
    init_platform,
    make_llm,
    make_llm_with_tools,
    make_llm_with_tools_anthropic,
)
from clarissa_core.emotional_context import (
    finalize_conversation_emotional_context,
    get_conversation_sentiments,
    has_pending_emotional_context,
    track_message_sentiment,
)

# Import MCP plugin system
from clarissa_core.mcp import get_mcp_manager, init_mcp, shutdown_mcp

# Import Discord slash commands
from clarissa_core.discord import setup as setup_slash_commands
from clarissa_core.topic_recurrence import extract_and_store_topics
from config.logging import (
    get_discord_handler,
    get_logger,
    init_discord_logging,
    init_logging,
    set_db_session_factory,
)
from db import SessionLocal
from db.channel_config import (
    CLARISSA_ADMIN_ROLE,
    get_channel_mode,
    get_guild_channels,
    is_ors_enabled,
    set_channel_mode,
    should_respond_to_message,
)
from db.models import ChannelSummary, Project, Session
from email_monitor import (
    email_check_loop,
    handle_email_tool,
)
from email_service.monitor import (
    email_monitor_loop,
    is_email_monitoring_enabled,
)
from organic_response_system import (
    is_enabled as proactive_enabled,
)
from organic_response_system import (
    on_user_message as proactive_on_user_message,
)
from organic_response_system import (
    ors_main_loop as proactive_check_loop,
)
from sandbox.manager import get_sandbox_manager
from storage.local_files import get_file_manager

# Import modular tools system for GitHub, ADO, etc.
from tools import ToolContext, get_registry, init_tools
from tools._registry import validate_tool_args

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

# Log channel configuration - mirror console logs to this Discord channel
LOG_CHANNEL_ID = os.getenv("DISCORD_LOG_CHANNEL_ID", "")

# Stop phrase configuration - phrases that interrupt running tasks
# Default phrases that will stop Clara mid-task
STOP_PHRASES = [
    phrase.strip().lower()
    for phrase in os.getenv("DISCORD_STOP_PHRASES", "clara stop,stop clara,nevermind,never mind").split(",")
    if phrase.strip()
]

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

# Image extensions for vision support
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
}
# Max image size for vision (4MB default - base64 encoding adds ~33% overhead)
# Most API providers have request body limits of 5-20MB, so 4MB raw is safe
MAX_IMAGE_SIZE = int(os.getenv("DISCORD_MAX_IMAGE_SIZE", "4194304"))

ALLOWED_CHANNELS = [ch.strip() for ch in os.getenv("DISCORD_ALLOWED_CHANNELS", "").split(",") if ch.strip()]
ALLOWED_SERVERS = [s.strip() for s in os.getenv("DISCORD_ALLOWED_SERVERS", "").split(",") if s.strip()]
ALLOWED_ROLES = [r.strip() for r in os.getenv("DISCORD_ALLOWED_ROLES", "").split(",") if r.strip()]
DEFAULT_PROJECT = os.getenv("DEFAULT_PROJECT", "Default Project")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

# Docker sandbox configuration
DOCKER_ENABLED = True  # Docker sandbox is always available if Docker is running
MAX_TOOL_ITERATIONS = 75  # Max tool call rounds per response

# Dedicated thread pool for blocking I/O operations (LLM calls, mem0, etc.)
# Using more threads than default since these are I/O-bound, not CPU-bound
from concurrent.futures import ThreadPoolExecutor

BLOCKING_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("DISCORD_IO_THREADS", "20")),
    thread_name_prefix="clara-io-",
)

# Auto-continue configuration
# When Clara ends with a permission-seeking question, auto-continue without waiting
AUTO_CONTINUE_ENABLED = os.getenv("DISCORD_AUTO_CONTINUE", "true").lower() == "true"
AUTO_CONTINUE_MAX = int(os.getenv("DISCORD_AUTO_CONTINUE_MAX", "3"))  # Max auto-continues per conversation

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
_mcp_initialized = False


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
    """Get the current time formatted for Clara's context."""
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


def _format_discord_timestamp(dt: datetime) -> str:
    """Format a Discord message timestamp in the user's timezone.

    Returns format like "10:43 PM EST".
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        # Discord timestamps are always UTC-aware
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%-I:%M %p %Z")
    except Exception:
        return dt.strftime("%H:%M UTC")


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


async def init_mcp_plugins() -> None:
    """Initialize the MCP plugin system.

    Loads enabled MCP servers from the database. MCP tools are now served
    directly through the manager's format conversion methods rather than
    through the registry adapter. Also sets up official MCP servers based
    on configured environment variables.
    """
    global _mcp_initialized
    if _mcp_initialized:
        return

    try:
        manager = await init_mcp()

        # Setup official MCP servers based on environment
        try:
            from clarissa_core.core_tools import setup_official_mcp_servers

            official_results = await setup_official_mcp_servers()
            if official_results:
                success_count = sum(1 for v in official_results.values() if v)
                tools_logger.info(f"MCP: Official servers: {success_count}/{len(official_results)} installed")
        except ImportError:
            pass  # clarissa_core.tools not available
        except Exception as e:
            tools_logger.warning(f"MCP: Error setting up official servers: {e}")

        connected = len(manager)
        tool_count = len(manager.get_tools_openai_format())

        if connected > 0:
            tools_logger.info(f"MCP: {connected} servers connected, {tool_count} tools available")
        else:
            tools_logger.info("MCP: No servers configured (use mcp_install to add)")

        _mcp_initialized = True
    except Exception as e:
        tools_logger.error(f"Failed to initialize MCP plugins: {e}")


def get_all_tools(include_docker: bool = True) -> list[dict]:
    """Get all available tools from the modular registry and MCP servers.

    Args:
        include_docker: Whether to include Docker sandbox tools (for capability filtering)

    Returns:
        List of tool definitions in OpenAI format
    """
    if not _modular_tools_initialized:
        tools_logger.warning("Tools not initialized, returning empty list")
        return []

    registry = get_registry()

    # Build capabilities dict based on what's configured
    capabilities = {
        "docker": include_docker,
        "files": True,  # Local files always available (has default path)
        "discord": True,  # Discord-specific tools available in Discord bot
    }

    # Google OAuth - check if credentials are configured
    if os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
        capabilities["google_oauth"] = True

    # Email - check if credentials are configured
    if os.getenv("CLARISSA_EMAIL_ADDRESS") and os.getenv("CLARISSA_EMAIL_PASSWORD"):
        capabilities["email"] = True

    # Get native tools from registry
    native_tools = registry.get_tools(platform="discord", capabilities=capabilities, format="openai")

    # Get MCP tools directly from manager (bypassing registry adapter)
    mcp_tools = []
    if _mcp_initialized:
        try:
            manager = get_mcp_manager()
            mcp_tools = manager.get_tools_openai_format()
        except Exception as e:
            tools_logger.warning(f"Failed to get MCP tools: {e}")

    # Combine tools with deduplication check
    all_tools = native_tools + mcp_tools

    # Check for duplicates and warn
    seen_names = set()
    unique_tools = []
    for tool in all_tools:
        name = tool.get("function", {}).get("name", "")
        if name in seen_names:
            tools_logger.warning(f"Duplicate tool detected: {name} (skipping duplicate)")
            continue
        seen_names.add(name)
        unique_tools.append(tool)

    if len(unique_tools) != len(all_tools):
        tools_logger.warning(f"Tool deduplication: {len(all_tools)} total -> {len(unique_tools)} unique")

    return unique_tools


# Discord message limit
DISCORD_MSG_LIMIT = 2000

# Monitor configuration
# Railway sets PORT env var - use it if available, otherwise fall back to DISCORD_MONITOR_PORT
MONITOR_PORT = int(os.getenv("PORT", os.getenv("DISCORD_MONITOR_PORT", "8001")))
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

# Auto tier selection - use fast model to determine complexity
AUTO_TIER_ENABLED = os.getenv("AUTO_TIER_SELECTION", "false").lower() == "true"

# Classification prompt for auto tier selection (by Clara)
# Note: This prompt considers conversation context when available
TIER_CLASSIFICATION_PROMPT = """You are a routing assistant. Analyze the current message IN CONTEXT of the conversation and decide which model tier should handle it.

## Tiers
- LOW (Haiku) - Fast, cheap, minimal
- MID (Sonnet) - Capable, warm, default
- HIGH (Opus) - Deep, present, human

## Route to LOW when:
- Simple acknowledgments ("thanks", "got it", "lol") to SIMPLE tasks
- Single-word answers to LOW-tier questions
- Messages where overthinking would be weird

## Route to MID when (DEFAULT):
- General conversation
- Problem-solving, code, planning, debugging
- Creative work with clear constraints
- Multi-step reasoning
- Questions with concrete answers
- Helpful assistance that requires thought but not depth
- Continuations of MID-tier conversations
- Short answers that are part of a larger MID/HIGH discussion

## Route to HIGH when:
- Vulnerability or emotional weight in the message
- Self-reflection, identity, meaning-making
- Open-ended "why" questions about life/self/purpose
- Creative work where voice and soul matter more than structure
- The person seems to need presence, not just answers
- Nuanced emotional support (not just "I'm stressed" but deeper)
- Anything where *being seen* matters

## CRITICAL: Context Matters!
- A short reply like "yes", "ok", or "sounds good" should MATCH the tier of the ongoing conversation
- If assistant just asked a complex question, a simple answer still needs MID/HIGH to process it properly
- Look at what the conversation is about, not just the current message length
- One-word answers to important questions still need capable handling

## Key Signals for HIGH:
- First-person emotional language ("I feel", "I don't know why I...")
- Existential or philosophical questions
- Uncertainty about self, not just facts
- Requests for genuine opinion/perspective
- Tone that suggests heaviness or searching

## The Core Question
When in doubt between MID and HIGH, ask: "Does this person need to be *seen* right now, or do they need something *done*?"
- Seen → HIGH
- Done → MID
{context}
Current message: {message}

Respond with only one word: LOW, MID, or HIGH"""


async def classify_message_complexity(
    content: str,
    recent_messages: list[dict] | None = None,
) -> str:
    """Use fast model to classify message complexity for auto tier selection.

    Args:
        content: The current user message to classify
        recent_messages: Optional list of recent conversation messages for context.
                        Each should have 'role' and 'content' keys.

    Returns: "low", "mid", or "high"
    """
    import asyncio

    try:
        # Use fast/low tier model for classification
        llm = make_llm(tier="low")

        # Build context from recent messages (last few exchanges)
        context_str = ""
        if recent_messages:
            # Take up to 4 recent messages (2 exchanges) for context
            context_msgs = recent_messages[-4:]
            if context_msgs:
                context_lines = []
                for msg in context_msgs:
                    role = msg.get("role", "unknown")
                    msg_content = msg.get("content", "")
                    # Truncate long messages in context
                    if len(msg_content) > 200:
                        msg_content = msg_content[:200] + "..."
                    context_lines.append(f"{role.upper()}: {msg_content}")
                context_str = "\n\n## Recent conversation:\n" + "\n".join(context_lines)

        messages = [
            {
                "role": "user",
                "content": TIER_CLASSIFICATION_PROMPT.format(
                    message=content[:500],
                    context=context_str,
                ),
            }
        ]

        # Run sync LLM call in dedicated thread pool
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(BLOCKING_IO_EXECUTOR, llm, messages)

        # Parse response - expect LOW, MID, or HIGH
        result = response.strip().upper()
        if "HIGH" in result:
            return "high"
        elif "LOW" in result:
            return "low"
        else:
            return "mid"  # Default to mid if unclear

    except Exception as e:
        logger.warning(f"Auto tier classification failed: {e}, defaulting to mid")
        return "mid"


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


def is_stop_phrase(content: str) -> bool:
    """Check if the message content matches a stop phrase.

    Stop phrases interrupt running tasks. They are checked case-insensitively.
    """
    if not STOP_PHRASES:
        return False
    content_lower = content.lower().strip()
    # Remove bot mentions for comparison
    content_lower = re.sub(r"<@!?\d+>", "", content_lower).strip()
    return content_lower in STOP_PHRASES


def has_clara_admin_permission(member: discord.Member) -> bool:
    """Check if a member has Clara-Admin permissions.

    Returns True if member:
    - Has a role matching CLARISSA_ADMIN_ROLE name
    - Has administrator permission
    - Has manage_channels permission
    """
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_channels:
        return True
    for role in member.roles:
        if role.name == CLARISSA_ADMIN_ROLE:
            return True
    return False


async def handle_channel_command(message: DiscordMessage) -> bool:
    """Handle /clara channel commands.

    Returns True if a command was handled, False otherwise.
    """
    # Remove bot mention and clean content
    content = re.sub(r"<@!?\d+>", "", message.content).strip().lower()

    # Check for channel commands
    if not content.startswith("channel "):
        return False

    # Must be in a guild
    if not message.guild:
        await message.reply("Channel commands only work in servers, not DMs.")
        return True

    # Check permission
    if not isinstance(message.author, discord.Member):
        return False

    if not has_clara_admin_permission(message.author):
        await message.reply(
            f"You need the **{CLARISSA_ADMIN_ROLE}** role or admin permissions to configure channels.",
            mention_author=False,
        )
        return True

    parts = content.split()
    if len(parts) < 2:
        await message.reply(
            "Usage: `@Clara channel set [active|mention|off]` or `@Clara channel list`",
            mention_author=False,
        )
        return True

    subcommand = parts[1]

    if subcommand == "set" and len(parts) >= 3:
        mode = parts[2]
        if mode not in ("active", "mention", "off"):
            await message.reply(
                "Invalid mode. Use: `active`, `mention`, or `off`",
                mention_author=False,
            )
            return True

        set_channel_mode(
            channel_id=str(message.channel.id),
            guild_id=str(message.guild.id),
            mode=mode,
            configured_by=str(message.author.id),
        )

        mode_descriptions = {
            "active": "I'll participate actively and may chime in organically.",
            "mention": "I'll only respond when mentioned directly.",
            "off": "I'll ignore this channel entirely.",
        }
        await message.reply(
            f"Channel mode set to **{mode}**. {mode_descriptions[mode]}",
            mention_author=False,
        )
        return True

    elif subcommand == "list":
        configs = get_guild_channels(str(message.guild.id))
        if not configs:
            await message.reply(
                "No channels have been configured yet. Default mode is `mention`.",
                mention_author=False,
            )
            return True

        lines = ["**Channel Configurations:**"]
        for cfg in configs:
            lines.append(f"• <#{cfg.channel_id}>: `{cfg.mode}`")
        await message.reply("\n".join(lines), mention_author=False)
        return True

    elif subcommand == "status":
        mode = get_channel_mode(str(message.channel.id))
        await message.reply(
            f"This channel is set to **{mode}** mode.",
            mention_author=False,
        )
        return True

    else:
        await message.reply(
            "Usage:\n"
            "• `@Clara channel set [active|mention|off]` - Configure this channel\n"
            "• `@Clara channel list` - List all configured channels\n"
            "• `@Clara channel status` - Show this channel's mode",
            mention_author=False,
        )
        return True


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
    is_mention: bool = False  # Was Clara explicitly mentioned?
    queued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    position: int = 0  # Position in queue when added

    @property
    def is_batchable(self) -> bool:
        """Check if this task can be batched with others in active mode.

        Batchable tasks are: non-DM, non-mention (active mode) messages.
        """
        return not self.is_dm and not self.is_mention


class TaskQueue:
    """Manages task queuing per channel to prevent concurrent tool usage."""

    def __init__(self):
        # Active tasks: channel_id -> message being processed
        self._active: dict[int, DiscordMessage] = {}
        # Running asyncio tasks: channel_id -> asyncio.Task (for cancellation)
        self._running_tasks: dict[int, asyncio.Task] = {}
        # Queued tasks: channel_id -> list of queued tasks
        self._queues: dict[int, list[QueuedTask]] = {}
        self._lock = asyncio.Lock()

    async def try_acquire(
        self, message: DiscordMessage, is_dm: bool, is_mention: bool = False
    ) -> tuple[bool, int]:
        """Try to acquire the channel for processing.

        Args:
            message: The Discord message
            is_dm: Whether this is a DM
            is_mention: Whether Clara was explicitly mentioned

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
            task = QueuedTask(message=message, is_dm=is_dm, is_mention=is_mention, position=position)
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
                logger.info(f"Dequeued task for channel {channel_id}, {len(self._queues[channel_id])} remaining")
                return next_task

            return None

    async def release_batch(self, channel_id: int) -> list[QueuedTask]:
        """Release the channel and return all consecutive batchable tasks.

        For active mode, this allows responding to multiple queued messages at once.
        Returns tasks up until a non-batchable task (DM or mention) is encountered.

        Returns:
            List of batchable tasks (may be empty if next task is not batchable).
            If first task is not batchable, returns empty list (use release() instead).
        """
        async with self._lock:
            if channel_id in self._active:
                del self._active[channel_id]

            # Check for queued tasks
            if channel_id not in self._queues or not self._queues[channel_id]:
                return []

            queue = self._queues[channel_id]

            # Check if the first task is batchable
            if not queue[0].is_batchable:
                return []  # Use regular release() for non-batchable tasks

            # Collect consecutive batchable tasks
            batch = []
            while queue and queue[0].is_batchable:
                batch.append(queue.pop(0))

            if batch:
                # Mark the last message in the batch as active
                self._active[channel_id] = batch[-1].message
                logger.info(
                    f"Batch dequeued {len(batch)} task(s) for channel {channel_id}, "
                    f"{len(queue)} remaining"
                )

            return batch

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

    def register_task(self, channel_id: int, task: asyncio.Task):
        """Register the running asyncio task for a channel (for cancellation support)."""
        self._running_tasks[channel_id] = task

    def unregister_task(self, channel_id: int):
        """Unregister the running task for a channel."""
        self._running_tasks.pop(channel_id, None)

    async def cancel_and_clear(self, channel_id: int) -> bool:
        """Cancel the running task and clear the queue for a channel.

        Returns True if a task was cancelled, False if nothing was running.
        """
        async with self._lock:
            cancelled = False

            # Cancel the running asyncio task if any
            if channel_id in self._running_tasks:
                task = self._running_tasks[channel_id]
                if not task.done():
                    task.cancel()
                    cancelled = True
                    logger.info(f"Cancelled running task for channel {channel_id}")
                del self._running_tasks[channel_id]

            # Clear the active message
            if channel_id in self._active:
                del self._active[channel_id]

            # Clear the queue
            if channel_id in self._queues:
                queue_len = len(self._queues[channel_id])
                if queue_len > 0:
                    logger.info(f"Cleared {queue_len} queued task(s) for channel {channel_id}")
                del self._queues[channel_id]

            return cancelled


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


class ClaraDiscordBot(discord_commands.Bot):
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

        # Deduplication: track processed message IDs to prevent double processing
        # Maps message_id -> timestamp for TTL cleanup
        self._processed_messages: dict[int, float] = {}
        self._processed_messages_ttl = 300  # 5 minutes

        # Track startup state for log messages
        self._first_ready = True

        # Initialize Clara's unified platform (DB, LLM, MemoryManager, ToolRegistry)
        init_platform()
        self.mm = MemoryManager.get_instance()

    def _sync_llm(self, messages: list[dict]) -> str:
        """Synchronous LLM call for MemoryManager."""
        llm = make_llm()
        return llm(messages)

    def _format_time_gap(self, last_time: datetime | None) -> str | None:
        """Format time gap since last message in human-readable form.

        Returns None if gap is < 1 minute (not worth mentioning).
        """
        if last_time is None:
            return None

        now = datetime.now(UTC).replace(tzinfo=None)
        # Handle timezone-aware datetimes
        if last_time.tzinfo is not None:
            last_time = last_time.replace(tzinfo=None)

        delta = now - last_time
        total_seconds = delta.total_seconds()

        if total_seconds < 60:  # < 1 min, not worth mentioning
            return None
        elif total_seconds < 3600:  # < 1 hour
            minutes = int(total_seconds // 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif total_seconds < 86400:  # < 1 day
            hours = int(total_seconds // 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = int(total_seconds // 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"

    async def _maybe_finalize_previous_conversation(
        self,
        user_id: str,
        channel_id: str,
        channel_name: str,
        is_dm: bool,
        recent_msgs: list,
    ) -> None:
        """
        Check if there's a gap since last message and finalize emotional context.

        If >30 min since last message and we have pending sentiment data,
        retroactively finalize the previous conversation's emotional context.
        This works regardless of whether ORS is enabled.
        """
        if not recent_msgs:
            return

        # Check if we have pending emotional context to finalize
        if not has_pending_emotional_context(user_id, channel_id):
            return

        # Get last message time
        last_msg = recent_msgs[-1] if recent_msgs else None
        if not last_msg or not hasattr(last_msg, "created_at"):
            return

        last_time = last_msg.created_at
        if last_time is None:
            return

        # Handle timezone
        now = datetime.now(UTC)
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=UTC)

        gap = now - last_time
        gap_minutes = gap.total_seconds() / 60

        # If gap > 30 minutes, finalize previous conversation
        if gap_minutes > 30:
            logger.debug(f" Finalizing emotional context (gap: {gap_minutes:.0f} min)")

            # Generate a simple summary from last few messages
            summary = "general conversation"
            energy = "neutral"

            # Try to extract energy from last assistant message
            assistant_msgs = [m for m in recent_msgs if m.role == "assistant"]
            if assistant_msgs:
                # Use a simple heuristic based on content length/tone
                last_response = assistant_msgs[-1].content
                if any(word in last_response.lower() for word in ["sorry", "unfortunately", "issue", "problem"]):
                    energy = "concerned"
                elif any(word in last_response.lower() for word in ["great", "awesome", "happy", "glad"]):
                    energy = "positive"
                else:
                    energy = "neutral"

            # Get conversation sentiment before clearing
            sentiments = get_conversation_sentiments(user_id, channel_id)
            avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

            # Finalize emotional context and store to mem0
            finalize_conversation_emotional_context(
                user_id=user_id,
                channel_id=channel_id,
                channel_name=channel_name,
                is_dm=is_dm,
                energy=energy,
                summary=summary,
            )

            # Extract and store topics from the conversation
            # Build conversation text from recent messages
            conversation_lines = []
            for msg in recent_msgs[-20:]:  # Last 20 messages
                role = "User" if msg.role == "user" else "Clara"
                conversation_lines.append(f"{role}: {msg.content}")
            conversation_text = "\n".join(conversation_lines)

            if conversation_text and len(conversation_text) >= 50:
                # Create async LLM wrapper
                loop = asyncio.get_event_loop()

                async def async_llm_call(messages: list[dict]) -> str:
                    return await loop.run_in_executor(
                        BLOCKING_IO_EXECUTOR,
                        lambda: self.mm.llm(messages),
                    )

                try:
                    await extract_and_store_topics(
                        user_id=user_id,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        is_dm=is_dm,
                        conversation_text=conversation_text,
                        conversation_sentiment=avg_sentiment,
                        llm_call=async_llm_call,
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract topics: {e}")

    def _extract_departure_context(self, last_user_message: str | None) -> str | None:
        """Extract what user said they were going to do from their last message.

        Looks for patterns like:
        - "going to [verb]"
        - "brb [doing something]"
        - "heading out to [activity]"
        - "gotta [do something]"

        Returns a brief context or None if no departure detected.
        """
        if not last_user_message:
            return None

        import re

        msg = last_user_message.lower().strip()

        # Common departure patterns
        patterns = [
            r"(?:going to|gonna|gotta|about to|heading to|off to)\s+(.+?)(?:\.|!|$)",
            r"brb\s*[,:]?\s*(.+?)(?:\.|!|$)",
            r"(?:be right back|be back)\s*[,:]?\s*(.+?)(?:\.|!|$)",
            r"(?:stepping away|stepping out)\s*(?:to|for)?\s*(.+?)(?:\.|!|$)",
            r"(?:need to|have to|gotta)\s+(.+?)(?:\.|!|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                activity = match.group(1).strip()
                # Clean up and limit length
                if len(activity) > 100:
                    activity = activity[:100] + "..."
                if activity:
                    return activity

        return None

    def _build_discord_context(
        self,
        message: DiscordMessage,
        user_mems: list[str],
        proj_mems: list[str],
        is_dm: bool = False,
        recent_msgs: list | None = None,
    ) -> str:
        """Build Discord-specific system context.

        Organized for prompt caching: static content first, dynamic content last.

        Args:
            message: Current Discord message
            user_mems: User memories from mem0
            proj_mems: Project memories from mem0
            is_dm: Whether this is a DM conversation
            recent_msgs: Recent messages from the session (for time gap tracking)
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

        # Add tool prompts from native modules (static)
        # Only include essential modules - everything else will become MCP servers
        # MCP tools are self-describing via the tools parameter, no system prompt needed
        if _modular_tools_initialized:
            registry = get_registry()
            tool_prompts = registry.get_system_prompts(
                platform="discord",
                allowed_modules=["mcp_management", "ors_notes"],
            )
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

        # Format when this message was sent
        msg_sent_time = _format_discord_timestamp(message.created_at)

        # Calculate time context from recent messages
        time_context_lines = []
        if recent_msgs:
            # Find last assistant message (Clara's last response)
            assistant_msgs = [m for m in recent_msgs if m.role == "assistant"]
            if assistant_msgs:
                last_clara_msg = assistant_msgs[-1]
                clara_time_gap = self._format_time_gap(last_clara_msg.created_at)
                if clara_time_gap:
                    time_context_lines.append(f"Your last response: {clara_time_gap}")

            # Find last user message for departure context
            user_msgs = [m for m in recent_msgs if m.role == "user"]
            if user_msgs:
                last_user_msg = user_msgs[-1]
                # Check if user mentioned what they were doing
                departure_ctx = self._extract_departure_context(last_user_msg.content)
                if departure_ctx:
                    time_context_lines.append(f"User was: {departure_ctx}")

        time_context = "\n" + "\n".join(time_context_lines) if time_context_lines else ""

        if is_dm:
            dynamic_context = f"""## Current Context
Time: {current_time}
Message sent: {msg_sent_time}{time_context}
Environment: Private DM with {display_name} (one-on-one)
User: {display_name} (@{username}, discord-{user_id})
Memories: {len(user_mems)} user, {len(proj_mems)} project"""
        else:
            dynamic_context = f"""## Current Context
Time: {current_time}
Message sent: {msg_sent_time}{time_context}
Environment: {guild_name} server, #{channel_name} (shared channel)
Speaker: {display_name} (@{username}, discord-{user_id})
Memories: {len(user_mems)} user, {len(proj_mems)} project

Note: Messages prefixed with [Username] are from other users. Address people by name."""

        # Combine: static first (cacheable), then dynamic
        return "\n\n".join(static_parts) + "\n\n" + dynamic_context

    async def _extract_attachments(self, message: DiscordMessage, user_id: str | None = None) -> list[dict]:
        """Extract content from message attachments (text and images).

        Also saves all attachments to local storage if user_id is provided.

        Returns list of attachment dicts with:
        - For text files: {"filename": str, "content": str}
        - For images: {"filename": str, "type": "image", "media_type": str, "base64_data": str}
        - For other files: {"filename": str, "saved_locally": True, "note": str}
        - On errors: {"filename": str, "error": str}
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
            content_bytes = None
            if file_manager and user_id:
                try:
                    content_bytes = await attachment.read()
                    save_result = file_manager.save_from_bytes(user_id, original_filename, content_bytes, channel_id)
                    if save_result.success:
                        logger.debug(f" Saved attachment to storage: {original_filename}")
                except Exception as e:
                    logger.debug(f" Failed to save attachment locally: {e}")

            # Handle images for vision support
            if ext in IMAGE_EXTENSIONS:
                if attachment.size > MAX_IMAGE_SIZE:
                    logger.debug(f" Image too large for vision: {filename} ({attachment.size} bytes)")
                    attachments.append(
                        {
                            "filename": original_filename,
                            "saved_locally": True,
                            "note": f"Image too large for vision ({attachment.size // 1024 // 1024}MB). Saved locally.",
                        }
                    )
                    continue

                try:
                    # Download image if not already cached
                    if content_bytes is None:
                        content_bytes = await attachment.read()

                    # Determine media type from extension
                    media_type_map = {
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".gif": "image/gif",
                        ".webp": "image/webp",
                    }
                    media_type = media_type_map.get(ext, "image/png")

                    # Encode as base64
                    base64_data = base64.b64encode(content_bytes).decode("ascii")

                    attachments.append(
                        {
                            "filename": original_filename,
                            "type": "image",
                            "media_type": media_type,
                            "base64_data": base64_data,
                        }
                    )
                    logger.debug(f" Processed image for vision: {filename} ({len(content_bytes)} bytes)")

                except Exception as e:
                    logger.debug(f" Error processing image {filename}: {e}")
                    attachments.append(
                        {
                            "filename": original_filename,
                            "saved_locally": True,
                            "note": f"Failed to process image: {e}",
                        }
                    )
                continue

            if ext not in TEXT_EXTENSIONS:
                # Note: file was still saved locally above
                attachments.append(
                    {
                        "filename": original_filename,
                        "saved_locally": True,
                        "note": "Binary file saved locally. Use `read_local_file` or `send_local_file` to access.",
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
                if content_bytes is None:
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

        # Initialize Discord log channel mirroring (if configured)
        if LOG_CHANNEL_ID:
            try:
                channel_id = int(LOG_CHANNEL_ID)
                discord_handler = init_discord_logging(self, channel_id, self.loop)
                if discord_handler:
                    if self._first_ready:
                        await discord_handler.send_direct(f"🟢 Bot started - Logged in as {self.user}")
                    else:
                        await discord_handler.send_direct(f"🔄 Bot reconnected - {self.user}")
                    logger.info(f"Discord log mirroring enabled to channel {channel_id}")
            except ValueError:
                logger.warning(f"Invalid DISCORD_LOG_CHANNEL_ID: {LOG_CHANNEL_ID}")

        self._first_ready = False

        # Initialize modular tools system (GitHub, ADO, etc.)
        await init_modular_tools()

        # Initialize MCP plugin system
        await init_mcp_plugins()

        # Sync slash commands with Discord
        try:
            await self.sync_commands()
            cmd_count = len(self.pending_application_commands or [])
            logger.info(f"Synced {cmd_count} slash commands")
        except Exception as e:
            logger.warning(f"Failed to sync slash commands: {e}")

        # Update monitor
        monitor.bot_user = str(self.user)
        monitor.start_time = datetime.now(UTC)
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Logged in as {self.user}")
        # Start email monitoring background task (Clara's personal email)
        self.loop.create_task(email_check_loop(self))
        logger.info("Email monitoring task started")

        # Start user email monitoring service (if enabled)
        if is_email_monitoring_enabled():
            self.loop.create_task(email_monitor_loop(self))
            logger.info("User email monitoring service started")

        # Start proactive conversation engine (if enabled)
        if proactive_enabled():
            sync_llm = make_llm(tier="mid")  # Use mid tier for proactive decisions

            # Wrap sync LLM in async for ORS
            async def async_llm(messages: list[dict[str, str]]) -> str:
                return sync_llm(messages)

            self.loop.create_task(proactive_check_loop(self, async_llm))
            logger.info("Proactive conversation engine started")

    async def on_guild_join(self, guild):
        """Called when bot joins a guild."""
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Joined server: {guild.name}")

    async def on_guild_remove(self, guild):
        """Called when bot leaves a guild."""
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Left server: {guild.name}")

    async def on_disconnect(self):
        """Called when bot disconnects from Discord."""
        logger.warning("Bot disconnected from Discord")
        monitor.log("system", "Bot", "Disconnected from Discord")
        discord_handler = get_discord_handler()
        if discord_handler:
            await discord_handler.send_direct("🟡 Bot disconnected from Discord")

    async def on_resumed(self):
        """Called when bot resumes after a disconnect."""
        logger.info("Bot resumed connection to Discord")
        monitor.log("system", "Bot", "Resumed connection")
        discord_handler = get_discord_handler()
        if discord_handler:
            await discord_handler.send_direct("🔄 Bot resumed connection")

    async def on_message(self, message: DiscordMessage):
        """Handle incoming messages."""
        # Debug: log all messages
        logger.debug(f"Message from {message.author}: {message.content[:50]!r}")

        # Ignore own messages
        if message.author == self.user:
            return

        # Deduplication: prevent same message from being processed twice
        # (can happen if Discord fires event multiple times due to reconnect)
        import time

        now = time.time()
        msg_id = message.id

        # Cleanup old entries (TTL)
        stale_ids = [mid for mid, ts in self._processed_messages.items() if now - ts > self._processed_messages_ttl]
        for mid in stale_ids:
            del self._processed_messages[mid]

        # Check if already processed
        if msg_id in self._processed_messages:
            logger.warning(f"Duplicate message detected (id={msg_id}), skipping")
            return

        # Mark as being processed
        self._processed_messages[msg_id] = now

        # Check if this is a DM
        is_dm = message.guild is None

        # For DMs: always respond (no mention needed)
        # For channels: require mention or reply, and check channel mode
        if not is_dm:
            is_mentioned = self.user.mentioned_in(message)
            is_reply_to_bot = (
                message.reference and message.reference.resolved and message.reference.resolved.author == self.user
            )

            logger.debug(f"mentioned={is_mentioned}, reply_to_bot={is_reply_to_bot}")

            # Check channel mode configuration
            # "active" mode: respond to all messages
            # "mention" mode: only respond to mentions/replies
            # "off" mode: don't respond at all
            channel_id_str = str(message.channel.id)
            if not should_respond_to_message(channel_id_str, is_mentioned or is_reply_to_bot):
                logger.debug(f"Channel {channel_id_str} mode blocks this message")
                return

            # Check server/channel permissions (only for non-DM)
            # Server allowlist supersedes channel allowlist
            server_id = str(message.guild.id) if message.guild else None
            server_allowed = server_id and server_id in ALLOWED_SERVERS

            if not server_allowed and ALLOWED_CHANNELS:
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

        # Handle channel configuration commands (before regular message processing)
        if not is_dm and await handle_channel_command(message):
            return

        # Check for stop phrase - bypass queue and cancel running task
        if is_stop_phrase(message.content):
            channel_id = message.channel.id
            was_cancelled = await task_queue.cancel_and_clear(channel_id)
            if was_cancelled:
                await message.reply(
                    "-# 🛑 Stopping... I've cancelled my current task.",
                    mention_author=False,
                )
                monitor.log(
                    "system",
                    message.author.display_name,
                    "Stopped running task via stop phrase",
                    guild_name,
                    channel_name,
                )
                logger.info(f"Stop phrase from {message.author} cancelled task in channel {channel_id}")
            else:
                await message.reply(
                    "-# I wasn't working on anything, but I'm here!",
                    mention_author=False,
                )
            return

        # Determine if this was a direct mention/reply (vs active mode catching all messages)
        is_mention = False
        if not is_dm:
            is_mention = self.user.mentioned_in(message) or (
                message.reference and message.reference.resolved and message.reference.resolved.author == self.user
            )

        # Try to acquire channel for processing (queue if busy)
        await self._process_with_queue(message, is_dm, is_mention)

    async def _process_with_queue(self, message: DiscordMessage, is_dm: bool, is_mention: bool = False):
        """Process message with queue management.

        Args:
            message: The Discord message
            is_dm: Whether this is a DM
            is_mention: Whether Clara was explicitly mentioned (vs active mode)
        """
        channel_id = message.channel.id

        # Try to acquire the channel
        acquired, queue_position = await task_queue.try_acquire(message, is_dm, is_mention)

        if not acquired:
            # Channel is busy, message is queued
            logger.info(f"[QUEUE] Message {message.id} queued at position {queue_position}")

            # Only send queue notification for DMs and mentions (not active mode)
            # In active mode, messages will be batched and processed together
            if is_dm or is_mention:
                queue_msg = (
                    f"-# ⏳ I'm working on something else right now. "
                    f"Your request is queued (position {queue_position})."
                )
                try:
                    await message.reply(queue_msg, mention_author=False)
                except Exception as e:
                    logger.warning(f"Failed to send queue notification: {e}")
            else:
                # Active mode: add a reaction to acknowledge the message is queued
                try:
                    await message.add_reaction("⏳")
                except Exception as e:
                    logger.debug(f"Failed to add queue reaction: {e}")

            return  # The task will be processed when dequeued

        # We have the channel, process the message
        logger.info(f"[NEW] Processing message {message.id} from {message.author}: {message.content[:50]!r}")

        # Wrap in a task so it can be cancelled via stop phrase
        async def run_handler():
            await self._handle_message(message, is_dm)

        task = asyncio.create_task(run_handler())
        task_queue.register_task(channel_id, task)

        try:
            await task
        except asyncio.CancelledError:
            logger.info(f"Task cancelled for channel {channel_id}")
            # Don't re-raise - task was intentionally cancelled via stop phrase
        finally:
            task_queue.unregister_task(channel_id)
            # Release channel and check for queued tasks
            await self._process_queued_tasks(channel_id)

    async def _process_queued_tasks(self, channel_id: int):
        """Process any queued tasks for the channel after releasing.

        For active mode (non-DM, non-mention), batches multiple messages together
        for a single combined response. DMs and mentions are processed individually.
        """
        while True:
            # First, try to get a batch of batchable tasks (active mode messages)
            batch = await task_queue.release_batch(channel_id)

            if batch:
                # Process batch of active mode messages together
                await self._process_batched_tasks(channel_id, batch)
                continue

            # No batch available - check for individual task (DM or mention)
            next_task = await task_queue.release(channel_id)
            if not next_task:
                break

            # Process individual task (DM or mention)
            await self._process_single_queued_task(channel_id, next_task)

    async def _process_single_queued_task(self, channel_id: int, task: QueuedTask):
        """Process a single queued task (for DMs and mentions)."""
        logger.info(
            f"[QUEUED] Processing queued message {task.message.id} "
            f"from {task.message.author}: {task.message.content[:50]!r}"
        )

        # Notify user their queued request is starting
        wait_time = (datetime.now(UTC) - task.queued_at).total_seconds()
        start_msg = f"-# ▶️ Starting your queued request (waited {wait_time:.0f}s)..."
        try:
            await task.message.reply(start_msg, mention_author=False)
        except Exception as e:
            logger.warning(f"Failed to send start notification: {e}")

        # Process the queued task (wrapped for cancellation support)
        async def run_queued_handler():
            await self._handle_message(task.message, task.is_dm)

        async_task = asyncio.create_task(run_queued_handler())
        task_queue.register_task(channel_id, async_task)

        try:
            await async_task
        except asyncio.CancelledError:
            logger.info(f"Queued task cancelled for channel {channel_id}")
        except Exception as e:
            logger.exception(f"Error processing queued task: {e}")
            try:
                err_msg = f"Sorry, I encountered an error processing your queued request: {str(e)[:100]}"
                await task.message.reply(err_msg, mention_author=False)
            except Exception:
                pass
        finally:
            task_queue.unregister_task(channel_id)

    async def _process_batched_tasks(self, channel_id: int, batch: list[QueuedTask]):
        """Process a batch of active mode messages together.

        Combines multiple messages into a single context and generates one response.
        """
        if not batch:
            return

        logger.info(
            f"[BATCH] Processing {len(batch)} queued messages together for channel {channel_id}"
        )

        # Remove the hourglass reactions we added
        for task in batch:
            try:
                await task.message.remove_reaction("⏳", self.user)
            except Exception:
                pass  # Reaction may have been removed or message deleted

        # Calculate total wait time (from first message in batch)
        first_queued = min(t.queued_at for t in batch)
        wait_time = (datetime.now(UTC) - first_queued).total_seconds()

        # Use the last message as the "anchor" for the response
        anchor_message = batch[-1].message

        # Log all messages being batched
        for task in batch:
            logger.info(
                f"  [BATCH] Including: {task.message.author.display_name}: "
                f"{task.message.content[:50]!r}"
            )

        # Send a brief indicator that we're responding to multiple messages
        if len(batch) > 1:
            try:
                indicator = f"-# 📨 Catching up on {len(batch)} messages (waited {wait_time:.0f}s)..."
                await anchor_message.channel.send(indicator, silent=True)
            except Exception as e:
                logger.warning(f"Failed to send batch indicator: {e}")

        # Process the batch (wrapped for cancellation support)
        async def run_batch_handler():
            await self._handle_message(
                anchor_message,
                is_dm=False,
                batched_messages=[t.message for t in batch],
            )

        async_task = asyncio.create_task(run_batch_handler())
        task_queue.register_task(channel_id, async_task)

        try:
            await async_task
        except asyncio.CancelledError:
            logger.info(f"Batched task cancelled for channel {channel_id}")
        except Exception as e:
            logger.exception(f"Error processing batched tasks: {e}")
            try:
                err_msg = f"Sorry, I encountered an error: {str(e)[:100]}"
                await anchor_message.reply(err_msg, mention_author=False)
            except Exception:
                pass
        finally:
            task_queue.unregister_task(channel_id)

    async def _handle_message(
        self,
        message: DiscordMessage,
        is_dm: bool = False,
        auto_continue_count: int = 0,
        auto_continue_content: str | None = None,
        batched_messages: list[DiscordMessage] | None = None,
    ):
        """Process a message and generate a response.

        Args:
            message: The Discord message to respond to (or anchor for batch)
            is_dm: Whether this is a DM (vs channel message)
            auto_continue_count: How many auto-continues have happened (to prevent loops)
            auto_continue_content: If set, use this as the user message instead of message.content
            batched_messages: If set, process these messages together as a batch (active mode)
        """
        if batched_messages and len(batched_messages) > 1:
            content_preview = f"[BATCH of {len(batched_messages)} messages]"
        else:
            content_preview = (auto_continue_content or message.content)[:50]
        logger.info(f"Handling message from {message.author}: {content_preview!r}")

        async with message.channel.typing():
            try:
                # Fetch context: channel history for channels, reply chain for DMs
                if not is_dm:
                    channel_id = f"discord-channel-{message.channel.id}"
                    all_channel_msgs = await self._fetch_channel_history(message.channel)
                    (
                        channel_summary,
                        recent_channel_msgs,
                    ) = await self._get_or_update_channel_summary(channel_id, all_channel_msgs)
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

                # Track interaction for proactive engine
                proactive_channel_id = (
                    f"discord-dm-{message.author.id}" if is_dm else f"discord-channel-{message.channel.id}"
                )
                await proactive_on_user_message(
                    user_id=user_id,
                    channel_id=proactive_channel_id,
                    message_preview=message.content[:100] if message.content else None,
                )

                # Get the user's message content (or use auto-continue content, or batched)
                tier_override = None
                image_attachments = []  # Images for vision support

                if batched_messages and len(batched_messages) > 1:
                    # Batched mode: combine multiple messages into one context
                    logger.info(f" Processing batch of {len(batched_messages)} messages")
                    batch_parts = []
                    for batch_msg in batched_messages:
                        # Get content and extract attachments for each message
                        msg_content = self._clean_content(batch_msg.content)
                        msg_user_id = f"discord-{batch_msg.author.id}"

                        # Extract attachments from each batched message
                        msg_attachments = await self._extract_attachments(batch_msg, msg_user_id)
                        attachment_text = []
                        for att in msg_attachments:
                            if att.get("type") == "image":
                                image_attachments.append(att)
                                attachment_text.append(f" [Image: {att['filename']}]")
                            elif "content" in att:
                                attachment_text.append(f"\n--- File: {att['filename']} ---\n{att['content']}")
                            elif "note" in att:
                                attachment_text.append(f" [Attachment: {att['filename']}]")

                        msg_content += "".join(attachment_text)

                        # Include username for each message in the batch
                        display_name = batch_msg.author.display_name
                        batch_parts.append(f"[{display_name}]: {msg_content}")

                    # Join all batched messages with line breaks
                    raw_content = "\n".join(batch_parts)
                    user_content = f"[Multiple messages to address]\n{raw_content}"
                    logger.debug(f" Batched content: {len(user_content)} chars from {len(batched_messages)} messages")

                elif auto_continue_content:
                    raw_content = auto_continue_content
                    # Don't change tier on auto-continue

                    # For channels, prefix with username so Clara knows who's speaking
                    if not is_dm:
                        display_name = message.author.display_name
                        user_content = f"[{display_name}]: {raw_content}"
                    else:
                        user_content = raw_content
                else:
                    raw_content = self._clean_content(message.content)
                    # Detect tier override from message prefix (!high, !mid, !low, etc.)
                    tier_override, raw_content = detect_tier_from_message(raw_content)

                    # Extract and append file attachments (also saves to local storage)
                    attachments = await self._extract_attachments(message, user_id)
                    if attachments:
                        attachment_text = []
                        for att in attachments:
                            if att.get("type") == "image":
                                # Store image for multimodal message
                                image_attachments.append(att)
                                attachment_text.append(f"\n\n[Image: {att['filename']}]")
                            elif "content" in att:
                                attachment_text.append(f"\n\n--- File: {att['filename']} ---\n{att['content']}")
                            elif "note" in att:
                                # File saved locally but not shown inline
                                fname, note = att["filename"], att["note"]
                                attachment_text.append(f"\n\n[Attachment: {fname}] {note}")
                            elif "error" in att:
                                fname, err = att["filename"], att["error"]
                                attachment_text.append(f"\n\n[File {fname}: {err}]")
                        raw_content += "".join(attachment_text)
                        logger.debug(f" Added {len(attachments)} file(s) to message")

                    # For channels, prefix with username so Clara knows who's speaking
                    if not is_dm:
                        display_name = message.author.display_name
                        user_content = f"[{display_name}]: {raw_content}"
                    else:
                        user_content = raw_content

                if image_attachments:
                    logger.debug(f" Including {len(image_attachments)} image(s) for vision")

                logger.debug(f" Content length: {len(user_content)} chars")

                # Track sentiment for emotional continuity (fast, no API calls)
                channel_id = f"discord-channel-{message.channel.id}" if not is_dm else f"discord-dm-{message.author.id}"
                track_message_sentiment(user_id, channel_id, raw_content)

                # Extract participants from conversation for cross-user memory
                participants = self._extract_participants(recent_channel_msgs, message.author)
                if len(participants) > 1:
                    names = [p["name"] for p in participants]
                    logger.debug(f" Participants: {', '.join(names)}")

                # Fetch memories (DMs prioritize personal, servers prioritize project)
                # Run in dedicated executor to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                user_mems, proj_mems = await loop.run_in_executor(
                    BLOCKING_IO_EXECUTOR,
                    lambda: self.mm.fetch_mem0_context(
                        user_id,
                        project_id,
                        user_content,
                        participants=participants,
                        is_dm=is_dm,
                    ),
                )
                db = SessionLocal()
                try:
                    recent_msgs = self.mm.get_recent_messages(db, thread.id)
                finally:
                    db.close()

                # Check if we need to finalize previous conversation (gap-based trigger)
                channel_name = getattr(message.channel, "name", "DM") if not is_dm else "DM"
                await self._maybe_finalize_previous_conversation(user_id, channel_id, channel_name, is_dm, recent_msgs)

                # Fetch emotional context from recent sessions (for tone calibration)
                emotional_context = await loop.run_in_executor(
                    BLOCKING_IO_EXECUTOR,
                    lambda: self.mm.fetch_emotional_context(user_id, limit=3),
                )

                # Fetch recurring topic patterns (for awareness of what keeps coming up)
                recurring_topics = await loop.run_in_executor(
                    BLOCKING_IO_EXECUTOR,
                    lambda: self.mm.fetch_topic_recurrence(user_id, lookback_days=14, min_mentions=2),
                )

                # Build prompt with Clara's persona
                prompt_messages = self.mm.build_prompt(
                    user_mems,
                    proj_mems,
                    thread.session_summary,
                    recent_msgs,
                    user_content,
                    emotional_context=emotional_context,
                    recurring_topics=recurring_topics,
                )

                # Inject Discord-specific context after the base system prompt
                discord_context = self._build_discord_context(message, user_mems, proj_mems, is_dm, recent_msgs)
                # Insert as second system message (after Clara's persona)
                system_msg = {"role": "system", "content": discord_context}
                prompt_messages.insert(1, system_msg)

                # Add channel summary if available (for channels only)
                if channel_summary:
                    summary_content = f"## Earlier Channel Context (summarized)\n{channel_summary}"
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
                    prompt_messages = prompt_messages[:-1] + channel_context + [prompt_messages[-1]]

                # Debug: check Docker sandbox status
                docker_available = DOCKER_ENABLED and get_sandbox_manager().is_available()
                logger.debug(f" Docker sandbox: enabled={DOCKER_ENABLED}, available={docker_available}")

                # Generate streaming response (with optional tier override and images for vision)
                response = await self._generate_response(
                    message, prompt_messages, tier_override, images=image_attachments
                )

                # Store in Clara's memory system
                # Use thread_owner for message storage, user_id for memories
                # DMs store as "personal" memories, servers as "project" memories
                if response:
                    await self._store_exchange(
                        thread_owner,  # For message storage in shared thread
                        user_id,  # For per-user memory extraction
                        project_id,
                        thread.id,
                        user_content,
                        response,
                        participants=participants,
                        is_dm=is_dm,
                    )

                    # Log response to monitor
                    guild_name = message.guild.name if message.guild else None
                    channel_name = getattr(message.channel, "name", "DM")
                    response_preview = response[:200] + "..." if len(response) > 200 else response
                    monitor.log("response", "Clara", response_preview, guild_name, channel_name)

                    # Check for auto-continue (Clara asking permission to proceed)
                    if _should_auto_continue(response) and auto_continue_count < AUTO_CONTINUE_MAX:
                        logger.info(f"Auto-continuing ({auto_continue_count + 1}/{AUTO_CONTINUE_MAX})")
                        # Send a subtle indicator that we're auto-continuing
                        await message.channel.send("-# ▶️ Proceeding automatically...", silent=True)
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

    async def _build_message_chain(self, message: DiscordMessage) -> list[CachedMessage]:
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
                    current = await message.channel.fetch_message(current.reference.message_id)
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
                oldest = sorted(self.msg_cache.items(), key=lambda x: x[1].timestamp)[:100]
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

    async def _fetch_channel_history(self, channel, limit: int = CHANNEL_HISTORY_LIMIT) -> list[CachedMessage]:
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
            summary_record = db.query(ChannelSummary).filter_by(channel_id=channel_id).first()

            # Check if we need to update summary
            needs_update = False
            if not summary_record:
                summary_record = ChannelSummary(channel_id=channel_id)
                db.add(summary_record)
                needs_update = bool(old_messages)
            elif old_messages:
                # Check if there are new old messages since last summary
                last_old_ts = old_messages[-1].timestamp.replace(tzinfo=None)
                if not summary_record.summary_cutoff_at or last_old_ts > summary_record.summary_cutoff_at:
                    needs_update = True

            if needs_update and old_messages:
                # Generate new summary including old summary + new old messages
                existing_summary = summary_record.summary or ""
                new_summary = await self._summarize_messages(existing_summary, old_messages)
                summary_record.summary = new_summary
                summary_record.summary_cutoff_at = old_messages[-1].timestamp.replace(tzinfo=None)
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
        summary = await loop.run_in_executor(BLOCKING_IO_EXECUTOR, lambda: self._sync_llm(prompt))
        return summary

    async def _ensure_project(self, user_id: str) -> str:
        """Ensure project exists and return its ID."""
        db = SessionLocal()
        try:
            proj = db.query(Project).filter_by(owner_id=user_id, name=DEFAULT_PROJECT).first()
            if not proj:
                proj = Project(owner_id=user_id, name=DEFAULT_PROJECT)
                db.add(proj)
                db.commit()
                db.refresh(proj)
            return proj.id
        finally:
            db.close()

    async def _ensure_thread(self, message: DiscordMessage, is_dm: bool) -> tuple[Session, str]:
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
        images: list[dict] | None = None,
    ) -> str:
        """Generate response and send to Discord, handling tool calls.

        Args:
            message: The Discord message to respond to
            prompt_messages: The conversation history and context
            tier: Optional model tier override (high/mid/low)
            images: Optional list of image attachments for vision support
        """
        # Auto tier selection - classify message complexity if no explicit tier
        auto_selected = False
        if tier is None and AUTO_TIER_ENABLED:
            # Get the user's message content for classification
            user_msg = next(
                (m["content"] for m in reversed(prompt_messages) if m.get("role") == "user"),
                "",
            )
            if user_msg:
                # Extract recent conversation for context (exclude system messages)
                recent_for_tier = [m for m in prompt_messages if m.get("role") in ("user", "assistant")]
                # Exclude the current message from context (it's passed separately)
                if recent_for_tier and recent_for_tier[-1].get("content") == user_msg:
                    recent_for_tier = recent_for_tier[:-1]
                tier = await classify_message_complexity(user_msg, recent_for_tier)
                auto_selected = True
                logger.info(f"Auto-selected tier '{tier}' for message complexity")

        # Log tier info if specified
        if tier:
            emoji, display = TIER_DISPLAY.get(tier, ("", tier))
            logger.info(f"Generating response for {message.author} using {display}...")
        else:
            logger.info(f"Generating response for {message.author}...")
        user_id = f"discord-{message.author.id}"

        try:
            # Send tier indicator if tier was explicitly selected or auto-selected
            if tier:
                emoji, display = TIER_DISPLAY.get(tier, ("⚙️", tier))
                model_name = get_model_for_tier(tier)
                # Extract just the model name without provider prefix
                short_model = model_name.split("/")[-1] if "/" in model_name else model_name
                auto_tag = " (auto)" if auto_selected else ""
                await message.channel.send(f"-# {emoji} Using {display}{auto_tag} ({short_model})", silent=True)
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
                message, prompt_messages, user_id, loop, active_tools, tier, images
            )

            logger.info(f"Got response: {len(full_response)} chars")

            if not full_response:
                logger.warning("Empty response from LLM")
                full_response = "I'm sorry, I didn't generate a response."

            # Extract any file attachments from the response text
            cleaned_response, inline_files = self._extract_file_attachments(full_response)
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
                                discord_files.append(discord.File(fp=io.BytesIO(content), filename=file_path.name))
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
                        response_msg = await message.reply(chunk, mention_author=False, files=chunk_files)
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
                            username="Clara",
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
        images: list[dict] | None = None,
    ) -> tuple[str, list]:
        """Generate response with tool calling support.

        Args:
            message: The Discord message to respond to
            prompt_messages: The conversation history and context
            user_id: The user ID for sandbox management
            loop: The event loop for running blocking calls
            active_tools: List of tool definitions to use
            tier: Optional model tier override (high/mid/low)
            images: Optional list of image attachments for vision support

        Returns:
            tuple: (response_text, list of file paths to send)
        """
        from pathlib import Path

        sandbox_manager = get_sandbox_manager()
        file_manager = get_file_manager()
        messages = list(prompt_messages)  # Copy to avoid mutation

        # Convert the last user message to multimodal format if images are present
        if images:
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    text_content = messages[i]["content"]
                    # Build multimodal content array
                    content_parts = []
                    # Add text first
                    if isinstance(text_content, str) and text_content.strip():
                        content_parts.append({"type": "text", "text": text_content})
                    # Add images
                    for img in images:
                        # OpenAI/OpenRouter format: image_url with data URL
                        content_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{img['media_type']};base64,{img['base64_data']}",
                                },
                            }
                        )
                    messages[i]["content"] = content_parts
                    logger.debug(f" Converted user message to multimodal with {len(images)} image(s)")
                    break

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
            tools_logger.info(f"[msg:{message.id}] Iteration {iteration + 1}/{MAX_TOOL_ITERATIONS}")

            # Call LLM with tools
            # Use native Anthropic SDK when LLM_PROVIDER=anthropic
            provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

            def call_llm():
                if provider == "anthropic":
                    llm = make_llm_with_tools_anthropic(active_tools, tier=tier)
                    anthropic_response = llm(messages)
                    # Convert to OpenAI-like dict for unified processing
                    return anthropic_to_openai_response(anthropic_response)
                else:
                    llm = make_llm_with_tools(active_tools, tier=tier)
                    completion = llm(messages)
                    msg = completion.choices[0].message
                    # Convert to dict for unified processing
                    return {
                        "content": msg.content,
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in (msg.tool_calls or [])
                        ]
                        if msg.tool_calls
                        else None,
                    }

            response_message = await loop.run_in_executor(BLOCKING_IO_EXECUTOR, call_llm)

            # Check if there are tool calls
            if not response_message.get("tool_calls"):
                if iteration == 0:
                    # First iteration with no tools - fall back to main chat LLM
                    # This preserves the main LLM's personality for regular chat
                    logger.info("No tools needed, using main chat LLM")

                    # Remove the tool instruction we added
                    original_messages = [m for m in messages if m.get("content") != tool_instruction["content"]]

                    def main_llm_call():
                        llm = make_llm(tier=tier)
                        return llm(original_messages)

                    result = await loop.run_in_executor(BLOCKING_IO_EXECUTOR, main_llm_call)
                    return result or "", files_to_send
                else:
                    # Tools were used in previous iterations, return tool model's response
                    return response_message.get("content") or "", files_to_send

            # Process tool calls
            tool_calls = response_message.get("tool_calls", [])
            tool_count = len(tool_calls)
            tools_logger.info(f"[msg:{message.id}] Processing {tool_count} tool call(s)")

            # Add assistant message with tool calls to conversation
            # response_message is already in the right format
            messages.append(response_message)

            # Execute each tool call and add results
            for tool_call in tool_calls:
                tool_name = tool_call["function"]["name"]
                try:
                    raw_args = tool_call["function"]["arguments"]
                    # Debug: log raw arguments
                    if raw_args:
                        tools_logger.debug(f"Raw args type: {type(raw_args).__name__}, len: {len(raw_args)}")
                        preview = raw_args[:200] + "..." if len(raw_args) > 200 else raw_args
                        tools_logger.debug(f"Raw args preview: {preview}")
                    else:
                        tools_logger.warning("raw_args is empty/None")

                    arguments = json.loads(raw_args) if raw_args else {}
                except (json.JSONDecodeError, TypeError) as e:
                    tools_logger.error(f"JSON parse error: {e}")
                    tools_logger.error(f"Raw value: {repr(raw_args)[:500]}")
                    arguments = {}

                # Validate and coerce arguments against tool schema
                registry = get_registry()
                tool_def = registry.get_tool(tool_name)
                if tool_def:
                    arguments, validation_warnings = validate_tool_args(tool_name, arguments, tool_def.parameters)
                    for warning in validation_warnings:
                        tools_logger.warning(f"[{tool_name}] {warning}")

                tools_logger.info(
                    f"[msg:{message.id}] Executing: {tool_name} with {len(arguments)} args: {list(arguments.keys())}"
                )

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

                # Generate Haiku description for tools without custom status
                haiku_desc = None
                try:
                    haiku_desc = await generate_tool_description(tool_name, arguments)
                except Exception:
                    pass  # Silently fail - description is optional

                # Build final status message
                if haiku_desc:
                    status_msg = f"-# {status_text}{step_label}\n-# ↳ *{haiku_desc}*"
                else:
                    status_msg = f"-# {status_text}{step_label}"

                try:
                    await message.channel.send(status_msg, silent=True)
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

                # Check for special Discord button response
                try:
                    button_data = json.loads(tool_output)
                    if button_data.get("_discord_button"):
                        # Send a Discord URL button
                        view = discord.ui.View()
                        view.add_item(
                            discord.ui.Button(
                                label=button_data.get("label", "Click Here"),
                                url=button_data["url"],
                                emoji=button_data.get("emoji"),
                            )
                        )
                        await message.channel.send(
                            button_data.get("message", ""),
                            view=view,
                        )
                        # Simplify output for LLM context
                        tool_output = "OAuth authorization link sent to user via Discord button."
                except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                    pass  # Not a button response, use as-is

                # Add tool result to conversation
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_output,
                    }
                )

                success = not tool_output.startswith("Error:")
                status = "success" if success else "failed"
                tools_logger.info(f"[msg:{message.id}] {tool_name} → {status}")

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
                    "You've reached the maximum number of tool calls. " "Please summarize what you've accomplished."
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

        result = await loop.run_in_executor(BLOCKING_IO_EXECUTOR, final_call)
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

        Handles MCP tools, Docker sandbox tools, local file tools, and chat history tools.
        """
        import time

        exec_start = time.time()
        tools_logger.debug(f"[EXEC] Starting execution of {tool_name}")

        # Get channel_id for file storage organization
        channel_id = str(channel.id) if channel else None

        # Route MCP tools directly to manager (bypass registry)
        if "__" in tool_name and _mcp_initialized:
            try:
                manager = get_mcp_manager()
                if manager.is_mcp_tool(tool_name):
                    tools_logger.debug(f"[EXEC] Routing {tool_name} to MCP manager")
                    result = await manager.call_tool(tool_name, arguments)
                    tools_logger.debug(f"[EXEC] {tool_name} completed in {time.time() - exec_start:.2f}s")
                    return result
            except Exception as e:
                tools_logger.error(f"MCP tool {tool_name} failed: {e}")
                return f"Error executing MCP tool {tool_name}: {e}"

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
            result = await sandbox_manager.handle_tool_call(user_id, tool_name, arguments)
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
            # Try modular tools from registry (GitHub, ADO, etc.)
            if _modular_tools_initialized:
                registry = get_registry()
                if tool_name in registry:
                    tools_logger.debug(f"[EXEC] Routing {tool_name} to registry")
                    # Build tool context for modular tools
                    ctx = ToolContext(
                        user_id=user_id,
                        channel_id=channel_id,
                        platform="discord",
                        extra={
                            "channel": channel,
                            "files_to_send": files_to_send,
                            "bot": self,  # For cross-channel messaging
                        },
                    )
                    try:
                        result = await registry.execute(tool_name, arguments, ctx)
                        tools_logger.debug(f"[EXEC] {tool_name} completed in {time.time() - exec_start:.2f}s")
                        return result
                    except Exception as e:
                        tools_logger.error(f"Modular tool {tool_name} failed: {e}")
                        return f"Error executing {tool_name}: {e}"

            tools_logger.warning(f"[EXEC] Unknown tool: {tool_name}")
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
        # This catches cases where Clara forgets the closing tag entirely
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

            cleaned = re.sub(
                unclosed_pattern,
                replace_unclosed,
                cleaned,
                flags=re.DOTALL | re.IGNORECASE,
            )

        # Debug: check if we still have unmatched file tags
        remaining_tags = re.findall(r"<<<\s*file\s*:", cleaned, re.IGNORECASE)
        if remaining_tags:
            logger.warning(f"Found {len(remaining_tags)} unmatched <<<file: tag(s) after extraction")
            logger.debug(f"Text snippet: {cleaned[:500]}")

        return cleaned, files

    def _create_discord_files(self, files: list[tuple[str, str]]) -> list[discord.File]:
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
                discord_file = discord.File(fp=io.BytesIO(content_bytes), filename=filename)
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
        is_dm: bool = False,
    ):
        """Store the exchange in Clara's memory system.

        Args:
            thread_owner_id: ID for message storage (channel or DM owner)
            memory_user_id: ID for mem0 memory extraction (always per-user)
            project_id: Project ID for memory organization
            is_dm: Whether this is a DM (stores as "personal" vs "project" memory)
            thread_id: Thread ID for message storage
            user_message: The user's message
            assistant_reply: Clara's response
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
            self.mm.store_message(db, thread_id, thread_owner_id, "assistant", assistant_reply)
            thread.last_activity_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()

            # Update summary periodically
            if self.mm.should_update_summary(db, thread_id):
                self.mm.update_thread_summary(db, thread)

            # Add to mem0 for per-user memory extraction
            # DMs store as "personal" memories, servers store as "project" memories
            self.mm.add_to_mem0(
                memory_user_id,
                project_id,
                recent_msgs,
                user_message,
                assistant_reply,
                participants=participants,
                is_dm=is_dm,
            )
            logger.debug(f" Stored exchange (thread: {thread_owner_id[:20]}...)")

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


@monitor_app.get("/health")
def health_check():
    """Health check endpoint for Railway and other platforms."""
    stats = monitor.get_stats()
    return {
        "status": "healthy",
        "bot_connected": stats.get("bot_user") is not None,
        "uptime_seconds": stats.get("uptime_seconds", 0),
        "guilds": stats.get("guild_count", 0),
    }


# ============== Google OAuth Endpoints ==============


@monitor_app.get("/oauth/google/callback", response_class=HTMLResponse)
async def google_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """Handle Google OAuth callback - exchange code for tokens."""
    from tools.google_oauth import (
        decode_state,
        exchange_code_for_tokens,
        is_configured,
    )

    if not is_configured():
        return _oauth_error_html("Google OAuth not configured on this server.")

    if error:
        return _oauth_error_html(f"Google authorization denied: {error}")

    if not code or not state:
        return _oauth_error_html("Missing authorization code or state.")

    try:
        user_id = decode_state(state)
        await exchange_code_for_tokens(code, user_id)
        return _oauth_success_html()
    except Exception as e:
        logger.error(f"Google OAuth error: {e}")
        return _oauth_error_html(f"Failed to connect: {e}")


@monitor_app.get("/oauth/google/status/{user_id}")
def google_oauth_status(user_id: str):
    """Check if a user has connected their Google account."""
    from tools.google_oauth import is_configured, is_user_connected

    return {
        "configured": is_configured(),
        "connected": is_user_connected(user_id) if is_configured() else False,
    }


def _oauth_success_html() -> str:
    """HTML page for successful OAuth connection."""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Google Connected - Clara</title>
    <style>
        body {
            font-family: system-ui, sans-serif;
            background: #1a1a2e;
            color: #eee;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .card {
            background: #252542;
            padding: 40px;
            border-radius: 12px;
            text-align: center;
            max-width: 400px;
        }
        .success { color: #43b581; font-size: 48px; margin-bottom: 20px; }
        h1 { color: #7289da; margin-bottom: 10px; }
        p { color: #aaa; }
    </style>
</head>
<body>
    <div class="card">
        <div class="success">✓</div>
        <h1>Google Connected!</h1>
        <p>Your Google account is now connected to Clara. You can close this window and return to Discord.</p>
    </div>
</body>
</html>
"""


def _oauth_error_html(message: str) -> str:
    """HTML page for OAuth error."""
    import html

    safe_message = html.escape(message)
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Connection Failed - Clara</title>
    <style>
        body {{
            font-family: system-ui, sans-serif;
            background: #1a1a2e;
            color: #eee;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }}
        .card {{
            background: #252542;
            padding: 40px;
            border-radius: 12px;
            text-align: center;
            max-width: 400px;
        }}
        .error {{ color: #f04747; font-size: 48px; margin-bottom: 20px; }}
        h1 {{ color: #f04747; margin-bottom: 10px; }}
        p {{ color: #aaa; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="error">✗</div>
        <h1>Connection Failed</h1>
        <p>{safe_message}</p>
    </div>
</body>
</html>
"""


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

    # Register slash commands cog
    try:
        setup_slash_commands(bot)
        logger.info("Slash commands cog registered")
    except Exception as e:
        logger.warning(f"Failed to register slash commands: {e}")

    try:
        await bot.start(BOT_TOKEN)
    finally:
        # Shutdown MCP servers gracefully
        if _mcp_initialized:
            try:
                await shutdown_mcp()
                logger.info("MCP servers shut down")
            except Exception as e:
                logger.warning(f"Error shutting down MCP servers: {e}")

        # Send shutdown message before closing
        discord_handler = get_discord_handler()
        if discord_handler and not bot.is_closed():
            try:
                await discord_handler.send_direct("🔴 Bot shutting down...")
            except Exception:
                pass  # Best effort
        await bot.close()


async def run_monitor_server():
    """Run the FastAPI monitoring server."""
    config = uvicorn.Config(monitor_app, host="0.0.0.0", port=MONITOR_PORT, log_level="warning")
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

    logger.info("Clara Discord Bot Starting")

    config_logger.info(f"Max message chain: {MAX_MESSAGES}")
    if ALLOWED_SERVERS:
        config_logger.info(f"Allowed servers ({len(ALLOWED_SERVERS)}): {', '.join(ALLOWED_SERVERS)}")
    else:
        config_logger.info("Allowed servers: NONE (using channel list)")
    if ALLOWED_CHANNELS:
        config_logger.info(f"Allowed channels ({len(ALLOWED_CHANNELS)}): {', '.join(ALLOWED_CHANNELS)}")
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
            sandbox_logger.info("  - docker package not installed (run: poetry add docker)")
        elif not sandbox_mgr.is_available():
            sandbox_logger.info("  - Docker daemon not running (start Docker Desktop or dockerd)")

    if MONITOR_ENABLED:
        logger.info(f"Dashboard at http://localhost:{MONITOR_PORT}")
        await asyncio.gather(run_bot(), run_monitor_server())
    else:
        await run_bot()


def daemonize(pidfile: str, logfile: str | None = None):
    """Fork the process into a background daemon (Unix only).

    Args:
        pidfile: Path to write the daemon's PID
        logfile: Path to redirect stdout/stderr (None = /dev/null)
    """
    import sys

    # Convert relative paths to absolute before changing directory
    pidfile = os.path.abspath(pidfile)
    if logfile:
        logfile = os.path.abspath(logfile)

    # First fork
    try:
        pid = os.fork()
        if pid > 0:
            # Parent exits
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"First fork failed: {e}\n")
        sys.exit(1)

    # Decouple from parent environment
    os.chdir("/")
    os.setsid()
    os.umask(0)

    # Second fork
    try:
        pid = os.fork()
        if pid > 0:
            # Parent exits
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"Second fork failed: {e}\n")
        sys.exit(1)

    # Redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()

    if logfile:
        # Redirect to log file
        log_fd = open(logfile, "a+")
        os.dup2(log_fd.fileno(), sys.stdout.fileno())
        os.dup2(log_fd.fileno(), sys.stderr.fileno())
    else:
        # Redirect to /dev/null
        devnull = open("/dev/null", "r+")
        os.dup2(devnull.fileno(), sys.stdin.fileno())
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())

    # Write PID file
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))


def stop_daemon(pidfile: str) -> bool:
    """Stop a running daemon using its PID file.

    Args:
        pidfile: Path to the PID file

    Returns:
        True if daemon was stopped, False otherwise
    """
    import signal

    try:
        with open(pidfile, "r") as f:
            pid = int(f.read().strip())
    except FileNotFoundError:
        print(f"PID file not found: {pidfile}")
        return False
    except ValueError:
        print(f"Invalid PID in {pidfile}")
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to process {pid}")
        # Wait a moment and check if it's still running
        import time

        time.sleep(1)
        try:
            os.kill(pid, 0)  # Check if process exists
            print(f"Process {pid} still running, sending SIGKILL...")
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass  # Process already terminated
        # Clean up PID file
        if os.path.exists(pidfile):
            os.remove(pidfile)
        print("Daemon stopped")
        return True
    except OSError as e:
        print(f"Failed to stop daemon: {e}")
        if os.path.exists(pidfile):
            os.remove(pidfile)
        return False


def main():
    """Run the Discord bot with optional monitoring."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Clara Discord Bot - AI assistant with memory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d",
        "--daemon",
        action="store_true",
        help="Run as a background daemon (Unix only)",
    )
    parser.add_argument(
        "--pidfile",
        default="/tmp/clara-discord.pid",
        help="PID file path (default: /tmp/clara-discord.pid)",
    )
    parser.add_argument(
        "--logfile",
        default=None,
        help="Log file path when daemonized (default: /dev/null)",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop a running daemon",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check if daemon is running",
    )

    args = parser.parse_args()

    # Handle --stop
    if args.stop:
        stop_daemon(args.pidfile)
        return

    # Handle --status
    if args.status:
        try:
            with open(args.pidfile, "r") as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                print(f"Daemon is running (PID: {pid})")
            except OSError:
                print("Daemon is not running (stale PID file)")
        except FileNotFoundError:
            print("Daemon is not running (no PID file)")
        return

    # Handle --daemon
    if args.daemon:
        # Check if already running
        if os.path.exists(args.pidfile):
            try:
                with open(args.pidfile, "r") as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 0)
                    print(f"Daemon already running (PID: {pid})")
                    return
                except OSError:
                    # Stale PID file, remove it
                    os.remove(args.pidfile)
            except (ValueError, FileNotFoundError):
                pass

        print(f"Starting daemon (PID file: {args.pidfile})...")
        if args.logfile:
            print(f"Logging to: {args.logfile}")
        daemonize(args.pidfile, args.logfile)

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Clean up PID file if we're a daemon
        if args.daemon and os.path.exists(args.pidfile):
            try:
                os.remove(args.pidfile)
            except OSError:
                pass


if __name__ == "__main__":
    main()
