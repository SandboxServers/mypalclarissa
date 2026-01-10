"""Group session management for multi-user contexts.

Tracks participants, conversation topics, and maintains context
across multiple users in a channel or thread.

Inspired by HuixiangDou's session management for group chats.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class Participant:
    """A participant in a group conversation."""

    user_id: str
    display_name: str
    last_message_at: datetime = field(default_factory=datetime.utcnow)
    message_count: int = 0
    mentioned_by_bot: bool = False

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_message_at = datetime.utcnow()
        self.message_count += 1

    @property
    def is_active(self) -> bool:
        """Check if participant was active in last 30 minutes."""
        return datetime.utcnow() - self.last_message_at < timedelta(minutes=30)


@dataclass
class ConversationTopic:
    """Extracted topic from conversation."""

    topic: str
    confidence: float
    extracted_at: datetime = field(default_factory=datetime.utcnow)
    mentioned_by: list[str] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        """Check if topic is older than 1 hour."""
        return datetime.utcnow() - self.extracted_at > timedelta(hours=1)


@dataclass
class MessageReference:
    """A reference to a previous message for coreference resolution."""

    message_id: str
    author_id: str
    author_name: str
    content: str
    timestamp: datetime
    entities: list[str] = field(default_factory=list)  # Named entities mentioned


class GroupSession:
    """Manages context for a group conversation.

    Features:
    - Participant tracking with activity status
    - Topic extraction and tracking
    - Coreference resolution (pronoun → entity mapping)
    - Recent message buffer for context
    """

    # Pronouns that may need resolution
    PRONOUNS = {
        "he", "him", "his", "she", "her", "hers",
        "they", "them", "their", "theirs",
        "it", "its", "this", "that", "these", "those",
    }

    def __init__(
        self,
        channel_id: str,
        thread_id: str | None = None,
        max_messages: int = 50,
        max_participants: int = 20,
    ):
        """Initialize group session.

        Args:
            channel_id: Discord channel ID
            thread_id: Optional thread ID within channel
            max_messages: Maximum messages to keep in buffer
            max_participants: Maximum participants to track
        """
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.max_messages = max_messages
        self.max_participants = max_participants

        self._participants: dict[str, Participant] = {}
        self._messages: list[MessageReference] = []
        self._topics: list[ConversationTopic] = []
        self._entity_mentions: dict[str, list[MessageReference]] = defaultdict(list)

        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()

    @property
    def session_key(self) -> str:
        """Unique key for this session."""
        if self.thread_id:
            return f"{self.channel_id}:{self.thread_id}"
        return self.channel_id

    @property
    def active_participants(self) -> list[Participant]:
        """Get list of currently active participants."""
        return [p for p in self._participants.values() if p.is_active]

    @property
    def participant_names(self) -> list[str]:
        """Get display names of active participants."""
        return [p.display_name for p in self.active_participants]

    @property
    def current_topic(self) -> ConversationTopic | None:
        """Get the most recent non-stale topic."""
        for topic in reversed(self._topics):
            if not topic.is_stale:
                return topic
        return None

    def add_message(
        self,
        message_id: str,
        author_id: str,
        author_name: str,
        content: str,
        timestamp: datetime | None = None,
    ) -> None:
        """Add a message to the session buffer.

        Args:
            message_id: Unique message ID
            author_id: Author's user ID
            author_name: Author's display name
            content: Message content
            timestamp: Message timestamp (defaults to now)
        """
        timestamp = timestamp or datetime.utcnow()
        self.last_activity = timestamp

        # Update or add participant
        if author_id not in self._participants:
            if len(self._participants) >= self.max_participants:
                # Remove least recently active
                oldest = min(self._participants.values(), key=lambda p: p.last_message_at)
                del self._participants[oldest.user_id]

            self._participants[author_id] = Participant(
                user_id=author_id,
                display_name=author_name,
            )

        self._participants[author_id].update_activity()
        self._participants[author_id].display_name = author_name  # Update name

        # Extract entities from message
        entities = self._extract_entities(content)

        # Create message reference
        msg_ref = MessageReference(
            message_id=message_id,
            author_id=author_id,
            author_name=author_name,
            content=content,
            timestamp=timestamp,
            entities=entities,
        )

        # Add to buffer
        self._messages.append(msg_ref)
        if len(self._messages) > self.max_messages:
            self._messages.pop(0)

        # Index entities for coreference
        for entity in entities:
            self._entity_mentions[entity.lower()].append(msg_ref)

    def _extract_entities(self, content: str) -> list[str]:
        """Extract named entities from message content."""
        entities = []

        # Extract @mentions (Discord format)
        mentions = re.findall(r"<@!?(\d+)>", content)
        for mention_id in mentions:
            if mention_id in self._participants:
                entities.append(self._participants[mention_id].display_name)

        # Extract quoted names (simple heuristic)
        quoted = re.findall(r'"([^"]+)"', content)
        entities.extend(quoted)

        # Extract capitalized words that might be names (2+ chars, not at start)
        words = content.split()
        for i, word in enumerate(words[1:], 1):
            if word[0].isupper() and len(word) > 1 and word.isalpha():
                entities.append(word)

        return entities

    def resolve_pronouns(self, message: str) -> dict[str, str]:
        """Attempt to resolve pronouns to entities.

        Args:
            message: Message containing pronouns

        Returns:
            Dict mapping pronoun → resolved entity
        """
        resolutions: dict[str, str] = {}
        words = message.lower().split()

        for word in words:
            if word in self.PRONOUNS:
                resolved = self._resolve_pronoun(word)
                if resolved:
                    resolutions[word] = resolved

        return resolutions

    def _resolve_pronoun(self, pronoun: str) -> str | None:
        """Resolve a single pronoun based on recent context."""
        if not self._messages:
            return None

        # Look at recent messages for entity references
        recent = self._messages[-10:]

        # For "he/him/his" - look for male names or last male speaker
        if pronoun in ("he", "him", "his"):
            for msg in reversed(recent):
                # Simple heuristic: use most recent other speaker
                if msg.entities:
                    return msg.entities[0]
            # Fallback to most recent participant
            if len(self._participants) > 1:
                for p in self.active_participants:
                    return p.display_name

        # For "she/her/hers" - similar logic
        if pronoun in ("she", "her", "hers"):
            for msg in reversed(recent):
                if msg.entities:
                    return msg.entities[0]

        # For "it/this/that" - look for recent topics or objects
        if pronoun in ("it", "this", "that"):
            # Try to find a recent topic
            if self.current_topic:
                return self.current_topic.topic
            # Or a recent entity
            for msg in reversed(recent):
                if msg.entities:
                    return msg.entities[0]

        # For "they/them" - could be plural or singular they
        if pronoun in ("they", "them", "their", "theirs"):
            # Return recent participants
            names = self.participant_names[:3]
            if names:
                return " and ".join(names)

        return None

    def set_topic(self, topic: str, confidence: float, mentioned_by: str) -> None:
        """Set/update the current conversation topic."""
        self._topics.append(ConversationTopic(
            topic=topic,
            confidence=confidence,
            mentioned_by=[mentioned_by],
        ))

        # Keep only last 5 topics
        if len(self._topics) > 5:
            self._topics.pop(0)

    def get_context_summary(self) -> dict[str, Any]:
        """Get a summary of the current session context.

        Useful for including in LLM prompts.
        """
        return {
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "active_participants": [
                {"name": p.display_name, "messages": p.message_count}
                for p in self.active_participants
            ],
            "current_topic": self.current_topic.topic if self.current_topic else None,
            "message_count": len(self._messages),
            "session_duration_minutes": (datetime.utcnow() - self.created_at).seconds // 60,
        }

    def get_recent_context(self, limit: int = 10) -> list[dict[str, str]]:
        """Get recent messages for context.

        Returns:
            List of dicts with 'author' and 'content' keys
        """
        return [
            {"author": msg.author_name, "content": msg.content}
            for msg in self._messages[-limit:]
        ]

    def format_for_prompt(self) -> str:
        """Format session context for inclusion in LLM prompt."""
        lines = []

        # Participant context
        active = self.active_participants
        if active:
            names = ", ".join(p.display_name for p in active[:5])
            lines.append(f"Active participants: {names}")

        # Topic context
        if self.current_topic:
            lines.append(f"Current topic: {self.current_topic.topic}")

        return "\n".join(lines)


# Session cache
_sessions: dict[str, GroupSession] = {}
_session_timeout = timedelta(hours=2)


def get_group_session(
    channel_id: str,
    thread_id: str | None = None,
    create: bool = True,
) -> GroupSession | None:
    """Get or create a group session.

    Args:
        channel_id: Discord channel ID
        thread_id: Optional thread ID
        create: Create session if it doesn't exist

    Returns:
        GroupSession instance or None
    """
    key = f"{channel_id}:{thread_id}" if thread_id else channel_id

    # Check for existing session
    if key in _sessions:
        session = _sessions[key]
        # Check if session is stale
        if datetime.utcnow() - session.last_activity > _session_timeout:
            del _sessions[key]
        else:
            return session

    # Create new session if requested
    if create:
        session = GroupSession(channel_id, thread_id)
        _sessions[key] = session
        return session

    return None


def cleanup_stale_sessions() -> int:
    """Remove stale sessions from cache.

    Returns:
        Number of sessions removed
    """
    now = datetime.utcnow()
    stale_keys = [
        key for key, session in _sessions.items()
        if now - session.last_activity > _session_timeout
    ]

    for key in stale_keys:
        del _sessions[key]

    return len(stale_keys)
