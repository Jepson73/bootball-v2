# src/storage/db.py - SQLAlchemy engine + session helpers
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings
from src.storage.models import Base

logger = logging.getLogger(__name__)


def _get_engine():
    """Create engine with proper settings for SQLite."""
    db_path = settings.database_url.replace("sqlite:///", "")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    )
    
    # Enable foreign keys for SQLite
    if "sqlite" in settings.database_url:
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()
    
    return engine


_engine = None
_SessionLocal = None


def get_engine():
    """Lazy initialization of engine."""
    global _engine
    if _engine is None:
        _engine = _get_engine()
    return _engine


def get_session_maker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def init_db() -> None:
    """Create all tables."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized: %s", settings.database_url)


@contextmanager
def get_session() -> Session:
    """Context manager for database sessions."""
    SessionLocal = get_session_maker()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()