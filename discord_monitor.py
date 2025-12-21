"""
Discord Bot Monitoring Dashboard

A simple web UI for monitoring Clara's Discord bot activity.
Run alongside the main discord_bot.py or integrate into it.

Usage:
    poetry run python discord_monitor.py
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

import discord
import uvicorn
from discord import Message as DiscordMessage
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from db import SessionLocal, init_db
from discord_bot import ALLOWED_CHANNELS, ALLOWED_ROLES, CachedMessage
from llm_backends import make_llm
from memory_manager import MemoryManager

load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
MONITOR_PORT = int(os.getenv("DISCORD_MONITOR_PORT", "8001"))
MAX_LOG_ENTRIES = 100


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
        self.guilds: dict[int, dict] = {}  # guild_id -> info
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

        # Update counters
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


# ============== FastAPI Dashboard ==============

app = FastAPI(title="Clara Discord Monitor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/stats")
def get_stats():
    """Get bot statistics."""
    return monitor.get_stats()


@app.get("/api/guilds")
def get_guilds():
    """Get list of guilds."""
    return {"guilds": list(monitor.guilds.values())}


@app.get("/api/logs")
def get_logs(limit: int = 50, event_type: str | None = None):
    """Get recent log entries."""
    logs = list(monitor.logs)
    if event_type:
        logs = [entry for entry in logs if entry.event_type == event_type]
    return {"logs": [entry.to_dict() for entry in logs[:limit]]}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the monitoring dashboard."""
    return """
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
        .stat-card .label {
            color: #888;
            margin-top: 5px;
        }
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
        .guild-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        .guild {
            background: #1a1a2e;
            border-radius: 8px;
            padding: 10px 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .guild img {
            width: 32px;
            height: 32px;
            border-radius: 50%;
        }
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
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }
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
        .log-entry .content {
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
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
            const d = new Date(isoString);
            return d.toLocaleTimeString();
        }

        async function fetchStats() {
            const res = await fetch('/api/stats');
            const data = await res.json();

            const uptime = formatUptime(data.uptime_seconds);
            document.getElementById('uptime').textContent = uptime;

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
                            ${l.guild ? `<b>${l.guild}</b> #${l.channel} • ` : ''}
                            <strong>${l.user}</strong>
                        </div>
                        <div class="text">${l.content.replace(/</g, '&lt;')}</div>
                    </div>
                </div>
            `).join('') || '<div style="padding:20px;color:#666">No activity yet</div>';
        }

        // Tab filtering
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t =>
                    t.classList.remove('active'));
                tab.classList.add('active');
                currentFilter = tab.dataset.filter;
                fetchLogs();
            });
        });

        // Initial load
        fetchStats();
        fetchGuilds();
        fetchLogs();

        // Auto-refresh
        setInterval(() => {
            fetchStats();
            fetchGuilds();
            fetchLogs();
        }, 3000);
    </script>
</body>
</html>
"""


# ============== Discord Bot with Monitoring ==============


class MonitoredClaraBot(discord.Client):
    """Clara Discord bot with monitoring integration."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)

        self.msg_cache: dict[int, CachedMessage] = {}
        self.cache_lock = asyncio.Lock()

        init_db()
        self.mm = MemoryManager(llm_callable=self._sync_llm)

    def _sync_llm(self, messages: list[dict]) -> str:
        llm = make_llm()
        return llm(messages)

    async def on_ready(self):
        print(f"[discord] Logged in as {self.user}")
        monitor.bot_user = str(self.user)
        monitor.start_time = datetime.now(UTC)
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Logged in as {self.user}")

    async def on_guild_join(self, guild):
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Joined server: {guild.name}")

    async def on_guild_remove(self, guild):
        monitor.update_guilds(self.guilds)
        monitor.log("system", "Bot", f"Left server: {guild.name}")

    async def on_message(self, message: DiscordMessage):
        if message.author == self.user:
            return

        is_dm = message.guild is None
        guild_name = message.guild.name if message.guild else None
        channel_name = getattr(message.channel, "name", "DM")

        # Log all messages to the bot
        if is_dm:
            # Check if the message is a DM
            is_mentioned = True  # Always respond in DMs
        else:
            is_mentioned = self.user.mentioned_in(message)
            is_reply_to_bot = (
                message.reference
                and message.reference.resolved
                and message.reference.resolved.author == self.user
            )
            is_mentioned = is_mentioned or is_reply_to_bot

        if not is_mentioned:
            return

        # Log the incoming message
        event_type = "dm" if is_dm else "message"
        monitor.log(
            event_type,
            message.author.display_name,
            message.content,
            guild_name,
            channel_name,
        )

        # Check permissions for channels
        if not is_dm:
            if ALLOWED_CHANNELS:
                if str(message.channel.id) not in ALLOWED_CHANNELS:
                    return
            if ALLOWED_ROLES and isinstance(message.author, discord.Member):
                user_roles = {str(r.id) for r in message.author.roles}
                if not user_roles.intersection(set(ALLOWED_ROLES)):
                    return

        # Process and respond
        await self._handle_message(message, is_dm)

    async def _handle_message(self, message: DiscordMessage, is_dm: bool):
        """Process message and generate response."""
        guild_name = message.guild.name if message.guild else None
        channel_name = getattr(message.channel, "name", "DM")

        async with message.channel.typing():
            try:
                # Import the full handler from discord_bot
                from discord_bot import ClaraDiscordBot

                # Create a temporary instance to use its methods
                # This is a simplified version - in production, refactor to share code
                bot_instance = ClaraDiscordBot.__new__(ClaraDiscordBot)
                bot_instance.user = self.user
                bot_instance.msg_cache = self.msg_cache
                bot_instance.cache_lock = self.cache_lock
                bot_instance.mm = self.mm

                # Get thread
                thread, thread_owner = await bot_instance._ensure_thread(message, is_dm)

                # Get user info
                user_id = f"discord-{message.author.id}"
                project_id = await bot_instance._ensure_project(user_id)

                # Clean content
                raw_content = bot_instance._clean_content(message.content)

                # Add username prefix for channels
                if not is_dm:
                    user_content = f"[{message.author.display_name}]: {raw_content}"
                else:
                    user_content = raw_content

                # Fetch memories
                db = SessionLocal()
                try:
                    user_mems, proj_mems = self.mm.fetch_mem0_context(
                        user_id, project_id, user_content
                    )
                    recent_msgs = self.mm.get_recent_messages(db, thread.id)
                finally:
                    db.close()

                # Build prompt
                prompt_messages = self.mm.build_prompt(
                    user_mems,
                    proj_mems,
                    thread.session_summary,
                    recent_msgs,
                    user_content,
                )

                # Add Discord context
                discord_context = bot_instance._build_discord_context(
                    message, user_mems, proj_mems, is_dm
                )
                prompt_messages.insert(
                    1, {"role": "system", "content": discord_context}
                )

                # Generate response
                loop = asyncio.get_event_loop()
                llm = make_llm()
                response = await loop.run_in_executor(
                    None, lambda: llm(prompt_messages)
                )

                if response:
                    # Extract files
                    cleaned, files = bot_instance._extract_file_attachments(response)
                    discord_files = bot_instance._create_discord_files(files)

                    # Split and send
                    chunks = bot_instance._split_message(cleaned)
                    for i, chunk in enumerate(chunks):
                        files_to_send = discord_files if i == 0 else []
                        if i == 0:
                            await message.reply(
                                chunk, mention_author=False, files=files_to_send
                            )
                        else:
                            await message.channel.send(chunk)

                    # Log response
                    monitor.log(
                        "response",
                        "Clara",
                        response[:200] + "..." if len(response) > 200 else response,
                        guild_name,
                        channel_name,
                    )

                    # Store exchange
                    await bot_instance._store_exchange(
                        thread_owner,
                        user_id,
                        project_id,
                        thread.id,
                        user_content,
                        response,
                    )

            except Exception as e:
                import traceback

                traceback.print_exc()
                monitor.log("error", "Bot", str(e), guild_name, channel_name)
                await message.reply(
                    f"Sorry, I encountered an error: {str(e)[:100]}",
                    mention_author=False,
                )


async def run_bot():
    """Run the Discord bot."""
    bot = MonitoredClaraBot()
    await bot.start(BOT_TOKEN)


async def run_server():
    """Run the FastAPI server."""
    config = uvicorn.Config(app, host="0.0.0.0", port=MONITOR_PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Run both bot and monitoring server."""
    if not BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN not set")
        return

    print(
        f"[monitor] Starting Clara Discord Monitor on http://localhost:{MONITOR_PORT}"
    )
    print("[monitor] Starting Discord bot...")

    # Run both concurrently
    await asyncio.gather(
        run_bot(),
        run_server(),
    )


if __name__ == "__main__":
    asyncio.run(main())
