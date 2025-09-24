"""Database utilities for the Backlink bot."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import os

from sqlmodel import Session, SQLModel, create_engine

from .config import DB_PATH

DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DB_PATH}"

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

_engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


def init_db() -> None:
    """Create tables if they don't already exist."""

    SQLModel.metadata.create_all(_engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope for database operations."""

    session = Session(_engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine():
    """Return the configured SQLAlchemy engine."""

    return _engine

__all__ = ["init_db", "session_scope", "get_engine"]
