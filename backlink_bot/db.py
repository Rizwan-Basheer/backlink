"""Database models and session utilities."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel, Session, create_engine, select

from . import config


class Role(str, Enum):
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"


class RecipeStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class CategoryRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class NotificationType(str, Enum):
    FAILURE = "failure"
    CATEGORY_REQUEST = "category_request"
    SYSTEM = "system"


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    recipes: list["Recipe"] = Relationship(back_populates="category")


class Recipe(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    site: str
    description: Optional[str] = None
    category_id: int = Field(foreign_key="category.id")
    path: str
    status: RecipeStatus = Field(default=RecipeStatus.ACTIVE)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1)
    rerun_schedule: Optional[str] = None
    is_paused: bool = Field(default=False)

    category: Optional[Category] = Relationship(back_populates="recipes")
    executions: list["Execution"] = Relationship(back_populates="recipe")
    versions: list["RecipeVersion"] = Relationship(back_populates="recipe")


class RecipeVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(foreign_key="recipe.id")
    version: int
    file_path: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None

    recipe: Optional[Recipe] = Relationship(back_populates="versions")


class Execution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(foreign_key="recipe.id")
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    log_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    error_message: Optional[str] = None
    run_context: Optional[str] = None

    recipe: Optional[Recipe] = Relationship(back_populates="executions")


class CategoryRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    requester: str
    requested_name: str
    description: Optional[str] = None
    status: CategoryRequestStatus = Field(default=CategoryRequestStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    reviewer: Optional[str] = None


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: NotificationType
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    role: Role = Field(default=Role.USER)
    hashed_password: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


engine = create_engine(f"sqlite:///{config.DB_PATH}", echo=False, connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


def get_or_create_default_category(session: Session) -> Category:
    statement = select(Category).where(Category.name == "Uncategorized")
    category = session.exec(statement).first()
    if category:
        return category
    category = Category(name="Uncategorized", description="Default bucket for new recipes")
    session.add(category)
    session.commit()
    session.refresh(category)
    return category
