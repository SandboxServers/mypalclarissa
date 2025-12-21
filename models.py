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
