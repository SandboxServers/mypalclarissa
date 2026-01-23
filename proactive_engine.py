"""Proactive Conversation Engine.

Background service that allows Clara to initiate conversations when appropriate.
Runs periodically, gathers context, and lets Clara decide whether to reach out.

Philosophy: Reach out when there's genuine reason - not on a schedule.
Feel like a thoughtful friend who knows when to check in vs. leave you alone.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from config.logging import get_logger
from db.channel_config import is_ors_enabled
from db.connection import SessionLocal
from db.models import (
    ProactiveMessage,
    ProactiveNote,
    UserInteractionPattern,
)

if TYPE_CHECKING:
    from discord import Client

logger = get_logger("proactive")

# Configuration
PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "false").lower() == "true"
PROACTIVE_POLL_MINUTES = int(os.getenv("PROACTIVE_POLL_MINUTES", "15"))
PROACTIVE_MIN_GAP_HOURS = float(os.getenv("PROACTIVE_MIN_GAP_HOURS", "2"))
PROACTIVE_ACTIVE_DAYS = int(os.getenv("PROACTIVE_ACTIVE_DAYS", "7"))

# Decision types
DecisionType = Literal["SPEAK", "WAIT", "NOTE"]

# Priority levels and their thresholds
PRIORITY_THRESHOLDS = {
    "low": {"min_gap_hours": 4, "active_hours_only": True},
    "normal": {"min_gap_hours": 2, "active_hours_only": True},
    "high": {"min_gap_hours": 1, "active_hours_only": False},
    "critical": {"min_gap_hours": 0.5, "active_hours_only": False},
}

# Proactive decision prompt
PROACTIVE_DECISION_PROMPT = """You are Clara, a thoughtful AI assistant deciding whether to proactively reach out to a user.

## Your Philosophy
- Reach out when there's genuine reason, not on a schedule
- Be like a thoughtful friend who knows when to check in vs. leave alone
- Consider: Would a caring friend reach out right now? Why or why not?

## Context
Current time: {current_time}
User timezone estimate: {timezone_estimate}

### Last Interaction
- When: {last_interaction_time} ({time_since_last} ago)
- Channel: {last_channel}
- Summary: {last_summary}
- Energy level: {last_energy}
- Explicit signals: {explicit_signals}

### Upcoming Calendar Events (next 24h)
{calendar_events}

### Pending Notes/Items
{pending_notes}

### Interaction Patterns
- Typical active hours: {active_hours}
- Average response time: {avg_response_time}
- Proactive success rate: {success_rate}%
- Current silence streak: {silence_hours} hours

### Recent Proactive History
{proactive_history}

## Decision Framework

**SPEAK** - Send a message now. Use when:
- There's something genuinely time-relevant (upcoming event, deadline)
- You noticed something worth following up on
- The user seemed to need support and enough time has passed
- There's a natural conversation opener

**WAIT** - Not the right time. Use when:
- It's outside their typical active hours
- Too soon since last interaction
- No compelling reason to reach out
- Recent proactive messages were ignored

**NOTE** - Store for later. Use when:
- You have something to bring up, but timing isn't right
- You want to remember to check on something later
- There's context worth preserving for future

## Response Format
Respond with a JSON object:
```json
{{
  "decision": "SPEAK" | "WAIT" | "NOTE",
  "priority": "low" | "normal" | "high" | "critical",
  "content": "Message to send (for SPEAK) or note to save (for NOTE)",
  "reason": "Brief explanation of your decision",
  "check_again_in_minutes": 60
}}
```

Make your decision:"""


def is_enabled() -> bool:
    """Check if proactive engine is enabled."""
    return PROACTIVE_ENABLED


async def get_user_patterns(user_id: str) -> UserInteractionPattern | None:
    """Get user interaction patterns from database."""
    db = SessionLocal()
    try:
        return db.query(UserInteractionPattern).filter_by(user_id=user_id).first()
    finally:
        db.close()


async def update_user_patterns(
    user_id: str,
    channel_id: str | None = None,
    summary: str | None = None,
    energy: str | None = None,
) -> None:
    """Update user interaction patterns after an interaction."""
    db = SessionLocal()
    try:
        patterns = db.query(UserInteractionPattern).filter_by(user_id=user_id).first()

        if not patterns:
            patterns = UserInteractionPattern(user_id=user_id)
            db.add(patterns)

        patterns.last_interaction_at = datetime.now(UTC).replace(tzinfo=None)

        if channel_id:
            patterns.last_interaction_channel = channel_id
        if summary:
            patterns.last_interaction_summary = summary
        if energy:
            patterns.last_interaction_energy = energy

        db.commit()
        logger.debug(f"Updated interaction patterns for {user_id}")

    finally:
        db.close()


async def record_proactive_message(
    user_id: str,
    channel_id: str,
    message: str,
    priority: str,
    reason: str | None = None,
) -> str:
    """Record a proactive message in the database."""
    db = SessionLocal()
    try:
        record = ProactiveMessage(
            user_id=user_id,
            channel_id=channel_id,
            message=message,
            priority=priority,
            reason=reason,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record.id
    finally:
        db.close()


async def record_proactive_response(message_id: str) -> None:
    """Mark that a proactive message received a response."""
    db = SessionLocal()
    try:
        record = db.query(ProactiveMessage).filter_by(id=message_id).first()
        if record:
            record.response_received = "true"
            record.response_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()
    finally:
        db.close()


async def save_proactive_note(
    user_id: str,
    note: str,
    surface_at: datetime | None = None,
) -> None:
    """Save a note for later surfacing."""
    db = SessionLocal()
    try:
        record = ProactiveNote(
            user_id=user_id,
            note=note,
            surface_at=surface_at.replace(tzinfo=None) if surface_at else None,
        )
        db.add(record)
        db.commit()
        logger.info(f"Saved proactive note for {user_id}")
    finally:
        db.close()


async def get_pending_notes(user_id: str) -> list[dict]:
    """Get pending notes for a user."""
    db = SessionLocal()
    try:
        notes = (
            db.query(ProactiveNote)
            .filter_by(user_id=user_id, surfaced="false")
            .order_by(ProactiveNote.created_at.desc())
            .limit(10)
            .all()
        )
        return [
            {
                "id": n.id,
                "note": n.note,
                "surface_at": n.surface_at.isoformat() if n.surface_at else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notes
        ]
    finally:
        db.close()


async def get_recent_proactive_history(user_id: str, limit: int = 5) -> list[dict]:
    """Get recent proactive messages sent to a user."""
    db = SessionLocal()
    try:
        messages = (
            db.query(ProactiveMessage)
            .filter_by(user_id=user_id)
            .order_by(ProactiveMessage.sent_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                "priority": m.priority,
                "message": m.message[:100] + "..."
                if len(m.message) > 100
                else m.message,
                "responded": m.response_received == "true",
            }
            for m in messages
        ]
    finally:
        db.close()


async def get_last_proactive_time(user_id: str) -> datetime | None:
    """Get the timestamp of the last proactive message to a user."""
    db = SessionLocal()
    try:
        last = (
            db.query(ProactiveMessage)
            .filter_by(user_id=user_id)
            .order_by(ProactiveMessage.sent_at.desc())
            .first()
        )
        return last.sent_at if last else None
    finally:
        db.close()


async def get_active_users(days: int = 7) -> list[str]:
    """Get user IDs with activity in the last N days."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        patterns = (
            db.query(UserInteractionPattern)
            .filter(UserInteractionPattern.last_interaction_at >= cutoff)
            .all()
        )
        return [p.user_id for p in patterns]
    finally:
        db.close()


async def build_context_payload(
    user_id: str,
    calendar_events: list[dict] | None = None,
) -> dict[str, Any]:
    """Build the context payload for a proactive decision."""
    patterns = await get_user_patterns(user_id)
    pending_notes = await get_pending_notes(user_id)
    proactive_history = await get_recent_proactive_history(user_id)

    now = datetime.now(UTC)

    # Calculate time since last interaction
    if patterns and patterns.last_interaction_at:
        last_dt = patterns.last_interaction_at
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        time_since = now - last_dt
        hours_since = time_since.total_seconds() / 3600
        time_since_str = (
            f"{int(hours_since)} hours"
            if hours_since >= 1
            else f"{int(time_since.total_seconds() / 60)} minutes"
        )
    else:
        time_since_str = "unknown"
        hours_since = float("inf")

    # Parse explicit signals
    explicit_signals = {}
    if patterns and patterns.explicit_signals:
        try:
            explicit_signals = json.loads(patterns.explicit_signals)
        except json.JSONDecodeError:
            pass

    # Parse active hours
    active_hours = "unknown"
    if patterns and patterns.typical_active_hours:
        try:
            hours = json.loads(patterns.typical_active_hours)
            active_hours = json.dumps(hours)
        except json.JSONDecodeError:
            pass

    return {
        "current_time": now.isoformat(),
        "timezone_estimate": "UTC",  # TODO: infer from patterns
        "last_interaction_time": patterns.last_interaction_at.isoformat()
        if patterns and patterns.last_interaction_at
        else "never",
        "time_since_last": time_since_str,
        "last_channel": patterns.last_interaction_channel if patterns else "unknown",
        "last_summary": patterns.last_interaction_summary
        if patterns
        else "No previous interaction",
        "last_energy": patterns.last_interaction_energy if patterns else "unknown",
        "explicit_signals": json.dumps(explicit_signals)
        if explicit_signals
        else "none",
        "calendar_events": json.dumps(calendar_events)
        if calendar_events
        else "No calendar access",
        "pending_notes": json.dumps(pending_notes) if pending_notes else "None",
        "active_hours": active_hours,
        "avg_response_time": f"{patterns.avg_response_time_seconds // 60} minutes"
        if patterns and patterns.avg_response_time_seconds
        else "unknown",
        "success_rate": patterns.proactive_success_rate
        if patterns and patterns.proactive_success_rate
        else 50,
        "silence_hours": round(hours_since, 1)
        if hours_since != float("inf")
        else "unknown",
        "proactive_history": json.dumps(proactive_history)
        if proactive_history
        else "None",
    }


async def make_proactive_decision(
    user_id: str,
    llm_callable: Any,
    calendar_events: list[dict] | None = None,
) -> dict[str, Any] | None:
    """Ask Clara to make a proactive decision for a user.

    Args:
        user_id: User to check
        llm_callable: LLM function to call
        calendar_events: Upcoming calendar events (if available)

    Returns:
        Decision dict or None if error
    """
    context = await build_context_payload(user_id, calendar_events)

    prompt = PROACTIVE_DECISION_PROMPT.format(**context)

    try:
        messages = [{"role": "user", "content": prompt}]

        # Run sync LLM call in thread pool
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, llm_callable, messages)

        # Parse JSON response
        response_text = response.strip()

        # Extract JSON from response (handle markdown code blocks)
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()

        decision = json.loads(response_text)
        logger.info(f"Proactive decision for {user_id}: {decision.get('decision')}")
        return decision

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse proactive decision: {e}")
        return None
    except Exception as e:
        logger.error(f"Error making proactive decision: {e}")
        return None


async def check_rate_limit(user_id: str, priority: str) -> bool:
    """Check if we can send a proactive message based on rate limits."""
    last_time = await get_last_proactive_time(user_id)

    if not last_time:
        return True

    # Get threshold for priority
    threshold = PRIORITY_THRESHOLDS.get(priority, PRIORITY_THRESHOLDS["normal"])
    min_gap_hours = threshold["min_gap_hours"]

    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=UTC)

    time_since = datetime.now(UTC) - last_time
    hours_since = time_since.total_seconds() / 3600

    if hours_since < min_gap_hours:
        logger.debug(
            f"Rate limited: {hours_since:.1f}h < {min_gap_hours}h min gap for {priority}"
        )
        return False

    return True


async def execute_speak_decision(
    client: "Client",
    user_id: str,
    decision: dict[str, Any],
) -> bool:
    """Execute a SPEAK decision by sending a message.

    Args:
        client: Discord client
        user_id: User ID
        decision: Decision dict with content, priority, reason

    Returns:
        True if message sent successfully
    """
    patterns = await get_user_patterns(user_id)

    if not patterns or not patterns.last_interaction_channel:
        logger.warning(f"No channel found for user {user_id}")
        return False

    channel_id_str = patterns.last_interaction_channel

    # Check if ORS is enabled for this channel (only for guild channels)
    if channel_id_str.startswith("discord-channel-"):
        numeric_id = channel_id_str.replace("discord-channel-", "")
        if not is_ors_enabled(numeric_id):
            logger.debug(f"ORS disabled for channel {numeric_id}, skipping proactive message")
            return False

    # Extract numeric channel ID
    if channel_id_str.startswith("discord-channel-"):
        channel_id = int(channel_id_str.replace("discord-channel-", ""))
    elif channel_id_str.startswith("discord-dm-"):
        # For DMs, we need to get the user and create a DM channel
        discord_user_id = int(channel_id_str.replace("discord-dm-", ""))
        try:
            user = await client.fetch_user(discord_user_id)
            channel = await user.create_dm()
            channel_id = channel.id
        except Exception as e:
            logger.error(f"Failed to create DM channel: {e}")
            return False
    else:
        logger.warning(f"Unknown channel format: {channel_id_str}")
        return False

    try:
        channel = client.get_channel(channel_id)
        if not channel:
            channel = await client.fetch_channel(channel_id)

        if not channel:
            logger.warning(f"Could not find channel {channel_id}")
            return False

        content = decision.get("content", "Hey, just checking in!")
        priority = decision.get("priority", "normal")
        reason = decision.get("reason")

        # Send the message
        await channel.send(content)

        # Record the proactive message
        await record_proactive_message(
            user_id=user_id,
            channel_id=channel_id_str,
            message=content,
            priority=priority,
            reason=reason,
        )

        logger.info(
            f"Sent proactive message to {user_id} ({priority}): {content[:50]}..."
        )
        return True

    except Exception as e:
        logger.error(f"Failed to send proactive message: {e}")
        return False


async def proactive_check_loop(client: "Client", llm_callable: Any):
    """Background loop that periodically checks for proactive opportunities.

    Args:
        client: Discord client
        llm_callable: LLM function to use for decisions
    """
    if not is_enabled():
        logger.info("Proactive engine disabled")
        return

    logger.info(
        f"Proactive engine started (poll every {PROACTIVE_POLL_MINUTES} minutes)"
    )

    while True:
        try:
            await asyncio.sleep(PROACTIVE_POLL_MINUTES * 60)

            if not client.is_ready():
                logger.debug("Client not ready, skipping proactive check")
                continue

            # Get active users
            active_users = await get_active_users(PROACTIVE_ACTIVE_DAYS)
            logger.debug(f"Checking {len(active_users)} active users")

            for user_id in active_users:
                try:
                    # TODO: Fetch calendar events if user has Google connected
                    calendar_events = None

                    # Make decision
                    decision = await make_proactive_decision(
                        user_id, llm_callable, calendar_events
                    )

                    if not decision:
                        continue

                    decision_type = decision.get("decision", "WAIT")
                    priority = decision.get("priority", "normal")

                    if decision_type == "SPEAK":
                        # Check rate limits
                        if not await check_rate_limit(user_id, priority):
                            logger.debug(f"Rate limited for {user_id}")
                            continue

                        await execute_speak_decision(client, user_id, decision)

                    elif decision_type == "NOTE":
                        note = decision.get("content", "")
                        if note:
                            await save_proactive_note(user_id, note)

                    # WAIT: do nothing

                except Exception as e:
                    logger.error(f"Error processing user {user_id}: {e}")
                    continue

        except asyncio.CancelledError:
            logger.info("Proactive engine shutting down")
            break
        except Exception as e:
            logger.error(f"Error in proactive check loop: {e}")
            # Continue loop on error
            await asyncio.sleep(60)


# =============================================================================
# Integration helpers (call from discord_bot.py)
# =============================================================================


async def on_user_message(
    user_id: str,
    channel_id: str,
    message_preview: str | None = None,
):
    """Called when a user sends a message. Updates interaction patterns.

    Args:
        user_id: Discord user ID (e.g., "discord-123456")
        channel_id: Channel ID (e.g., "discord-channel-789" or "discord-dm-123")
        message_preview: Brief preview of the message (for context)
    """
    if not is_enabled():
        return

    await update_user_patterns(
        user_id=user_id,
        channel_id=channel_id,
        summary=message_preview,
    )


async def on_user_response_to_proactive(message_id: str):
    """Called when a user responds to a proactive message.

    Args:
        message_id: ID of the proactive message record
    """
    if not is_enabled():
        return

    await record_proactive_response(message_id)
