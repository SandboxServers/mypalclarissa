"""Telegram bot integration for Clara assistant."""
from __future__ import annotations

import os
import asyncio
import time
import httpx
from datetime import datetime, timezone
from typing import Optional

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db import SessionLocal
from models import Project, Session, Message

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_USERS = os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
USER_ID = os.getenv("USER_ID", "demo-user")
DEFAULT_PROJECT = os.getenv("DEFAULT_PROJECT", "Default Project")

# Internal API URL (same container)
API_BASE_URL = "http://localhost:8000"

# Thread name for Telegram chat
GENERAL_CHAT_TITLE = "General Chat"

# Track last seen message ID to detect new messages from web UI
_last_seen_message_id: Optional[int] = None
_telegram_chat_id: Optional[int] = None  # Store the chat ID for sending messages

# Deduplication: track processed update IDs with timestamps
_processed_updates: dict[int, float] = {}
_DEDUP_WINDOW_SECONDS = 60


def _is_duplicate_update(update_id: int) -> bool:
    """Check if we've already processed this update."""
    now = time.time()

    # Clean old entries
    stale = [uid for uid, ts in _processed_updates.items() if now - ts > _DEDUP_WINDOW_SECONDS]
    for uid in stale:
        del _processed_updates[uid]

    if update_id in _processed_updates:
        print(f"[telegram] Dropping duplicate update_id={update_id}")
        return True

    _processed_updates[update_id] = now
    return False


def ensure_project(name: str) -> str:
    """Ensure project exists and return its ID."""
    db = SessionLocal()
    try:
        proj = db.query(Project).filter_by(owner_id=USER_ID, name=name).first()
        if not proj:
            proj = Project(owner_id=USER_ID, name=name)
            db.add(proj)
            db.commit()
            db.refresh(proj)
        return proj.id
    finally:
        db.close()


def get_or_create_general_chat() -> str:
    """Get or create the 'General Chat' thread. Returns thread ID."""
    project_id = ensure_project(DEFAULT_PROJECT)
    db = SessionLocal()
    try:
        sess = (
            db.query(Session)
            .filter_by(user_id=USER_ID, project_id=project_id, title=GENERAL_CHAT_TITLE)
            .first()
        )

        if not sess:
            sess = Session(
                project_id=project_id,
                user_id=USER_ID,
                title=GENERAL_CHAT_TITLE,
                archived="pinned",
            )
            db.add(sess)
            db.commit()
            db.refresh(sess)
            print(f"[telegram] Created General Chat thread: {sess.id}")

        return sess.id
    finally:
        db.close()


def is_user_allowed(user_id: int, username: str) -> bool:
    """Check if a Telegram user is allowed to use the bot."""
    if not TELEGRAM_ALLOWED_USERS or TELEGRAM_ALLOWED_USERS == [""]:
        return True

    user_id_str = str(user_id)
    for allowed in TELEGRAM_ALLOWED_USERS:
        allowed = allowed.strip()
        if allowed == user_id_str or allowed == username or allowed == f"@{username}":
            return True
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if _is_duplicate_update(update.update_id):
        return

    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    # Store chat ID for sending messages from web UI
    global _telegram_chat_id
    _telegram_chat_id = update.effective_chat.id

    await update.message.reply_text(
        f"Hey {user.first_name}! I'm Clara, your AI assistant.\n\n"
        "Send me a message and I'll respond. Our conversation syncs with the web UI "
        "in the 'General Chat' thread.\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/clear - Clear conversation context"
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear command."""
    if _is_duplicate_update(update.update_id):
        return

    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        return

    context.user_data.clear()
    await update.message.reply_text("Conversation context cleared. Starting fresh!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages - route through /api/chat."""
    if _is_duplicate_update(update.update_id):
        return

    user = update.effective_user
    if not is_user_allowed(user.id, user.username):
        await update.message.reply_text("Sorry, you're not authorized to use this bot.")
        return

    user_message = update.message.text
    if not user_message:
        return

    # Store chat ID for sending messages from web UI
    global _telegram_chat_id
    _telegram_chat_id = update.effective_chat.id

    print(f"[telegram] update_id={update.update_id} from {user.username}: {user_message[:50]}...")

    # Get or create the General Chat thread
    thread_id = get_or_create_general_chat()

    # Send typing indicator
    await update.message.chat.send_action("typing")

    try:
        # Call /api/chat with the message - this handles everything uniformly
        # /api/chat stores both user and assistant messages, so don't call /messages separately
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/chat",
                json={
                    "message": user_message,
                    "thread_id": thread_id,
                    "source": "telegram",
                },
            )

            if response.status_code == 200:
                # Stream or get the response
                data = response.json()
                assistant_response = data.get("content", "Sorry, I couldn't generate a response.")
            else:
                print(f"[telegram] API error: {response.status_code} - {response.text}")
                assistant_response = "Sorry, I encountered an error. Please try again."

        # Send response to Telegram
        max_length = 4096
        if len(assistant_response) <= max_length:
            await update.message.reply_text(assistant_response)
        else:
            for i in range(0, len(assistant_response), max_length):
                await update.message.reply_text(assistant_response[i:i + max_length])

        print(f"[telegram] Sent response ({len(assistant_response)} chars)")

        # Update last seen message ID
        global _last_seen_message_id
        db = SessionLocal()
        try:
            last_msg = (
                db.query(Message)
                .filter_by(session_id=thread_id)
                .order_by(Message.id.desc())
                .first()
            )
            if last_msg:
                _last_seen_message_id = last_msg.id
        finally:
            db.close()

    except Exception as e:
        print(f"[telegram] Error: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(
            "Sorry, I encountered an error processing your message. Please try again."
        )


async def poll_for_web_messages(bot: Bot) -> None:
    """Poll for new messages from web UI and send to Telegram."""
    global _last_seen_message_id, _telegram_chat_id

    if not _telegram_chat_id:
        return  # No chat ID yet - user hasn't started bot

    thread_id = get_or_create_general_chat()

    db = SessionLocal()
    try:
        # Get assistant messages newer than last seen that came from web (not telegram)
        query = db.query(Message).filter_by(session_id=thread_id, role="assistant")

        if _last_seen_message_id:
            query = query.filter(Message.id > _last_seen_message_id)

        new_messages = query.order_by(Message.id.asc()).all()

        for msg in new_messages:
            # Skip messages that originated from Telegram - they're already there
            if msg.source == "telegram":
                _last_seen_message_id = msg.id
                continue

            # Only send web-originated messages to Telegram
            try:
                await bot.send_message(chat_id=_telegram_chat_id, text=msg.content)
                print(f"[telegram] Sent web UI message to Telegram: {msg.content[:50]}...")
            except Exception as e:
                print(f"[telegram] Error sending to Telegram: {e}")

            _last_seen_message_id = msg.id

    finally:
        db.close()


async def polling_loop(bot: Bot) -> None:
    """Background loop to poll for web UI messages."""
    while True:
        try:
            await poll_for_web_messages(bot)
        except Exception as e:
            print(f"[telegram] Polling error: {e}")
        await asyncio.sleep(2)  # Poll every 2 seconds


def create_bot_application() -> Application:
    """Create and configure the Telegram bot application."""
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return application


async def run_bot() -> None:
    """Run the Telegram bot with web UI message polling."""
    print("[telegram] Starting Telegram bot...")
    application = create_bot_application()

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    print("[telegram] Bot is running!")

    # Start polling for web UI messages
    polling_task = asyncio.create_task(polling_loop(application.bot))

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        polling_task.cancel()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def start_bot_sync() -> None:
    """Start the bot synchronously (for use in a thread)."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    asyncio.run(run_bot())
