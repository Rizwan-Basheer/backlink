"""Service layer for admin features."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, delete, select

from .db import (
    Category,
    CategoryRequest,
    CategoryRequestStatus,
    Execution,
    ExecutionStatus,
    Notification,
    NotificationType,
    Recipe,
    RecipeStatus,
    RecipeVersion,
    get_session,
)
from .utils.logging import get_logger

logger = get_logger("backlink.services")


@contextmanager
def session_scope(existing_session: Optional[Session] = None) -> Iterable[Session]:
    """Provide a transactional scope around a series of operations."""
    session = existing_session or get_session()
    try:
        yield session
        session.commit()
    except Exception:  # pragma: no cover - safety net
        session.rollback()
        raise
    finally:
        if existing_session is None:
            session.close()


class AdminService:
    """High level operations exposed to the admin panel and CLI."""

    def __init__(self, session: Optional[Session] = None) -> None:
        self._session = session

    @contextmanager
    def _session_scope(self) -> Iterable[Session]:
        with session_scope(self._session) as session:
            yield session

    # ------------------------------------------------------------------
    # Category management
    # ------------------------------------------------------------------
    def create_category(self, name: str, description: str | None = None) -> Category:
        name = name.strip()
        with self._session_scope() as session:
            existing = session.exec(select(Category).where(Category.name == name)).first()
            if existing:
                raise ValueError(f"Category '{name}' already exists")
            category = Category(name=name, description=description)
            session.add(category)
            session.commit()
            session.refresh(category)
            logger.info("Created category '%s'", name)
            return category

    def list_categories(self, include_inactive: bool = False) -> List[Category]:
        with self._session_scope() as session:
            statement = select(Category)
            if not include_inactive:
                statement = statement.where(Category.is_active == True)  # noqa: E712
            return list(session.exec(statement).all())

    # ------------------------------------------------------------------
    # Category requests
    # ------------------------------------------------------------------
    def submit_category_request(self, requester: str, requested_name: str, description: str | None = None) -> CategoryRequest:
        request = CategoryRequest(requester=requester, requested_name=requested_name, description=description)
        with self._session_scope() as session:
            session.add(request)
            session.commit()
            session.refresh(request)
            session.add(
                Notification(
                    type=NotificationType.CATEGORY_REQUEST,
                    message=f"New category request '{requested_name}' from {requester}",
                )
            )
            logger.info("Category request '%s' submitted by %s", requested_name, requester)
            return request

    def update_category_request(self, request_id: int, status: CategoryRequestStatus, reviewer: str, notes: str | None = None) -> CategoryRequest:
        with self._session_scope() as session:
            request = session.get(CategoryRequest, request_id)
            if not request:
                raise ValueError("Request not found")
            request.status = status
            request.reviewer = reviewer
            request.reviewed_at = datetime.utcnow()
            session.add(
                Notification(
                    type=NotificationType.CATEGORY_REQUEST,
                    message=f"Category request '{request.requested_name}' {status.value}",
                )
            )
            logger.info("Category request %s marked %s", request_id, status.value)
            return request

    def list_category_requests(self, status: CategoryRequestStatus | None = None) -> List[CategoryRequest]:
        with self._session_scope() as session:
            statement = select(CategoryRequest)
            if status:
                statement = statement.where(CategoryRequest.status == status)
            return list(session.exec(statement).all())

    # ------------------------------------------------------------------
    # Recipe management and versioning
    # ------------------------------------------------------------------
    def register_recipe(
        self,
        name: str,
        site: str,
        path: Path,
        category: Category,
        description: str | None = None,
        notes: str | None = None,
    ) -> Recipe:
        with self._session_scope() as session:
            recipe = session.exec(select(Recipe).where(Recipe.name == name)).first()
            if recipe:
                recipe.version += 1
                recipe.updated_at = datetime.utcnow()
                recipe.description = description or recipe.description
                recipe.site = site
                recipe.path = str(path)
                session.add(
                    RecipeVersion(
                        recipe_id=recipe.id,
                        version=recipe.version,
                        file_path=str(path),
                        notes=notes,
                    )
                )
            else:
                recipe = Recipe(
                    name=name,
                    site=site,
                    description=description,
                    category_id=category.id,
                    path=str(path),
                )
                session.add(recipe)
                session.commit()
                session.refresh(recipe)
                session.add(
                    RecipeVersion(
                        recipe_id=recipe.id,
                        version=recipe.version,
                        file_path=str(path),
                        notes=notes,
                    )
                )
            logger.info("Registered recipe '%s' (v%s)", name, recipe.version)
            return recipe

    def find_recipe_by_name(self, name: str) -> Recipe | None:
        with self._session_scope() as session:
            return session.exec(select(Recipe).where(Recipe.name == name)).first()


    def list_recipes(
        self,
        category: str | None = None,
        status: RecipeStatus | None = None,
        search: str | None = None,
    ) -> List[Recipe]:
        with self._session_scope() as session:
            statement = select(Recipe).options(selectinload(Recipe.category))
            if category:
                statement = statement.where(Recipe.category.has(Category.name == category))
            if status:
                statement = statement.where(Recipe.status == status)
            if search:
                like_value = f"%{search.lower()}%"
                statement = statement.where(Recipe.name.ilike(like_value) | Recipe.description.ilike(like_value))
            statement = statement.order_by(Recipe.updated_at.desc())
            return list(session.exec(statement).all())

    def update_recipe_status(self, recipe_id: int, status: RecipeStatus) -> Recipe:
        with self._session_scope() as session:
            recipe = session.get(Recipe, recipe_id)
            if not recipe:
                raise ValueError("Recipe not found")
            recipe.status = status
            recipe.updated_at = datetime.utcnow()
            logger.info("Recipe %s marked %s", recipe.name, status.value)
            return recipe

    def toggle_recipe_pause(self, recipe_id: int, pause: bool) -> Recipe:
        with self._session_scope() as session:
            recipe = session.get(Recipe, recipe_id)
            if not recipe:
                raise ValueError("Recipe not found")
            recipe.is_paused = pause
            recipe.updated_at = datetime.utcnow()
            logger.info("Recipe %s pause=%s", recipe.name, pause)
            return recipe

    def update_recipe_schedule(self, recipe_id: int, schedule: str | None) -> Recipe:
        with self._session_scope() as session:
            recipe = session.get(Recipe, recipe_id)
            if not recipe:
                raise ValueError("Recipe not found")
            recipe.rerun_schedule = schedule
            recipe.updated_at = datetime.utcnow()
            logger.info("Recipe %s schedule updated to %s", recipe.name, schedule)
            return recipe

    def recipe_detail(self, recipe_id: int) -> Recipe:
        with self._session_scope() as session:
            recipe = session.exec(
                select(Recipe).options(selectinload(Recipe.category)).where(Recipe.id == recipe_id)
            ).first()
            if not recipe:
                raise ValueError("Recipe not found")
            return recipe

    # ------------------------------------------------------------------
    # Executions
    # ------------------------------------------------------------------
    def create_execution(self, recipe: Recipe, context: str | None = None) -> Execution:
        with self._session_scope() as session:
            execution = Execution(recipe_id=recipe.id, status=ExecutionStatus.RUNNING, run_context=context)
            session.add(execution)
            session.commit()
            session.refresh(execution)
            logger.info("Execution %s started for recipe %s", execution.id, recipe.name)
            return execution

    def finish_execution(
        self,
        execution_id: int,
        status: ExecutionStatus,
        log_path: Path | None = None,
        screenshot_path: Path | None = None,
        error_message: str | None = None,
    ) -> Execution:
        with self._session_scope() as session:
            execution = session.get(Execution, execution_id)
            if not execution:
                raise ValueError("Execution not found")
            execution.status = status
            execution.finished_at = datetime.utcnow()
            execution.log_path = str(log_path) if log_path else execution.log_path
            execution.screenshot_path = str(screenshot_path) if screenshot_path else execution.screenshot_path
            execution.error_message = error_message
            session.add(execution)
            if status == ExecutionStatus.FAILED:
                session.add(
                    Notification(
                        type=NotificationType.FAILURE,
                        message=f"Recipe {execution.recipe_id} failed: {error_message}",
                    )
                )
            logger.info("Execution %s finished with %s", execution_id, status.value)
            return execution

    def list_executions(self, recipe_id: int | None = None, status: ExecutionStatus | None = None) -> List[Execution]:
        with self._session_scope() as session:
            statement = select(Execution).options(selectinload(Execution.recipe))
            if recipe_id:
                statement = statement.where(Execution.recipe_id == recipe_id)
            if status:
                statement = statement.where(Execution.status == status)
            statement = statement.order_by(Execution.started_at.desc())
            return list(session.exec(statement).all())

    # ------------------------------------------------------------------
    # Analytics / dashboard helpers
    # ------------------------------------------------------------------
    def dashboard_metrics(self) -> Dict[str, object]:
        with self._session_scope() as session:
            total_recipes = session.exec(select(func.count(Recipe.id))).one()[0]
            total_executions = session.exec(select(func.count(Execution.id))).one()[0]
            successes = session.exec(
                select(func.count(Execution.id)).where(Execution.status == ExecutionStatus.SUCCESS)
            ).one()[0]
            failures = session.exec(
                select(func.count(Execution.id)).where(Execution.status == ExecutionStatus.FAILED)
            ).one()[0]
            category_counts = session.exec(
                select(Category.name, func.count(Recipe.id))
                .join(Recipe, isouter=True)
                .group_by(Category.id)
            ).all()
            category_breakdown = {name: count for name, count in category_counts}
            recent_notifications = list(
                session.exec(select(Notification).order_by(Notification.created_at.desc()).limit(10)).all()
            )
        return {
            "total_recipes": total_recipes,
            "total_executions": total_executions,
            "successes": successes,
            "failures": failures,
            "category_breakdown": category_breakdown,
            "notifications": [
                {
                    "type": notification.type.value,
                    "message": notification.message,
                    "created_at": notification.created_at,
                }
                for notification in recent_notifications
            ],
        }

    # ------------------------------------------------------------------
    # Import / export helpers
    # ------------------------------------------------------------------
    def export_state(self, export_path: Path) -> Path:
        with self._session_scope() as session:
            data = {
                "categories": [category.dict() for category in session.exec(select(Category)).all()],
                "recipes": [recipe.dict() for recipe in session.exec(select(Recipe)).all()],
                "executions": [execution.dict() for execution in session.exec(select(Execution)).all()],
                "recipe_versions": [version.dict() for version in session.exec(select(RecipeVersion)).all()],
                "category_requests": [request.dict() for request in session.exec(select(CategoryRequest)).all()],
            }
        export_path = export_path.with_suffix(".json")
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(data, default=str, indent=2))
        logger.info("Exported state to %s", export_path)
        return export_path

    def import_state(self, import_path: Path) -> None:
        payload = json.loads(Path(import_path).read_text())
        with self._session_scope() as session:
            for table in (Execution, RecipeVersion, Recipe, CategoryRequest, Category):
                session.exec(delete(table))
            session.commit()
            for category_data in payload.get("categories", []):
                session.add(Category(**category_data))
            session.commit()
            for recipe_data in payload.get("recipes", []):
                session.add(Recipe(**recipe_data))
            session.commit()
            for version_data in payload.get("recipe_versions", []):
                session.add(RecipeVersion(**version_data))
            session.commit()
            for execution_data in payload.get("executions", []):
                session.add(Execution(**execution_data))
            session.commit()
            for request_data in payload.get("category_requests", []):
                session.add(CategoryRequest(**request_data))
            session.commit()
        logger.info("Imported state from %s", import_path)


__all__ = ["AdminService", "session_scope"]
