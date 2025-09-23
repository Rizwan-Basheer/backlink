"""High-level helper to record actions and persist recipes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..db import Category
from ..services import AdminService
from ..utils.logging import get_logger
from .actions import ActionType, BrowserAction
from .recipe_manager import RecipeManager

logger = get_logger("backlink.trainer")


@dataclass
class TrainingSession:
    name: str
    site: str
    description: str
    category: Category
    metadata: dict[str, str] = field(default_factory=dict)
    actions: List[BrowserAction] = field(default_factory=list)

    def record(
        self,
        action_type: ActionType,
        selector: str | None = None,
        value: str | None = None,
        description: str | None = None,
        wait_for: str | None = None,
    ) -> None:
        action = BrowserAction(
            type=action_type,
            selector=selector,
            value=value,
            description=description,
            wait_for=wait_for,
        )
        logger.info("Recorded action %s for session %s", action_type, self.name)
        self.actions.append(action)


class Trainer:
    """Create and manage training sessions."""

    def __init__(self, recipe_manager: RecipeManager | None = None, admin_service: AdminService | None = None) -> None:
        self.admin_service = admin_service or AdminService()
        self.recipe_manager = recipe_manager or RecipeManager(admin_service=self.admin_service)

    def create_session(
        self,
        name: str,
        site: str,
        description: str,
        category: Category,
        metadata: Optional[dict[str, str]] = None,
    ) -> TrainingSession:
        return TrainingSession(name=name, site=site, description=description, category=category, metadata=metadata or {})

    def save_session(self, session: TrainingSession, notes: str | None = None) -> None:
        if not session.actions:
            raise ValueError("Cannot save an empty recipe")
        self.recipe_manager.save_recipe(
            name=session.name,
            site=session.site,
            description=session.description,
            category=session.category,
            actions=session.actions,
            metadata=session.metadata,
            notes=notes,
        )
        logger.info("Training session %s saved", session.name)


__all__ = ["Trainer", "TrainingSession"]
