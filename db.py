from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from models import Base

# Support both SQLite (local dev) and PostgreSQL (production)
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    # PostgreSQL with connection pooling
    # Railway and other hosts use postgresql:// prefix, SQLAlchemy prefers postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    engine = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Verify connections before use
        echo=False,
    )
    print(f"[db] Using PostgreSQL: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'configured'}")
else:
    # Fallback to SQLite for local development
    DATA_DIR = Path(os.getenv("DATA_DIR", "."))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{DATA_DIR}/assistant.db"
    engine = create_engine(DATABASE_URL, echo=False, future=True)
    print(f"[db] Using SQLite: {DATABASE_URL}")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
