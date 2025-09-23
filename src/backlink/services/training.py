"""Helpers for recording user interactions into reusable recipes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .recipes import RecipeAction, RecipeDefinition, RecipeMetadata


@dataclass
class TrainingSession:
    """In-memory representation of an ongoing recording session."""

    metadata: RecipeMetadata
    actions: List[RecipeAction] = field(default_factory=list)
    variables: Dict[str, str] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    content_requirements: Dict[str, Any] = field(default_factory=dict)

    def add_action(
        self,
        *,
        name: str,
        action: str,
        selector: str | None = None,
        value: str | None = None,
        wait_for: float | None = None,
        screenshot: bool = False,
    ) -> None:
        self.actions.append(
            RecipeAction(
                name=name,
                action=action,
                selector=selector,
                value=value,
                wait_for=wait_for,
                screenshot=screenshot,
            )
        )

    def to_definition(self) -> RecipeDefinition:
        return RecipeDefinition(
            metadata=self.metadata,
            actions=self.actions,
            variables=self.variables,
            config=self.config,
            content_requirements=self.content_requirements,
        )


class RecipeTrainer:
    """Manage multiple concurrent training sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, TrainingSession] = {}

    def start_session(
        self,
        session_id: str,
        *,
        name: str,
        site: str,
        description: str | None,
        category_id: int,
    ) -> TrainingSession:
        metadata = RecipeMetadata(
            name=name,
            site=site,
            description=description,
            category_id=category_id,
        )
        session = TrainingSession(metadata=metadata)
        self._sessions[session_id] = session
        return session

    def record_action(self, session_id: str, **kwargs: Any) -> RecipeAction:
        session = self._get_session(session_id)
        session.add_action(**kwargs)
        return session.actions[-1]

    def update_variables(self, session_id: str, variables: Dict[str, str]) -> None:
        session = self._get_session(session_id)
        session.variables.update(variables)

    def update_content_requirements(self, session_id: str, requirements: Dict[str, Any]) -> None:
        session = self._get_session(session_id)
        session.content_requirements.update(requirements)

    def finish_session(self, session_id: str) -> RecipeDefinition:
        session = self._sessions.pop(session_id)
        return session.to_definition()

    def cancel_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_sessions(self) -> dict[str, TrainingSession]:
        return dict(self._sessions)

    def _get_session(self, session_id: str) -> TrainingSession:
        if session_id not in self._sessions:
            raise KeyError(f"session '{session_id}' not found")
        return self._sessions[session_id]


__all__ = ["RecipeTrainer", "TrainingSession"]
