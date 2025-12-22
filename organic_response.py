"""Organic Response System for Clara Discord bot.

Enables Clara to passively monitor Discord chat and respond organically
when she has something meaningful to contribute, without being @mentioned.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from db import SessionLocal
from models import OrganicResponseLog

# Configuration from environment
ORGANIC_ENABLED = os.getenv("ORGANIC_RESPONSE_ENABLED", "true").lower() == "true"
CONFIDENCE_THRESHOLD = float(os.getenv("ORGANIC_CONFIDENCE_THRESHOLD", "0.75"))
COOLDOWN_MINUTES = int(os.getenv("ORGANIC_COOLDOWN_MINUTES", "10"))
DAILY_LIMIT = int(os.getenv("ORGANIC_DAILY_LIMIT", "10"))
EXCLUDED_CHANNELS = [
    int(x)
    for x in os.getenv("ORGANIC_EXCLUDED_CHANNELS", "").split(",")
    if x.strip()
]
QUIET_HOURS = (23, 7)  # 11pm - 7am


@dataclass
class BufferedMessage:
    """A message in the rolling buffer."""

    content: str
    author: str
    author_id: str
    timestamp: datetime
    message_id: int
    channel_name: str


class MessageBuffer:
    """Rolling window of recent messages per channel."""

    def __init__(self, max_messages: int = 50, max_age_minutes: int = 30):
        self.channels: dict[int, deque[BufferedMessage]] = defaultdict(deque)
        self.max_messages = max_messages
        self.max_age = timedelta(minutes=max_age_minutes)

    def add(self, channel_id: int, msg: BufferedMessage):
        self.channels[channel_id].append(msg)
        self._prune(channel_id)

    def _prune(self, channel_id: int):
        buf = self.channels[channel_id]
        now = datetime.now(UTC)
        # Remove old messages
        while buf and (now - buf[0].timestamp) > self.max_age:
            buf.popleft()
        # Enforce max count
        while len(buf) > self.max_messages:
            buf.popleft()

    def get_recent(self, channel_id: int, count: int = 20) -> list[BufferedMessage]:
        return list(self.channels[channel_id])[-count:]

    def get_formatted(self, channel_id: int, count: int = 20) -> str:
        """Format messages for LLM evaluation."""
        recent = self.get_recent(channel_id, count)
        lines = []
        for m in recent:
            ts = m.timestamp.strftime("%H:%M")
            lines.append(f"[{ts}] {m.author}: {m.content}")
        return "\n".join(lines)


class OrganicResponseLimiter:
    """Rate limiting to prevent over-responding."""

    def __init__(
        self,
        cooldown_minutes: int = COOLDOWN_MINUTES,
        daily_limit: int = DAILY_LIMIT,
    ):
        self.last_organic: dict[int, datetime] = {}
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.daily_limit = daily_limit
        self.daily_count: dict[int, int] = defaultdict(int)
        self.last_reset = datetime.now(UTC).date()

    def _maybe_reset_daily(self):
        today = datetime.now(UTC).date()
        if today > self.last_reset:
            self.daily_count.clear()
            self.last_reset = today

    def can_respond(self, channel_id: int) -> tuple[bool, str]:
        self._maybe_reset_daily()
        now = datetime.now(UTC)
        last = self.last_organic.get(channel_id)

        if last and (now - last) < self.cooldown:
            remaining = self.cooldown - (now - last)
            return False, f"cooldown ({remaining.seconds // 60}m remaining)"
        if self.daily_count[channel_id] >= self.daily_limit:
            return (
                False,
                f"daily_limit ({self.daily_count[channel_id]}/{self.daily_limit})",
            )
        return True, "ok"

    def record_response(self, channel_id: int):
        self.last_organic[channel_id] = datetime.now(UTC)
        self.daily_count[channel_id] += 1


class OrganicResponseManager:
    """Main coordinator for organic responses."""

    def __init__(self, bot):
        self.bot = bot
        self.buffer = MessageBuffer()
        self.limiter = OrganicResponseLimiter()
        self.pending_evaluation: set[int] = set()
        self.evaluation_lock = asyncio.Lock()

    def record_message(self, message) -> str | None:
        """Record a message and check if evaluation should trigger.

        Returns trigger reason if evaluation was queued, None otherwise.
        """
        if not ORGANIC_ENABLED:
            return None

        channel_id = message.channel.id
        if channel_id in EXCLUDED_CHANNELS:
            return None

        # Add to buffer
        self.buffer.add(
            channel_id,
            BufferedMessage(
                content=message.content,
                author=message.author.display_name,
                author_id=str(message.author.id),
                timestamp=message.created_at.replace(tzinfo=UTC),
                message_id=message.id,
                channel_name=getattr(message.channel, "name", "DM"),
            ),
        )

        # Check trigger conditions
        trigger_reason = self._check_triggers(message)
        if trigger_reason:
            self.pending_evaluation.add(channel_id)
            return trigger_reason
        return None

    def _check_triggers(self, message) -> str | None:
        """Check if this message should trigger evaluation."""
        channel_id = message.channel.id
        recent = self.buffer.get_recent(channel_id, count=2)

        # Trigger: conversational lull (2+ min gap)
        if len(recent) >= 2:
            gap = recent[-1].timestamp - recent[-2].timestamp
            if gap > timedelta(minutes=2):
                return "lull"

        # Trigger: specific phrases
        content_lower = message.content.lower()
        trigger_phrases = [
            "anyone know",
            "does anyone",
            "help",
            "stuck",
            "confused",
            "frustrated",
            "excited",
            "finally",
            "check this out",
            "what do you think",
            "thoughts?",
        ]
        if any(phrase in content_lower for phrase in trigger_phrases):
            return "trigger_phrase"

        # Trigger: every 10th message
        if len(self.buffer.channels[channel_id]) % 10 == 0:
            return "periodic"

        return None

    def is_quiet_hours(self) -> bool:
        """Check if we're in quiet hours (no organic responses)."""
        hour = datetime.now().hour
        start, end = QUIET_HOURS
        if start > end:  # Crosses midnight
            return hour >= start or hour < end
        return start <= hour < end

    def build_evaluation_prompt(
        self, formatted_msgs: str, memories: str
    ) -> list[dict]:
        """Build the evaluation prompt for Claude."""
        system = """You are Clara, passively monitoring a Discord conversation.
You were NOT mentioned.

Your task: Decide if you should respond organically (without being asked).

## Guidelines:
RESPOND when:
- You have genuine insight or information to add
- Someone seems to be struggling and you can help
- There's a meaningful callback to a previous conversation
- Natural humor that fits the moment
- Greeting someone you know who just arrived

STAY SILENT when:
- The conversation is flowing fine without you
- Your input would be generic or obvious
- You've spoken recently (unprompted)
- It feels like a private moment between others
- Adding "help" that wasn't requested

The goal is presence, not participation. Restraint is the feature.

## Response Format (JSON only, no other text):
{
    "should_respond": true/false,
    "confidence": 0.0-1.0,
    "reason": "one sentence explanation",
    "response_type": "insight|support|correction|humor|callback|greeting|null",
    "draft_response": "what you'd say (or null if not responding)"
}"""

        user_content = f"""## Recent Conversation:
{formatted_msgs}

## Relevant Memories:
{memories}

Evaluate whether to respond. Output JSON only."""

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    def parse_evaluation(self, raw: str) -> dict:
        """Parse LLM evaluation response."""
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
        return {"should_respond": False, "reason": "parse_error", "confidence": 0}

    def log_evaluation(
        self,
        channel_id: int,
        channel_name: str,
        guild_id: str | None,
        trigger_reason: str,
        context: str,
        result: dict,
        response_sent: bool,
    ):
        """Log evaluation to database."""
        db = SessionLocal()
        try:
            log = OrganicResponseLog(
                channel_id=str(channel_id),
                channel_name=channel_name,
                guild_id=guild_id,
                trigger_reason=trigger_reason,
                message_context=context[:2000],  # Truncate for storage
                should_respond=result.get("should_respond", False),
                confidence=result.get("confidence", 0),
                response_type=result.get("response_type"),
                reason=result.get("reason", ""),
                response_text=result.get("draft_response"),
                response_sent=response_sent,
            )
            if response_sent:
                log.responded_at = datetime.now(UTC)
            db.add(log)
            db.commit()
        except Exception as e:
            print(f"[organic] Error logging evaluation: {e}")
            db.rollback()
        finally:
            db.close()
