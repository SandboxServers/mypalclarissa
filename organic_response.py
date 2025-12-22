"""Organic Response System for Clara Discord bot.

Enables Clara to passively monitor Discord chat and respond organically
when she has something meaningful to contribute, without being @mentioned.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from bot_config import get_organic_decision_prompt, get_organic_personality, get_organic_response_prompt
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
# Quiet hours: disable organic responses during certain hours (e.g., nighttime)
QUIET_HOURS_ENABLED = os.getenv("ORGANIC_QUIET_HOURS_ENABLED", "false").lower() == "true"
QUIET_HOURS_START = int(os.getenv("ORGANIC_QUIET_HOURS_START", "23"))  # 11pm
QUIET_HOURS_END = int(os.getenv("ORGANIC_QUIET_HOURS_END", "7"))  # 7am


@dataclass
class BufferedMessage:
    """A message in the rolling buffer."""

    content: str
    author: str
    author_id: str
    timestamp: datetime
    message_id: int
    channel_name: str
    images: list[str] = field(default_factory=list)


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
            content = m.content
            # Indicate images/GIFs in the text
            if m.images:
                img_count = len(m.images)
                img_note = f" [shared {img_count} image{'s' if img_count > 1 else ''}/GIF]"
                content += img_note
            lines.append(f"[{ts}] {m.author}: {content}")
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

    def can_respond(
        self, channel_id: int, bypass_cooldown: bool = False
    ) -> tuple[bool, str]:
        self._maybe_reset_daily()
        now = datetime.now(UTC)
        last = self.last_organic.get(channel_id)

        # Cooldown can be bypassed for contextual replies
        if not bypass_cooldown and last and (now - last) < self.cooldown:
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
        # Store (channel_id, trigger_reason) tuples
        self.pending_evaluation: dict[int, str] = {}
        self.evaluation_lock = asyncio.Lock()
        # Track Flo's last message per channel for contextual reply detection
        self.last_bot_message: dict[int, str] = {}
        # Evaluation cooldown per channel (separate from response cooldown)
        # Prevents evaluating every single message in rapid chat
        self.last_evaluation: dict[int, datetime] = {}
        self.eval_cooldown = timedelta(seconds=30)  # Max one eval per 30 sec per channel

    def record_bot_message(self, channel_id: int, content: str):
        """Record what Flo said for contextual reply detection."""
        self.last_bot_message[channel_id] = content[:500]  # Keep first 500 chars

    def _is_contextual_reply(self, message) -> bool:
        """Check if this message seems to be responding to Flo's last message."""
        channel_id = message.channel.id
        last_bot = self.last_bot_message.get(channel_id)
        if not last_bot:
            return False

        content = message.content.lower()

        # Direct reply indicators
        reply_starters = [
            "yeah", "yes", "no", "nah", "true", "right", "exactly",
            "agreed", "disagree", "but", "and", "also", "wait",
            "oh", "haha", "lol", "lmao", "omg", "damn", "wow",
            "that's", "thats", "i think", "i know", "i mean",
            "thanks", "thank you", "ty", "fair", "good point",
            "same", "mood", "this", "^", "flo", "she", "you",
        ]

        # Check if message starts with a reply indicator
        for starter in reply_starters:
            if content.startswith(starter):
                return True

        # Short messages after Flo spoke are likely replies
        if len(content) < 50:
            return True

        # Check for question responses (if Flo asked something)
        if "?" in last_bot and len(content) < 200:
            return True

        return False

    def record_message(self, message, images: list[str] | None = None) -> str | None:
        """Record a message and check if evaluation should trigger.

        Args:
            message: Discord message object
            images: Optional list of image URLs extracted from the message

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
                images=images or [],
            ),
        )

        # Check trigger conditions
        trigger_reason = self._check_triggers(message)
        if trigger_reason:
            # Store channel_id -> trigger_reason (latest trigger wins)
            self.pending_evaluation[channel_id] = trigger_reason
            return trigger_reason
        return None

    def _check_triggers(self, message) -> str | None:
        """Check if this message should trigger evaluation.

        Priority triggers (contextual_reply, trigger_phrase, lull) always fire.
        Regular triggers (periodic, every_message) respect eval cooldown.
        """
        channel_id = message.channel.id
        recent = self.buffer.get_recent(channel_id, count=2)

        # PRIORITY TRIGGER: contextual reply to Flo (bypasses all cooldowns)
        if self._is_contextual_reply(message):
            return "contextual_reply"

        # PRIORITY TRIGGER: conversational lull (2+ min gap)
        if len(recent) >= 2:
            gap = recent[-1].timestamp - recent[-2].timestamp
            if gap > timedelta(minutes=2):
                return "lull"

        # Trigger: specific phrases
        content_lower = message.content.lower()
        trigger_phrases = [
            # Questions
            "anyone know",
            "does anyone",
            "what do you think",
            "thoughts?",
            "right?",
            "you know?",
            # Emotional states
            "help",
            "stuck",
            "confused",
            "frustrated",
            "excited",
            "finally",
            "omg",
            "wtf",
            "lol",
            "lmao",
            "fuck",
            "shit",
            "damn",
            "ugh",
            "yay",
            "awesome",
            "amazing",
            "terrible",
            "hate",
            "love",
            # Sharing
            "check this out",
            "look at this",
            "guess what",
            "you won't believe",
            "holy shit",
            # Social
            "hey everyone",
            "good morning",
            "good night",
            "i'm back",
            "brb",
        ]
        # PRIORITY TRIGGER: specific emotional/engagement phrases
        if any(phrase in content_lower for phrase in trigger_phrases):
            return "trigger_phrase"

        # REGULAR TRIGGERS: respect eval cooldown
        now = datetime.now(UTC)
        last_eval = self.last_evaluation.get(channel_id)
        eval_cooldown_active = last_eval and (now - last_eval) <= self.eval_cooldown

        if eval_cooldown_active:
            return None  # Skip regular triggers if we just evaluated

        # Trigger: every 5th message
        if len(self.buffer.channels[channel_id]) % 5 == 0:
            return "periodic"

        # Trigger: every message (catches everything else)
        return "every_message"

    def record_evaluation(self, channel_id: int):
        """Record that an evaluation happened (for eval cooldown)."""
        self.last_evaluation[channel_id] = datetime.now(UTC)

    def is_quiet_hours(self) -> bool:
        """Check if we're in quiet hours (no organic responses)."""
        if not QUIET_HOURS_ENABLED:
            return False
        hour = datetime.now().hour
        start, end = QUIET_HOURS_START, QUIET_HOURS_END
        if start > end:  # Crosses midnight
            return hour >= start or hour < end
        return start <= hour < end

    def build_evaluation_prompt(
        self, formatted_msgs: str, memories: str
    ) -> list[dict]:
        """Build the evaluation prompt for Claude (legacy single-tier)."""
        system = get_organic_personality()

        user_content = f"""## Recent Conversation:
{formatted_msgs}

## Relevant Memories:
{memories}

Evaluate whether to respond. Output JSON only."""

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    def build_decision_prompt(
        self, formatted_msgs: str, memories: str
    ) -> list[dict]:
        """Build fast decision-only prompt (tier 1).

        Uses a compact prompt for quick should_respond decisions.
        Does not generate a response.
        """
        system = get_organic_decision_prompt()

        # Keep context compact for speed
        user_content = f"""Chat:
{formatted_msgs}

Memories:
{memories}"""

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    def build_response_prompt(
        self, formatted_msgs: str, memories: str, response_type: str
    ) -> list[dict]:
        """Build response generation prompt (tier 2).

        Called only after tier 1 decides to respond with high confidence.
        Uses full personality and rich context.
        """
        system = get_organic_response_prompt()

        # Build context block similar to direct messages
        context_parts = []
        if memories and memories != "No relevant memories.":
            context_parts.append(f"## What You Remember About These People:\n{memories}")

        context_block = "\n\n".join(context_parts) if context_parts else ""

        user_content = f"""## Recent Discord Chat:
{formatted_msgs}

{context_block}

## Your Intent: {response_type}
(React naturally - don't mention this label)"""

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
                result = json.loads(raw[start:end])
                # Ensure proper types (LLM sometimes puts text in wrong fields)
                should_respond = result.get("should_respond", False)
                if isinstance(should_respond, str):
                    # LLM returned string instead of bool
                    should_respond = should_respond.lower() in ("true", "yes", "1")
                result["should_respond"] = bool(should_respond)

                confidence = result.get("confidence", 0)
                if isinstance(confidence, str):
                    try:
                        confidence = float(confidence)
                    except ValueError:
                        confidence = 0.5 if should_respond else 0
                result["confidence"] = float(confidence)

                return result
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
            # Ensure types are correct before inserting
            should_respond = result.get("should_respond", False)
            if not isinstance(should_respond, bool):
                should_respond = bool(should_respond) if should_respond in (True, 1, "true", "True") else False

            confidence = result.get("confidence", 0)
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.0

            log = OrganicResponseLog(
                channel_id=str(channel_id),
                channel_name=channel_name,
                guild_id=guild_id,
                trigger_reason=trigger_reason,
                message_context=context[:2000],  # Truncate for storage
                should_respond=should_respond,
                confidence=confidence,
                response_type=str(result.get("response_type", ""))[:50] if result.get("response_type") else None,
                reason=str(result.get("reason", ""))[:500],
                response_text=str(result.get("draft_response", ""))[:2000] if result.get("draft_response") else None,
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
