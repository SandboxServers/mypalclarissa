from __future__ import annotations

from datetime import datetime, timezone
import uuid


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


from sqlalchemy import (
    Column,
    String,
    DateTime,
    Text,
    ForeignKey,
    Integer,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_uuid)
    owner_id = Column(String, nullable=False)
    name = Column(String, nullable=False)

    sessions = relationship("Session", back_populates="project")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, nullable=False)
    title = Column(String, nullable=True)  # Thread title for UI
    archived = Column(String, default="false", nullable=False)  # "true" or "false"
    started_at = Column(DateTime, default=utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=utcnow, nullable=False)
    previous_session_id = Column(String, nullable=True)
    context_snapshot = Column(Text, nullable=True)
    session_summary = Column(Text, nullable=True)  # LLM-generated summary

    project = relationship("Project", back_populates="sessions")
    messages = relationship("Message", back_populates="session")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    user_id = Column(String, nullable=False)
    role = Column(String, nullable=False)  # 'user' | 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    session = relationship("Session", back_populates="messages")


class ChannelSummary(Base):
    """Rolling summary of Discord channel conversations."""

    __tablename__ = "channel_summaries"

    id = Column(String, primary_key=True, default=gen_uuid)
    channel_id = Column(String, nullable=False, unique=True)  # discord-channel-{id}
    summary = Column(Text, default="")
    summary_cutoff_at = Column(DateTime, nullable=True)  # newest summarized msg ts
    last_updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class LogEntry(Base):
    """Persistent log entries stored in the database."""

    __tablename__ = "log_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    timestamp = Column(DateTime, default=utcnow, nullable=False, index=True)
    level = Column(String(10), nullable=False, index=True)  # INFO, WARNING, ERROR, CRITICAL
    logger_name = Column(String(100), nullable=False, index=True)  # e.g., "api", "discord"
    message = Column(Text, nullable=False)
    module = Column(String(100), nullable=True)
    function = Column(String(100), nullable=True)
    line_number = Column(Integer, nullable=True)
    exception = Column(Text, nullable=True)  # Traceback if error
    extra_data = Column(Text, nullable=True)  # JSON for additional context
    user_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)


# =============================================================================
# KIRA-inspired Proactive Monitoring
# =============================================================================


class CheckerSubscription(Base):
    """User subscriptions to background checkers."""

    __tablename__ = "checker_subscriptions"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    checker_name = Column(String, nullable=False)  # github, ado, email
    enabled = Column(String, default="true", nullable=False)  # "true" or "false"
    notification_channel_id = Column(String, nullable=True)  # Where to send notifications
    config = Column(Text, nullable=True)  # JSON: checker-specific settings
    last_check_at = Column(DateTime, nullable=True)
    last_notification_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class CheckerState(Base):
    """State for incremental checking (e.g., last seen notification ID)."""

    __tablename__ = "checker_states"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    checker_name = Column(String, nullable=False)
    state_key = Column(String, nullable=False)  # e.g., "last_notification_id"
    state_value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# =============================================================================
# Multi-User / Group Chat Settings
# =============================================================================


class ChannelSettings(Base):
    """Per-channel settings for response behavior.

    Inspired by HuixiangDou's rejection throttle system.
    """

    __tablename__ = "channel_settings"

    id = Column(String, primary_key=True, default=gen_uuid)
    channel_id = Column(String, nullable=False, unique=True, index=True)
    guild_id = Column(String, nullable=True, index=True)  # Discord server ID

    # Rejection throttle (0.0-1.0): higher = more selective, fewer responses
    # Default 0.35, range 0.1-0.6
    reject_throttle = Column(String, default="0.35", nullable=False)

    # Response mode: "active" (respond proactively), "passive" (only when mentioned)
    response_mode = Column(String, default="active", nullable=False)

    # Maximum responses per minute (rate limiting)
    max_responses_per_minute = Column(Integer, default=10, nullable=False)

    # Quiet mode: only respond to direct mentions
    quiet_mode = Column(String, default="false", nullable=False)

    # Channel-specific personality adjustments (JSON)
    personality_overrides = Column(Text, nullable=True)

    # Statistics
    total_messages_seen = Column(Integer, default=0, nullable=False)
    total_responses = Column(Integer, default=0, nullable=False)
    total_rejections = Column(Integer, default=0, nullable=False)
    badcase_count = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ParticipantStats(Base):
    """Track participant activity for multi-user context.

    Helps with coreference resolution and personalized responses.
    """

    __tablename__ = "participant_stats"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    channel_id = Column(String, nullable=False, index=True)
    display_name = Column(String, nullable=True)

    # Activity tracking
    message_count = Column(Integer, default=0, nullable=False)
    last_message_at = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, default=utcnow, nullable=False)

    # Interaction with bot
    bot_mentions = Column(Integer, default=0, nullable=False)
    bot_replies_received = Column(Integer, default=0, nullable=False)

    # Preferences (learned from interactions)
    preferred_response_style = Column(String, nullable=True)  # brief, detailed, casual, formal
