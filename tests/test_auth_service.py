from __future__ import annotations

from sqlmodel import SQLModel, create_engine

from backlink import database
from backlink.services.auth import AuthService


def test_seed_admin_user_retains_attributes_after_commit(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "_engine", engine)

    with database.session_scope() as session:
        auth = AuthService(session)
        user = auth.seed_admin(
            "admin@example.com",
            name="Admin User",
            password="s3cret",
        )

    assert user.id is not None
    assert user.email == "admin@example.com"
    assert user.name == "Admin User"
