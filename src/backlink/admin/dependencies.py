"""FastAPI dependency helpers."""

from __future__ import annotations

from typing import Iterator

from sqlmodel import Session

from ..database import get_engine


def get_session() -> Iterator[Session]:
    engine = get_engine()
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
