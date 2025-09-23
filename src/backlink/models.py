"""Database models used across the application."""

import enum
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class Role(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


class RecipeStatus(str, enum.Enum):
    TRAINING = "training"
    READY = "ready"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


class CategoryRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ScheduleFrequency(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    recipes: List["Recipe"] = Relationship(back_populates="category")
    schedules: List["RecipeSchedule"] = Relationship(back_populates="category")


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    name: str
    role: Role = Field(default=Role.USER)
    hashed_password: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    recipes: List["Recipe"] = Relationship(back_populates="owner")
    category_requests: List["CategoryRequest"] = Relationship(back_populates="requester")


class Recipe(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    site: str = Field(index=True)
    slug: str = Field(index=True, unique=True)
    description: Optional[str] = None
    status: RecipeStatus = Field(default=RecipeStatus.TRAINING)
    category_id: int = Field(foreign_key="category.id")
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")
    current_version_id: Optional[int] = Field(default=None, foreign_key="recipeversion.id")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_executed_at: Optional[datetime] = None

    category: Optional[Category] = Relationship(back_populates="recipes")
    owner: Optional[User] = Relationship(back_populates="recipes")
    versions: List["RecipeVersion"] = Relationship(
        back_populates="recipe",
        sa_relationship_kwargs={"foreign_keys": "RecipeVersion.recipe_id"},
    )
    executions: List["Execution"] = Relationship(back_populates="recipe")
    schedules: List["RecipeSchedule"] = Relationship(back_populates="recipe")
    generated_assets: List["GeneratedAsset"] = Relationship(
        back_populates="recipe",
        sa_relationship_kwargs={"foreign_keys": "GeneratedAsset.recipe_id"},
    )


class RecipeVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(foreign_key="recipe.id")
    version: int = Field(index=True)
    yaml_path: str
    change_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    recipe: Optional[Recipe] = Relationship(
        back_populates="versions",
        sa_relationship_kwargs={"foreign_keys": "RecipeVersion.recipe_id"},
    )


class Execution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(foreign_key="recipe.id")
    target_id: Optional[int] = Field(default=None, foreign_key="targeturl.id")
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    log_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None

    recipe: Optional[Recipe] = Relationship(back_populates="executions")
    target: Optional["TargetURL"] = Relationship(back_populates="executions")


class CategoryRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    requested_by_id: int = Field(foreign_key="user.id")
    name: str
    reason: Optional[str] = None
    status: CategoryRequestStatus = Field(default=CategoryRequestStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decision_at: Optional[datetime] = None

    requester: Optional[User] = Relationship(back_populates="category_requests")


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message: str
    type: str = Field(default="info")
    is_read: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RecipeSchedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: Optional[int] = Field(default=None, foreign_key="recipe.id")
    category_id: Optional[int] = Field(default=None, foreign_key="category.id")
    frequency: ScheduleFrequency
    next_run: datetime
    is_active: bool = Field(default=True)

    recipe: Optional[Recipe] = Relationship(back_populates="schedules")
    category: Optional[Category] = Relationship(back_populates="schedules")


class TargetURL(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(index=True, unique=True)
    title: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None
    summary: Optional[str] = None
    html_snapshot_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    executions: List[Execution] = Relationship(back_populates="target")
    generated_assets: List["GeneratedAsset"] = Relationship(back_populates="target")


class GeneratedAsset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    target_id: int = Field(foreign_key="targeturl.id")
    recipe_id: Optional[int] = Field(default=None, foreign_key="recipe.id")
    kind: str
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    target: Optional[TargetURL] = Relationship(back_populates="generated_assets")
    recipe: Optional[Recipe] = Relationship(back_populates="generated_assets")


__all__ = [
    "Role",
    "RecipeStatus",
    "ExecutionStatus",
    "CategoryRequestStatus",
    "ScheduleFrequency",
    "Category",
    "User",
    "Recipe",
    "RecipeVersion",
    "Execution",
    "CategoryRequest",
    "Notification",
    "RecipeSchedule",
    "TargetURL",
    "GeneratedAsset",
]
