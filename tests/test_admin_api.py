import importlib
import re
from typing import Tuple

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from backlink.admin.app import app, get_session
from backlink.models import Category, Role
from backlink.services.auth import AuthService
from backlink.services.settings import SettingsService


def _extract_token(html: str, field: str = "csrf_token") -> str:
    match = re.search(r'name="%s" value="([^"]+)"' % re.escape(field), html)
    assert match, f"CSRF token {field} not found"
    return match.group(1)


@pytest.fixture()
def api_client(tmp_path, monkeypatch) -> Tuple[TestClient, Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_session():
        session = Session(engine)
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session

    class TestSettingsService(SettingsService):
        def __init__(self) -> None:
            super().__init__(settings_path=tmp_path / "settings.json")

    admin_module = importlib.import_module("backlink.admin.app")
    monkeypatch.setattr(admin_module, "SettingsService", TestSettingsService)
    monkeypatch.setattr(admin_module, "_run_enrichment", lambda target_id: None)

    with Session(engine) as session:
        auth = AuthService(session)
        auth.create_user(email="admin@example.com", name="Admin", password="secret", role=Role.ADMIN)
        auth.create_user(email="user@example.com", name="User", password="secret", role=Role.USER)
        session.add(Category(name="Profile Backlinks"))
        session.commit()

    client = TestClient(app)
    try:
        yield client, engine
    finally:
        app.dependency_overrides.clear()


def _login(client: TestClient, email: str, password: str) -> None:
    response = client.get("/login")
    token = _extract_token(response.text)
    result = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    assert result.status_code == status.HTTP_303_SEE_OTHER


def test_create_target_flow(api_client):
    client, _ = api_client
    _login(client, "admin@example.com", "secret")
    page = client.get("/targets")
    token = _extract_token(page.text, "X-CSRF-Token")
    response = client.post(
        "/api/targets",
        json={"url": "https://example.com"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["url"].rstrip("/") == "https://example.com"
    listing = client.get("/api/targets")
    assert any(target["url"].rstrip("/") == "https://example.com" for target in listing.json())


def test_settings_update(api_client):
    client, engine = api_client
    _login(client, "admin@example.com", "secret")
    page = client.get("/settings")
    token = _extract_token(page.text, "X-CSRF-Token")
    response = client.put(
        "/api/settings",
        json={"headless_default": False, "playwright_timeout_ms": 4321},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["headless_default"] is False
    assert body["playwright_timeout_ms"] == 4321


def test_category_request_approval(api_client):
    client, engine = api_client
    _login(client, "user@example.com", "secret")
    page = client.get("/targets")
    token = _extract_token(page.text, "X-CSRF-Token")
    create_resp = client.post(
        "/api/category-requests",
        json={"name": "Forums", "reason": "Need forum backlinks"},
        headers={"X-CSRF-Token": token},
    )
    assert create_resp.status_code == 200
    request_id = create_resp.json()["id"]
    client.get("/logout")

    _login(client, "admin@example.com", "secret")
    page = client.get("/categories")
    admin_token = _extract_token(page.text, "X-CSRF-Token")
    approve = client.post(
        f"/api/category-requests/{request_id}/approve",
        headers={"X-CSRF-Token": admin_token},
    )
    assert approve.status_code == 200
    categories = client.get("/api/categories")
    assert any(row["name"] == "Forums" for row in categories.json())
    requests_response = client.get("/api/category-requests")
    assert any(row["id"] == request_id for row in requests_response.json())
