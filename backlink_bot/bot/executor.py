"""Recipe execution logic."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, Optional

from .. import config
from ..db import ExecutionStatus, Recipe, RecipeStatus
from ..services import AdminService
from ..utils.logging import get_logger
from .actions import ActionExecutor, BrowserAction
from .recipe_manager import RecipeManager
from .variables_manager import VariablesManager

logger = get_logger("backlink.executor")


class RecipeExecutor:
    """Orchestrates recipe execution."""

    def __init__(
        self,
        recipe_manager: RecipeManager | None = None,
        variables_manager: VariablesManager | None = None,
        admin_service: AdminService | None = None,
        headless: bool | None = None,
    ) -> None:
        self.admin_service = admin_service or AdminService()
        self.recipe_manager = recipe_manager or RecipeManager(admin_service=self.admin_service)
        self.variables_manager = variables_manager or VariablesManager()
        self.executor = ActionExecutor(headless=headless)

    def execute_recipe(
        self,
        recipe: Recipe,
        runtime_variables: Optional[Dict[str, object]] = None,
        datasets: Optional[Dict[str, str]] = None,
    ) -> Path:
        runtime_variables = runtime_variables or {}
        datasets = datasets or {}
        execution = self.admin_service.create_execution(recipe, context=str(runtime_variables))

        log_path = config.LOG_DIR / f"execution_{execution.id}.log"
        try:
            payload = self.recipe_manager.load_recipe(recipe.name)
            actions_payload = payload.get("actions", [])
            actions = [BrowserAction.from_payload(action) for action in actions_payload]
            action_dicts = [action.to_payload() for action in actions]
            substituted_actions = self.variables_manager.substitute_in_actions(action_dicts, datasets=datasets, runtime=runtime_variables)
            substituted = [BrowserAction.from_payload(action) for action in substituted_actions]

            logs = asyncio.run(
                self.executor.run_actions(
                    substituted,
                    variables=runtime_variables,
                    screenshot_dir=f"execution_{execution.id}",
                )
            )
            log_path.write_text("\n".join(logs), encoding="utf-8")
            self.admin_service.finish_execution(
                execution.id,
                ExecutionStatus.SUCCESS,
                log_path=log_path,
            )
            logger.info("Recipe %s executed successfully", recipe.name)
            return log_path
        except Exception as exc:  # pragma: no cover - execution failure path
            log_path.write_text(str(exc), encoding="utf-8")
            self.admin_service.finish_execution(
                execution.id,
                ExecutionStatus.FAILED,
                log_path=log_path,
                error_message=str(exc),
            )
            logger.exception("Recipe %s execution failed", recipe.name)
            raise

    def execute_by_category(
        self,
        category_name: str,
        runtime_variables: Optional[Dict[str, object]] = None,
        datasets: Optional[Dict[str, str]] = None,
    ) -> None:
        recipes = self.admin_service.list_recipes(category=category_name)
        for recipe in recipes:
            if recipe.status != RecipeStatus.ACTIVE or recipe.is_paused:
                continue
            self.execute_recipe(recipe, runtime_variables=runtime_variables, datasets=datasets)

    def execute_all(self, runtime_variables: Optional[Dict[str, object]] = None, datasets: Optional[Dict[str, str]] = None) -> None:
        recipes = self.admin_service.list_recipes()
        for recipe in recipes:
            if recipe.status != RecipeStatus.ACTIVE or recipe.is_paused:
                continue
            self.execute_recipe(recipe, runtime_variables=runtime_variables, datasets=datasets)


__all__ = ["RecipeExecutor"]
