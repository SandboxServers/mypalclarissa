from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from logging_config import get_logger, init_logging, set_db_session_factory
from models import Base

# Initialize console logging early (database handler added later)
init_logging()
logger = get_logger("db")

# Support both SQLite (local dev) and PostgreSQL (production)
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    # PostgreSQL with connection pooling
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    engine = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )
    db_display = DATABASE_URL.split("@")[1] if "@" in DATABASE_URL else "configured"
    logger.info(f"Using PostgreSQL: {db_display}")
else:
    # Fallback to SQLite for local development
    DATA_DIR = Path(os.getenv("DATA_DIR", "."))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{DATA_DIR}/assistant.db"
    engine = create_engine(DATABASE_URL, echo=False, future=True)
    logger.info(f"Using SQLite: {DATABASE_URL}")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Connect database handler now that SessionLocal is available
set_db_session_factory(SessionLocal)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized")


# Create tables at import time (ensures log_entries exists before any logging)
init_db()
