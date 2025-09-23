from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from backlink.models import Role
from backlink.services.auth import AuthService
from backlink.services.settings import SettingsService


def create_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_seed_admin_creates_and_updates():
    with create_session() as session:
        service = AuthService(session)
        user = service.seed_admin("admin@example.com", name="Admin", password="secret")
        assert user.role == Role.ADMIN
        first_hash = user.hashed_password
        updated = service.seed_admin("admin@example.com", name="Admin 2", password="another")
        assert updated.id == user.id
        assert updated.role == Role.ADMIN
        assert updated.hashed_password != first_hash


def test_authenticate_success_and_failure():
    with create_session() as session:
        service = AuthService(session)
        service.create_user(email="user@example.com", name="User", password="topsecret")
        assert service.authenticate("user@example.com", "topsecret") is not None
        assert service.authenticate("user@example.com", "wrong") is None


def test_settings_service_round_trip(tmp_path):
    settings_path = Path(tmp_path) / "settings.json"
    service = SettingsService(settings_path=settings_path)
    default = service.load()
    assert default.playwright_timeout_ms > 0
    updated = service.update({"headless_default": False, "playwright_timeout_ms": 1234})
    assert updated.headless_default is False
    assert updated.playwright_timeout_ms == 1234
    reloaded = service.load()
    assert reloaded.headless_default is False
    assert reloaded.playwright_timeout_ms == 1234
