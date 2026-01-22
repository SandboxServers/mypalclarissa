from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
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


class ChannelConfig(Base):
    """Per-channel configuration for Clara's behavior."""

    __tablename__ = "channel_configs"

    id = Column(String, primary_key=True, default=gen_uuid)
    channel_id = Column(String, nullable=False, unique=True, index=True)
    guild_id = Column(String, nullable=False, index=True)  # Discord server ID
    mode = Column(String, default="mention", nullable=False)  # active, mention, off
    configured_by = Column(String, nullable=True)  # User ID who set this
    configured_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


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


class GoogleOAuthToken(Base):
    """OAuth 2.0 tokens for Google Workspace integration (per-user)."""

    __tablename__ = "google_oauth_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, unique=True, index=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_type = Column(String, default="Bearer")
    expires_at = Column(DateTime, nullable=True)
    scopes = Column(Text, nullable=True)  # JSON array of granted scopes
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# =============================================================================
# Proactive Conversation Models
# =============================================================================


class ProactiveMessage(Base):
    """History of proactive messages sent by Clara."""

    __tablename__ = "proactive_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    channel_id = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    priority = Column(String, nullable=False)  # low, normal, high, critical
    reason = Column(Text, nullable=True)  # Why Clara decided to reach out
    sent_at = Column(DateTime, default=utcnow, nullable=False)
    response_received = Column(String, default="false")  # "true" or "false"
    response_at = Column(DateTime, nullable=True)


class UserInteractionPattern(Base):
    """Learned interaction patterns per user for proactive timing."""

    __tablename__ = "user_interaction_patterns"

    user_id = Column(String, primary_key=True)
    last_interaction_at = Column(DateTime, nullable=True)
    last_interaction_channel = Column(String, nullable=True)
    last_interaction_summary = Column(Text, nullable=True)  # LLM-extracted summary of last convo
    last_interaction_energy = Column(String, nullable=True)  # stressed, focused, casual, tired, excited, frustrated
    typical_active_hours = Column(Text, nullable=True)  # JSON: {"weekday": [9,17], "weekend": [10,22]}
    timezone = Column(String, nullable=True)  # IANA timezone (e.g., "America/New_York")
    timezone_source = Column(String, nullable=True)  # "calendar", "explicit", "inferred"
    avg_response_time_seconds = Column(Integer, nullable=True)
    explicit_signals = Column(Text, nullable=True)  # JSON: {"do_not_disturb": false, "busy_until": null}
    proactive_success_rate = Column(Integer, nullable=True)  # Percentage (0-100)
    # ORS enhancements
    proactive_response_rate = Column(Integer, nullable=True)  # % of proactive messages acknowledged (0-100)
    preferred_proactive_times = Column(Text, nullable=True)  # JSON: When user responds best to proactive
    preferred_proactive_types = Column(Text, nullable=True)  # JSON: What kinds of reach-outs work
    topic_receptiveness = Column(Text, nullable=True)  # JSON: Which topics land vs. get ignored
    explicit_boundaries = Column(Text, nullable=True)  # JSON: Things user said not to do
    open_threads = Column(Text, nullable=True)  # JSON: Unresolved topics from conversations
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ProactiveNote(Base):
    """Internal notes/observations for ORS - accumulated understanding over time."""

    __tablename__ = "proactive_notes"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    note = Column(Text, nullable=False)  # The observation/thought
    note_type = Column(String, nullable=True)  # observation, question, follow_up, connection
    source_context = Column(Text, nullable=True)  # JSON: What triggered this note
    source_model = Column(String, nullable=True)  # Model that created this note (opus-4, sonnet-4, etc)
    source_confidence = Column(String, nullable=True)  # Self-assessed confidence (high, medium, low)
    grounding_message_ids = Column(Text, nullable=True)  # JSON: Message IDs that triggered this note
    connections = Column(Text, nullable=True)  # JSON: List of related note IDs
    relevance_score = Column(Integer, default=100)  # 0-100, decays over time
    surface_conditions = Column(Text, nullable=True)  # JSON: When should this come up
    surface_at = Column(DateTime, nullable=True)  # Specific time to potentially bring this up
    expires_at = Column(DateTime, nullable=True)  # Time-sensitive notes expire
    surfaced = Column(String, default="false")  # "true" or "false"
    surfaced_at = Column(DateTime, nullable=True)
    archived = Column(String, default="false")  # "true" or "false" - stale or surfaced notes
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ProactiveAssessment(Base):
    """Situation assessments for ORS continuity - records of THINK state processing."""

    __tablename__ = "proactive_assessments"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    context_snapshot = Column(Text, nullable=True)  # JSON: Full context at time of assessment
    assessment = Column(Text, nullable=True)  # LLM's read on the situation
    decision = Column(String, nullable=False)  # WAIT, THINK, or SPEAK
    reasoning = Column(Text, nullable=True)  # Why this decision was made
    note_created = Column(String, nullable=True)  # Note ID if THINK created a note
    message_sent = Column(Text, nullable=True)  # Message text if SPEAK
    next_check_at = Column(DateTime, nullable=True)  # When to check again
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)


# =============================================================================
# Email Monitoring Models
# =============================================================================


class EmailAccount(Base):
    """User email account connections for monitoring."""

    __tablename__ = "email_accounts"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    email_address = Column(String, nullable=False)
    provider_type = Column(String, nullable=False)  # "gmail", "imap"

    # Gmail uses existing GoogleOAuthToken via user_id
    # No separate tokens stored here - gmail provider checks GoogleOAuthToken

    # IMAP credentials (encrypted with Fernet)
    imap_server = Column(String, nullable=True)
    imap_port = Column(Integer, nullable=True)
    imap_username = Column(String, nullable=True)
    imap_password = Column(Text, nullable=True)  # Encrypted

    # Polling configuration
    enabled = Column(String, default="true")  # "true" or "false"
    poll_interval_minutes = Column(Integer, default=5)
    last_checked_at = Column(DateTime, nullable=True)
    last_seen_uid = Column(String, nullable=True)  # For incremental fetching
    last_seen_timestamp = Column(DateTime, nullable=True)

    # Status tracking
    status = Column(String, default="active")  # active, error, disabled
    last_error = Column(Text, nullable=True)
    error_count = Column(Integer, default=0)

    # Discord notification settings
    alert_channel_id = Column(String, nullable=True)
    ping_on_alert = Column(String, default="false")  # "true" = @mention
    quiet_hours_start = Column(Integer, nullable=True)  # Hour (0-23)
    quiet_hours_end = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class EmailRule(Base):
    """Per-user importance rules for email filtering."""

    __tablename__ = "email_rules"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    account_id = Column(String, ForeignKey("email_accounts.id"), nullable=True)  # null = all accounts

    name = Column(String, nullable=False)
    enabled = Column(String, default="true")  # "true" or "false"
    priority = Column(Integer, default=0)  # Higher = checked first

    # Rule definition JSON:
    # {
    #   "conditions": {
    #     "sender_contains": ["recruiter", "hr@"],
    #     "sender_domain": ["linkedin.com", "greenhouse.io"],
    #     "subject_contains": ["interview", "offer"],
    #     "subject_regex": "(?i)interview.*schedule",
    #     "body_contains": ["please respond"],
    #     "has_attachments": true
    #   },
    #   "match_mode": "any" | "all"
    # }
    rule_definition = Column(Text, nullable=False)

    # Action configuration
    importance = Column(String, default="normal")  # low, normal, high, urgent
    custom_alert_message = Column(Text, nullable=True)  # Template for alert
    override_ping = Column(String, nullable=True)  # "true"/"false" or null (inherit)

    # Preset tracking
    preset_name = Column(String, nullable=True)  # "job_hunting", "urgent", etc.

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class EmailAlert(Base):
    """History of email alerts sent to Discord (for dedup and tracking)."""

    __tablename__ = "email_alerts"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    account_id = Column(String, ForeignKey("email_accounts.id"), nullable=False)
    rule_id = Column(String, ForeignKey("email_rules.id"), nullable=True)

    # Email info (denormalized for history)
    email_uid = Column(String, nullable=False, index=True)  # For dedup
    email_from = Column(String, nullable=False)
    email_subject = Column(String, nullable=False)
    email_snippet = Column(Text, nullable=True)  # First ~200 chars
    email_received_at = Column(DateTime, nullable=True)

    # Alert info
    channel_id = Column(String, nullable=False)
    message_id = Column(String, nullable=True)  # Discord message ID
    importance = Column(String, nullable=False)
    was_pinged = Column(String, default="false")

    sent_at = Column(DateTime, default=utcnow)


# =============================================================================
# Guild Configuration Models
# =============================================================================


class GuildConfig(Base):
    """Per-guild (Discord server) configuration for Clara settings."""

    __tablename__ = "guild_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, unique=True, nullable=False, index=True)

    # Model settings
    default_tier = Column(String, nullable=True)  # high/mid/low or None (use env default)
    auto_tier_enabled = Column(String, default="false")  # "true" or "false"

    # ORS (Organic Response System) settings
    ors_enabled = Column(String, default="false")  # "true" or "false"
    ors_channel_id = Column(String, nullable=True)  # Channel for proactive messages
    ors_quiet_start = Column(String, nullable=True)  # HH:MM format
    ors_quiet_end = Column(String, nullable=True)  # HH:MM format

    # Sandbox settings
    sandbox_mode = Column(String, default="auto")  # local, remote, auto

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# =============================================================================
# MCP (Model Context Protocol) Models
# =============================================================================

# Import MCP models so they're included in metadata for table creation
# The model is defined in clarissa_core/mcp/models.py but uses this Base
try:
    from clarissa_core.mcp.models import MCPServer  # noqa: F401
except ImportError:
    pass  # MCP module not yet installed
