"""Recipe execution services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlmodel import Session

from ..actions.playwright import PlaywrightActionRunner, PlaywrightRunResult
from ..config import EXECUTION_DIR, SCREENSHOT_DIR, TROUBLESHOOT_RETRIES
from ..models import Execution, ExecutionStatus, Recipe, TargetURL
from ..utils.logging import create_execution_logger
from .admin import AdminService
from .recipes import RecipeDefinition, RecipeManager
from .variables import VariablesManager


@dataclass
class _PreparedExecution:
    recipe: Recipe
    definition: RecipeDefinition
    target: TargetURL
    variables: dict[str, Any]
    rendered_actions: list[dict[str, Any]]


class RecipeExecutor:
    """Execute recorded recipes using the configured action runner."""

    def __init__(
        self,
        session: Session,
        *,
        variables: VariablesManager | None = None,
        runner: PlaywrightActionRunner | None = None,
        admin_service: AdminService | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.session = session
        self.variables = variables or VariablesManager()
        self.runner = runner or PlaywrightActionRunner()
        self.admin = admin_service or AdminService(session)
        default_attempts = TROUBLESHOOT_RETRIES + 1
        self.max_retries = max(1, max_retries or default_attempts)

    def execute_recipe(
        self,
        recipe: Recipe | int,
        *,
        target: TargetURL | int,
        runtime_variables: Mapping[str, Any] | None = None,
        datasets: Mapping[str, Any] | None = None,
        dry_run: bool = False,
        headless: bool | None = None,
        refresh_content: bool = False,
    ) -> Execution:
        prepared = self._prepare_execution(
            recipe,
            target=target,
            runtime_variables=runtime_variables,
            datasets=datasets,
            refresh_content=refresh_content,
        )

        execution = Execution(
            recipe_id=prepared.recipe.id,
            target_id=prepared.target.id,
            status=ExecutionStatus.PENDING,
        )
        self.session.add(execution)
        self.session.flush()

        log_path = EXECUTION_DIR / prepared.recipe.slug / f"execution_{execution.id}.log"
        execution.log_path = str(log_path)
        self.session.add(execution)
        self.session.flush()

        logger = create_execution_logger(log_path)
        logger.info(
            "Starting execution for '%s' on %s (target=%s)",
            prepared.recipe.name,
            prepared.recipe.site,
            prepared.target.url,
        )

        execution.status = ExecutionStatus.RUNNING
        self.session.add(execution)
        self.session.flush()

        screenshot_dir = SCREENSHOT_DIR / prepared.recipe.slug / f"execution_{execution.id}"
        runner_config = dict(prepared.definition.config)
        if headless is not None:
            runner_config["headless"] = headless

        try:
            if dry_run:
                for action in prepared.rendered_actions:
                    logger.info("DRY RUN %s", self._mask_action(action))
                execution.status = ExecutionStatus.SUCCESS
            else:
                result = self.runner.run(
                    prepared.rendered_actions,
                    logger=logger,
                    config=runner_config,
                    troubleshoot=self.admin.llm_troubleshoot,
                    max_attempts=self.max_retries,
                    screenshot_dir=screenshot_dir,
                )
                if isinstance(result, PlaywrightRunResult):
                    execution.screenshot_path = result.last_screenshot
                execution.status = ExecutionStatus.SUCCESS
            execution.finished_at = datetime.utcnow()
            prepared.recipe.last_executed_at = execution.finished_at
            self.session.add(prepared.recipe)
        except Exception as exc:  # pragma: no cover - failure path
            execution.status = ExecutionStatus.FAILURE
            execution.error_message = str(exc)
            execution.finished_at = datetime.utcnow()
            if screenshot_dir.exists():
                screenshots = sorted(screenshot_dir.glob("failure_*.png"))
                if screenshots:
                    execution.screenshot_path = str(screenshots[-1])
            logger.exception("Execution failed: %s", exc)
        finally:
            self.session.add(execution)
            self.session.flush()

        return execution

    def plan_recipe(
        self,
        recipe: Recipe | int,
        *,
        target: TargetURL | int,
        runtime_variables: Mapping[str, Any] | None = None,
        datasets: Mapping[str, Any] | None = None,
        refresh_content: bool = False,
    ) -> Sequence[dict[str, Any]]:
        prepared = self._prepare_execution(
            recipe,
            target=target,
            runtime_variables=runtime_variables,
            datasets=datasets,
            refresh_content=refresh_content,
        )
        return prepared.rendered_actions

    # Internal helpers ---------------------------------------------------
    def _prepare_execution(
        self,
        recipe: Recipe | int,
        *,
        target: TargetURL | int,
        runtime_variables: Mapping[str, Any] | None,
        datasets: Mapping[str, Any] | None,
        refresh_content: bool,
    ) -> _PreparedExecution:
        recipe_obj = self._resolve_recipe(recipe)
        manager = RecipeManager(self.session)
        definition = manager.get_definition(recipe_obj)
        target_obj = self.admin.get_target(target)
        if not target_obj.title or not target_obj.summary or not target_obj.keywords:
            target_obj = self.admin.fetch_and_enrich_target(target_obj)

        generated = self.admin.generate_content_for_target(
            target_obj,
            recipe=recipe_obj,
            style_hints=definition.content_requirements,
            refresh=refresh_content,
        )
        target_variables = self.admin.resolve_runtime_variables(target_obj, generated)

        merged_variables: dict[str, Any] = dict(definition.variables)
        merged_variables.update(target_variables)
        if runtime_variables:
            merged_variables.update(runtime_variables)
        if datasets:
            container = merged_variables.get("datasets")
            if isinstance(container, Mapping):
                container = dict(container)
            else:
                container = {}
            container.update(datasets)
            merged_variables["datasets"] = container

        rendered_actions = [
            self._render_action(action, merged_variables) for action in definition.actions
        ]

        return _PreparedExecution(
            recipe=recipe_obj,
            definition=definition,
            target=target_obj,
            variables=merged_variables,
            rendered_actions=rendered_actions,
        )

    def _resolve_recipe(self, recipe: Recipe | int) -> Recipe:
        if isinstance(recipe, Recipe):
            return recipe
        result = self.session.get(Recipe, recipe)
        if not result:
            raise ValueError(f"recipe {recipe} not found")
        return result

    def _render_action(self, action: Mapping[str, Any], variables: Mapping[str, Any]) -> dict[str, Any]:
        if hasattr(action, "dict"):
            payload = dict(action.dict())  # type: ignore[attr-defined]
        else:
            payload = dict(action)
        resolved: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                resolved[key] = self.variables.substitute_placeholders(value, variables)
            else:
                resolved[key] = value
        return resolved

    def _mask_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        masked: dict[str, Any] = {}
        for key, value in action.items():
            if key in {"value", "text"} and isinstance(value, str):
                masked[key] = self._redact(value)
            else:
                masked[key] = value
        return masked

    @staticmethod
    def _redact(value: str) -> str:
        lowered = value.lower()
        if any(token in lowered for token in ("password", "secret", "token")):
            return "***"
        return value


__all__ = ["RecipeExecutor"]
