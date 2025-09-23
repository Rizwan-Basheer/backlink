"""Recipe management services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional

from pydantic import BaseModel, Field, validator
from sqlmodel import Session, select

from ..config import RECIPES_DIR
from ..models import Category, Recipe, RecipeStatus, RecipeVersion
from ..utils.files import read_yaml, write_yaml
from ..utils.strings import slugify


class RecipeAction(BaseModel):
    """Representation of a single automation step."""

    name: str
    action: str
    selector: str | None = None
    value: str | None = None
    wait_for: float | None = Field(default=None, ge=0)
    screenshot: bool = False


class RecipeMetadata(BaseModel):
    name: str
    site: str
    description: str | None = None
    category_id: int
    status: RecipeStatus = RecipeStatus.TRAINING


class RecipeDefinition(BaseModel):
    metadata: RecipeMetadata
    actions: list[RecipeAction]
    variables: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    content_requirements: dict[str, Any] = Field(default_factory=dict)

    @validator("actions")
    def validate_actions(cls, value: Iterable[RecipeAction]) -> List[RecipeAction]:
        items = list(value)
        if not items:
            raise ValueError("a recipe must contain at least one action")
        return items


@dataclass
class RecipeSummary:
    id: int
    name: str
    site: str
    category: str
    status: RecipeStatus
    version: int
    owner: str | None


class RecipeManager:
    """High level interface for storing and retrieving recipes."""

    def __init__(self, session: Session):
        self.session = session

    # Public API ---------------------------------------------------------
    def list_recipes(self) -> list[RecipeSummary]:
        statement = select(Recipe, Category).join(Category, Recipe.category_id == Category.id)
        result = self.session.exec(statement).all()
        summaries: list[RecipeSummary] = []
        for recipe, category in result:
            version = self._current_version_number(recipe)
            owner = recipe.owner.username if recipe.owner else None
            summaries.append(
                RecipeSummary(
                    id=recipe.id,
                    name=recipe.name,
                    site=recipe.site,
                    category=category.name,
                    status=recipe.status,
                    version=version,
                    owner=owner,
                )
            )
        return summaries

    def get_definition(self, recipe: Recipe | int) -> RecipeDefinition:
        recipe_obj = self._resolve_recipe(recipe)
        version = self._current_version(recipe_obj)
        if not version:
            raise ValueError("recipe does not have a stored version")
        yaml_path = Path(version.yaml_path)
        data = read_yaml(yaml_path)
        metadata = RecipeMetadata(**data.get("metadata", {}))
        actions = [RecipeAction(**item) for item in data.get("actions", [])]
        variables = data.get("variables", {})
        config = data.get("config", {})
        content_requirements = data.get("content_requirements", {})
        return RecipeDefinition(
            metadata=metadata,
            actions=actions,
            variables=variables,
            config=config,
            content_requirements=content_requirements,
        )

    def create_recipe(
        self,
        definition: RecipeDefinition,
        *,
        owner_id: Optional[int] = None,
    ) -> Recipe:
        slug = slugify(definition.metadata.name)
        existing = self.session.exec(select(Recipe).where(Recipe.slug == slug)).first()
        counter = 2
        unique_slug = slug
        while existing:
            unique_slug = f"{slug}-{counter}"
            existing = self.session.exec(select(Recipe).where(Recipe.slug == unique_slug)).first()
            counter += 1
        slug = unique_slug
        recipe_dir = RECIPES_DIR / slug
        version_number = 1
        yaml_path = recipe_dir / f"{slug}_v{version_number}.yaml"

        self._write_definition(yaml_path, definition)

        recipe = Recipe(
            name=definition.metadata.name,
            site=definition.metadata.site,
            slug=slug,
            description=definition.metadata.description,
            status=definition.metadata.status,
            category_id=definition.metadata.category_id,
            owner_id=owner_id,
        )
        self.session.add(recipe)
        self.session.flush()

        version = RecipeVersion(
            recipe_id=recipe.id,
            version=version_number,
            yaml_path=str(yaml_path),
            change_summary="Initial recording",
        )
        self.session.add(version)
        self.session.flush()

        recipe.current_version_id = version.id
        self.session.add(recipe)
        self.session.flush()
        return recipe

    def update_recipe(
        self,
        recipe: Recipe | int,
        definition: RecipeDefinition,
        *,
        change_summary: str,
    ) -> Recipe:
        recipe_obj = self._resolve_recipe(recipe)
        slug = recipe_obj.slug
        recipe_dir = RECIPES_DIR / slug
        version_number = self._next_version_number(recipe_obj)
        yaml_path = recipe_dir / f"{slug}_v{version_number}.yaml"
        self._write_definition(yaml_path, definition)

        version = RecipeVersion(
            recipe_id=recipe_obj.id,
            version=version_number,
            yaml_path=str(yaml_path),
            change_summary=change_summary,
        )
        self.session.add(version)
        self.session.flush()

        recipe_obj.current_version_id = version.id
        recipe_obj.updated_at = datetime.utcnow()
        recipe_obj.name = definition.metadata.name
        recipe_obj.site = definition.metadata.site
        recipe_obj.description = definition.metadata.description
        recipe_obj.category_id = definition.metadata.category_id
        recipe_obj.status = definition.metadata.status
        self.session.add(recipe_obj)
        self.session.flush()
        return recipe_obj

    def mark_status(self, recipe: Recipe | int, status: RecipeStatus) -> Recipe:
        recipe_obj = self._resolve_recipe(recipe)
        recipe_obj.status = status
        self.session.add(recipe_obj)
        self.session.flush()
        return recipe_obj

    def export_recipe(self, recipe: Recipe | int, destination: Path) -> Path:
        recipe_obj = self._resolve_recipe(recipe)
        version = self._current_version(recipe_obj)
        if not version:
            raise ValueError("recipe has no stored version")
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = read_yaml(Path(version.yaml_path))
        write_yaml(destination, data)
        return destination

    def delete_recipe(self, recipe: Recipe | int) -> None:
        recipe_obj = self._resolve_recipe(recipe)
        for version in recipe_obj.versions:
            path = Path(version.yaml_path)
            if path.exists():
                path.unlink()
        recipe_dir = RECIPES_DIR / recipe_obj.slug
        if recipe_dir.exists():
            import shutil

            shutil.rmtree(recipe_dir)
        self.session.delete(recipe_obj)
        self.session.flush()

    # Internal helpers ---------------------------------------------------
    def _resolve_recipe(self, recipe: Recipe | int) -> Recipe:
        if isinstance(recipe, Recipe):
            return recipe
        result = self.session.get(Recipe, recipe)
        if not result:
            raise ValueError(f"recipe {recipe} not found")
        return result

    def _current_version(self, recipe: Recipe) -> RecipeVersion | None:
        if not recipe.current_version_id:
            return None
        return self.session.get(RecipeVersion, recipe.current_version_id)

    def _current_version_number(self, recipe: Recipe) -> int:
        version = self._current_version(recipe)
        return version.version if version else 0

    def _next_version_number(self, recipe: Recipe) -> int:
        version = self._current_version(recipe)
        return (version.version if version else 0) + 1

    def _write_definition(self, path: Path, definition: RecipeDefinition) -> None:
        data = {
            "metadata": definition.metadata.dict(),
            "actions": [action.dict() for action in definition.actions],
            "variables": definition.variables,
            "config": definition.config,
            "content_requirements": definition.content_requirements,
        }
        write_yaml(path, data)


__all__ = [
    "RecipeAction",
    "RecipeMetadata",
    "RecipeDefinition",
    "RecipeSummary",
    "RecipeManager",
]
