#!/usr/bin/env python3
"""Backfill emotional context from conversation history.

This script initializes emotional context data from existing conversations
to bootstrap the emotional tracking system for existing users.

Supports two data sources:
1. Database: Existing Session/Message records
2. Discord Export: JSON files from DiscordChatExporter

Usage:
    python scripts/backfill_emotional.py --dry-run      # Preview changes
    python scripts/backfill_emotional.py                # Process all DB users
    python scripts/backfill_emotional.py --user USER_ID # Process one user
    python scripts/backfill_emotional.py --discord-export /path/to/export.json
    python scripts/backfill_emotional.py --discord-export /path/to/exports/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, ".")

from config.logging import get_logger, init_logging

init_logging()
logger = get_logger("backfill_emotional")


# =============================================================================
# Discord Export Parsing
# =============================================================================


def parse_discord_export(file_path: Path) -> dict:
    """Parse a Discord export JSON file.

    Returns dict with channel info and messages grouped by author.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {file_path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return {}

    # Extract channel info
    channel = data.get("channel", {})
    channel_id = channel.get("id", "unknown")
    channel_name = channel.get("name", "unknown")
    channel_type = channel.get("type", "")
    is_dm = "Direct" in channel_type or channel_type == "DirectTextChat"

    # Group messages by author
    messages_by_user: dict[str, list[dict]] = {}

    for msg in data.get("messages", []):
        # Skip bot messages and system messages
        author = msg.get("author", {})
        if author.get("isBot", False):
            continue
        if msg.get("type") not in ("Default", "Reply"):
            continue

        content = msg.get("content", "").strip()
        if not content:
            continue

        user_id = author.get("id", "unknown")
        username = author.get("name", "unknown")

        # Parse timestamp
        timestamp_str = msg.get("timestamp", "")
        try:
            # Handle various timestamp formats
            if "+" in timestamp_str or timestamp_str.endswith("Z"):
                # ISO format with timezone
                timestamp_str = timestamp_str.replace("Z", "+00:00")
                timestamp = datetime.fromisoformat(timestamp_str)
            else:
                timestamp = datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            timestamp = datetime.utcnow()

        if user_id not in messages_by_user:
            messages_by_user[user_id] = []

        messages_by_user[user_id].append({
            "content": content,
            "created_at": timestamp,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "is_dm": is_dm,
            "username": username,
        })

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "is_dm": is_dm,
        "messages_by_user": messages_by_user,
        "file": str(file_path),
    }


def find_discord_exports(path: Path) -> list[Path]:
    """Find all Discord export JSON files in a path.

    Accepts either a single JSON file or a directory containing JSON files.
    """
    if path.is_file():
        if path.suffix.lower() == ".json":
            return [path]
        else:
            logger.warning(f"Not a JSON file: {path}")
            return []

    if path.is_dir():
        # Find all JSON files recursively
        json_files = list(path.glob("**/*.json"))
        logger.info(f"Found {len(json_files)} JSON files in {path}")
        return sorted(json_files)

    logger.error(f"Path does not exist: {path}")
    return []


def process_discord_exports(
    export_path: Path,
    dry_run: bool = True,
    user_filter: str | None = None,
    days: int | None = None,
) -> dict:
    """Process Discord export files and backfill emotional context.

    Args:
        export_path: Path to JSON file or directory of exports
        dry_run: If True, preview without making changes
        user_filter: Optional Discord user ID to filter
        days: Optional limit to messages within N days

    Returns:
        Stats dict with processing results
    """
    total_stats = {
        "files_processed": 0,
        "users_processed": 0,
        "messages_analyzed": 0,
        "emotional_entries_created": 0,
        "errors": 0,
    }

    # Find export files
    export_files = find_discord_exports(export_path)
    if not export_files:
        logger.error("No Discord export files found")
        return total_stats

    # Calculate date cutoff if specified
    cutoff_date = None
    if days:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        logger.info(f"Filtering to messages after {cutoff_date.isoformat()}")

    # Track all users across files
    all_users: dict[str, list[dict]] = {}

    # Parse all export files
    for export_file in export_files:
        logger.info(f"Parsing: {export_file}")
        parsed = parse_discord_export(export_file)

        if not parsed:
            total_stats["errors"] += 1
            continue

        total_stats["files_processed"] += 1
        channel_name = parsed.get("channel_name", "unknown")
        is_dm = parsed.get("is_dm", False)

        for user_id, messages in parsed.get("messages_by_user", {}).items():
            # Apply user filter if specified
            if user_filter and user_id != user_filter:
                continue

            # Filter by date if specified
            if cutoff_date:
                messages = [
                    m for m in messages
                    if m["created_at"].replace(tzinfo=None) >= cutoff_date
                ]

            if not messages:
                continue

            # Add channel context to messages
            for msg in messages:
                msg["channel_name"] = channel_name
                msg["is_dm"] = is_dm

            # Aggregate messages by user across files
            if user_id not in all_users:
                all_users[user_id] = []
            all_users[user_id].extend(messages)

    # Process each user's aggregated messages
    for user_id, messages in all_users.items():
        # Sort by timestamp
        messages.sort(key=lambda m: m["created_at"])

        username = messages[0].get("username", "unknown") if messages else "unknown"
        logger.info(f"Processing Discord user: {username} ({user_id})")
        logger.info(f"  Found {len(messages)} messages across exports")

        # Use discord-prefixed user ID for clarity
        clarissa_user_id = f"discord-{user_id}"

        stats = backfill_user_emotional_context(
            user_id=clarissa_user_id,
            messages=messages,
            dry_run=dry_run,
            source="discord_export",
        )

        total_stats["users_processed"] += 1
        total_stats["messages_analyzed"] += stats["messages_analyzed"]
        total_stats["emotional_entries_created"] += stats["emotional_entries_created"]
        total_stats["errors"] += stats["errors"]

    return total_stats


# =============================================================================
# Database Processing
# =============================================================================


def get_db_session():
    """Get a database session."""
    try:
        from db import SessionLocal

        return SessionLocal()
    except Exception as e:
        logger.error(f"Failed to get database session: {e}")
        return None


def get_users_with_conversations(db) -> list[str]:
    """Get list of user IDs with conversation history."""
    try:
        from db.models import Session

        # Get unique user IDs from sessions
        results = db.query(Session.user_id).distinct().all()
        return [r[0] for r in results]
    except Exception as e:
        logger.error(f"Failed to get users: {e}")
        return []


def get_recent_messages_for_user(
    db,
    user_id: str,
    days: int = 30,
    limit: int = 100,
) -> list[dict]:
    """Get recent messages for a user."""
    try:
        from db.models import Message, Session

        cutoff = datetime.utcnow() - timedelta(days=days)

        messages = (
            db.query(Message)
            .join(Session)
            .filter(Session.user_id == user_id)
            .filter(Message.created_at >= cutoff)
            .filter(Message.role == "user")  # Only user messages for sentiment
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                "content": m.content,
                "created_at": m.created_at,
                "session_id": m.session_id,
            }
            for m in messages
        ]
    except Exception as e:
        logger.error(f"Failed to get messages for {user_id}: {e}")
        return []


# =============================================================================
# Sentiment Analysis
# =============================================================================


def analyze_message_sentiment(content: str) -> dict:
    """Analyze sentiment of a message.

    Returns a dict with sentiment analysis results.
    """
    try:
        from clarissa_core.sentiment import analyze_sentiment

        result = analyze_sentiment(content)
        # Map compound score to energy level
        compound = result.get("compound", 0)
        if compound >= 0.5:
            energy = "excited"
        elif compound >= 0.2:
            energy = "positive"
        elif compound <= -0.5:
            energy = "stressed"
        elif compound <= -0.2:
            energy = "negative"
        else:
            energy = "neutral"

        return {
            "energy": energy,
            "compound": compound,
            "confidence": 0.8,
        }
    except ImportError:
        # Fallback to simple heuristics
        content_lower = content.lower()

        # Simple keyword-based sentiment
        positive_words = ["thanks", "great", "awesome", "happy", "love", "good"]
        negative_words = ["frustrated", "angry", "upset", "hate", "bad", "annoyed"]
        stressed_words = [
            "urgent",
            "deadline",
            "asap",
            "stressed",
            "busy",
            "overwhelmed",
        ]

        positive_count = sum(1 for w in positive_words if w in content_lower)
        negative_count = sum(1 for w in negative_words if w in content_lower)
        stressed_count = sum(1 for w in stressed_words if w in content_lower)

        if stressed_count > 0:
            energy = "stressed"
        elif positive_count > negative_count:
            energy = "positive"
        elif negative_count > positive_count:
            energy = "negative"
        else:
            energy = "neutral"

        return {
            "energy": energy,
            "confidence": 0.5,  # Low confidence for heuristic
        }


# =============================================================================
# Emotional Context Backfill
# =============================================================================


def backfill_user_emotional_context(
    user_id: str,
    messages: list[dict],
    dry_run: bool = True,
    source: str = "database",
) -> dict:
    """Backfill emotional context for a user.

    Args:
        user_id: User identifier
        messages: List of message dicts with 'content' and 'created_at'
        dry_run: If True, preview without making changes
        source: Data source identifier ("database" or "discord_export")

    Returns:
        Stats about what was processed.
    """
    stats = {
        "messages_analyzed": 0,
        "emotional_entries_created": 0,
        "errors": 0,
    }

    if not messages:
        return stats

    # Analyze messages and track sentiment over time
    sentiments = []
    for msg in messages:
        try:
            sentiment = analyze_message_sentiment(msg["content"])
            sentiments.append({
                "timestamp": msg["created_at"],
                "energy": sentiment.get("energy", "neutral"),
                "compound": sentiment.get("compound", 0),
                "content_preview": msg["content"][:50],
                "channel_id": msg.get("channel_id", source),
                "channel_name": msg.get("channel_name", source),
                "is_dm": msg.get("is_dm", False),
            })
            stats["messages_analyzed"] += 1
        except Exception as e:
            logger.warning(f"Failed to analyze message: {e}")
            stats["errors"] += 1

    if dry_run:
        logger.info(
            f"  [DRY RUN] Would create emotional context from "
            f"{len(sentiments)} messages"
        )
        # Show sample of sentiments
        for s in sentiments[:3]:
            logger.info(f"    - {s['energy']}: {s['content_preview']}...")
        if len(sentiments) > 3:
            logger.info(f"    ... and {len(sentiments) - 3} more")
        return stats

    # Store to emotional context system
    try:
        from clarissa_core.emotional_context import track_message_sentiment

        for s in sentiments:
            track_message_sentiment(
                user_id=user_id,
                channel_id=s["channel_id"],
                message_content=s["content_preview"],
            )
            stats["emotional_entries_created"] += 1

        logger.info(f"  Created {stats['emotional_entries_created']} entries")

    except ImportError as e:
        logger.warning(f"emotional_context module not available: {e}")
        import traceback
        logger.debug(traceback.format_exc())
    except Exception as e:
        logger.error(f"Failed to store emotional context: {e}")
        import traceback
        logger.error(traceback.format_exc())
        stats["errors"] += 1

    return stats


# =============================================================================
# Main
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Backfill emotional context from conversation history"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--user",
        help="Process only this user ID (DB user or Discord user ID)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Look back this many days (default: 30)",
    )
    parser.add_argument(
        "--discord-export",
        type=Path,
        help="Path to Discord export JSON file or directory of exports",
    )
    parser.add_argument(
        "--logs",
        type=Path,
        help="Alias for --discord-export (backwards compatibility)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--check-imports",
        action="store_true",
        help="Check if all required modules can be imported",
    )
    args = parser.parse_args()

    # Set debug logging if requested
    if args.debug:
        import logging
        logging.getLogger("backfill_emotional").setLevel(logging.DEBUG)

    # Check imports if requested
    if args.check_imports:
        logger.info("Checking imports...")
        try:
            from clarissa_core.emotional_context import (
                track_message_sentiment,  # noqa: F401
            )
            logger.info("  emotional_context: OK")
        except Exception as e:
            logger.error(f"  emotional_context: FAILED - {e}")
        try:
            from clarissa_core.sentiment import analyze_sentiment  # noqa: F401
            logger.info("  sentiment: OK")
        except Exception as e:
            logger.error(f"  sentiment: FAILED - {e}")
        try:
            from db import SessionLocal  # noqa: F401
            logger.info("  db: OK")
        except Exception as e:
            logger.error(f"  db: FAILED - {e}")
        return

    # Handle --logs as alias for --discord-export
    discord_export_path = args.discord_export or args.logs

    logger.info("=" * 60)
    logger.info("Clarissa Emotional Context Backfill")
    logger.info(f"Started at: {datetime.now().isoformat()}")
    if args.dry_run:
        logger.info("MODE: DRY RUN (no changes will be made)")
    if discord_export_path:
        logger.info(f"SOURCE: Discord Export ({discord_export_path})")
    else:
        logger.info("SOURCE: Database")
    logger.info("=" * 60)

    # Process Discord exports if specified
    if discord_export_path:
        total_stats = process_discord_exports(
            export_path=discord_export_path,
            dry_run=args.dry_run,
            user_filter=args.user,
            days=args.days,
        )

        logger.info("=" * 60)
        logger.info("Backfill Summary (Discord Export)")
        logger.info(f"  Files processed: {total_stats['files_processed']}")
        logger.info(f"  Users processed: {total_stats['users_processed']}")
        logger.info(f"  Messages analyzed: {total_stats['messages_analyzed']}")
        logger.info(f"  Entries created: {total_stats['emotional_entries_created']}")
        logger.info(f"  Errors: {total_stats['errors']}")
        logger.info("=" * 60)
        return

    # Otherwise, process from database
    db = get_db_session()
    if db is None:
        logger.error("Failed to connect to database")
        sys.exit(1)

    try:
        # Get users to process
        if args.user:
            users = [args.user]
        else:
            users = get_users_with_conversations(db)

        logger.info(f"Found {len(users)} users to process")

        total_stats = {
            "users_processed": 0,
            "messages_analyzed": 0,
            "emotional_entries_created": 0,
            "errors": 0,
        }

        for user_id in users:
            logger.info(f"Processing user: {user_id}")

            # Get recent messages
            messages = get_recent_messages_for_user(db, user_id, days=args.days)
            logger.info(f"  Found {len(messages)} messages in last {args.days} days")

            if not messages:
                continue

            # Backfill emotional context
            stats = backfill_user_emotional_context(
                user_id, messages, args.dry_run, source="database"
            )

            total_stats["users_processed"] += 1
            total_stats["messages_analyzed"] += stats["messages_analyzed"]
            total_stats["emotional_entries_created"] += stats[
                "emotional_entries_created"
            ]
            total_stats["errors"] += stats["errors"]

        logger.info("=" * 60)
        logger.info("Backfill Summary (Database)")
        logger.info(f"  Users processed: {total_stats['users_processed']}")
        logger.info(f"  Messages analyzed: {total_stats['messages_analyzed']}")
        logger.info(f"  Entries created: {total_stats['emotional_entries_created']}")
        logger.info(f"  Errors: {total_stats['errors']}")
        logger.info("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
