from __future__ import annotations

from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine

from backlink.services.admin import AdminService


def create_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_resolve_runtime_variables_maps_target_fields():
    with create_session() as session:
        admin = AdminService(session)
        target = SimpleNamespace(
            id=1,
            url="https://example.com",
            title="Example",
            description="Desc",
            summary="Summary",
            keywords="alpha, beta",
        )
        generated = {"GENERATED_BIO": "Bio text"}
        variables = admin.resolve_runtime_variables(target, generated)
        assert variables["TARGET_URL"] == "https://example.com"
        assert variables["GENERATED_BIO"] == "Bio text"
        assert variables["TARGET_KEYWORDS_LIST"] == ["alpha", "beta"]
