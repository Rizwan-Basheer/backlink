"""Recipe persistence helpers."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from .. import config
from ..db import Category
from ..services import AdminService
from ..utils.logging import get_logger
from .actions import BrowserAction

logger = get_logger("backlink.recipe_manager")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class RecipeManager:
    """Manage recipe files on disk and register them in the database."""

    def __init__(self, admin_service: Optional[AdminService] = None) -> None:
        self.admin_service = admin_service or AdminService()

    def sanitize_name(self, name: str) -> str:
        slug = _SLUG_RE.sub("-", name.lower()).strip("-")
        if not slug:
            raise ValueError("Recipe name must contain alphanumeric characters")
        return slug

    def recipe_path(self, name: str, version: int | None = None) -> Path:
        safe_name = self.sanitize_name(name)
        if version:
            version_dir = config.VERSION_DIR / safe_name
            version_dir.mkdir(parents=True, exist_ok=True)
            return version_dir / f"{safe_name}_v{version:04d}.yaml"
        return config.RECIPES_DIR / f"{safe_name}.yaml"

    def save_recipe(
        self,
        name: str,
        site: str,
        description: str,
        category: Category,
        actions: Iterable[BrowserAction],
        metadata: Optional[Dict[str, str]] = None,
        notes: Optional[str] = None,
    ) -> Path:
        safe_name = self.sanitize_name(name)
        path = self.recipe_path(safe_name)
        payload = {
            "meta": {
                "name": name,
                "site": site,
                "description": description,
                "category": category.name,
            }
        }
        payload["meta"].update(metadata or {})
        payload["actions"] = [action.to_payload() for action in actions]

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False)
        logger.info("Saved recipe '%s' to %s", name, path)

        recipe = self.admin_service.register_recipe(
            name=name,
            site=site,
            path=path,
            category=category,
            description=description,
            notes=notes,
        )

        version_path = self.recipe_path(safe_name, recipe.version)
        if version_path != path:
            version_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Created version snapshot %s", version_path)

        return path

    def load_recipe(self, name: str) -> Dict[str, object]:
        path = self.recipe_path(name)
        if not path.exists():
            raise FileNotFoundError(f"Recipe '{name}' not found")
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data

    def delete_recipe(self, name: str) -> None:
        path = self.recipe_path(name)
        if path.exists():
            path.unlink()
            logger.info("Deleted recipe file %s", path)

    def list_recipe_files(self) -> List[Path]:
        return sorted(config.RECIPES_DIR.glob("*.yaml"))


__all__ = ["RecipeManager"]
