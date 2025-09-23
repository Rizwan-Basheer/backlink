"""Utilities to convert recorded sessions into recipe files."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from .. import config
from ..utils.logging import get_logger
from .trainer_recorder import RecordedAction, RecordingResult

logger = get_logger("backlink.recipe.serializer")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str, site: str) -> str:
    """Create a filesystem-friendly slug for recipe assets."""

    raw = f"{name}-{site}".lower()
    slug = _SLUG_RE.sub("-", raw).strip("-")
    return slug or "recipe"


def _action_to_payload(action: RecordedAction) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": action.type}
    if action.selector:
        payload["selector"] = action.selector
    if action.url:
        payload["url"] = action.url
    if action.value is not None:
        payload["value"] = action.value
    if action.description:
        payload["description"] = action.description
    if action.wait_for:
        payload["wait_for"] = action.wait_for
    return payload


def materialize_yaml(meta: dict[str, Any], result: RecordingResult) -> str:
    """Serialise meta data and actions into a YAML recipe payload."""

    payload: dict[str, Any] = {
        "name": meta["name"],
        "site": meta["site"],
        "description": meta.get("description", ""),
        "version": int(meta["version"]),
        "created_at": meta["created_at"],
        "actions": [_action_to_payload(action) for action in result.actions],
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def save_recipe_and_version(
    meta: dict[str, Any],
    yaml_text: str,
    base_dir: Path,
    screenshots: list[Path],
) -> Path:
    """Persist the recipe YAML and copy artefacts to the version snapshot."""

    slug = slugify(meta["name"], meta["site"])
    recipe_path = base_dir / f"{slug}.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(yaml_text, encoding="utf-8")
    logger.info("Recipe file written to %s", recipe_path)

    version = int(meta["version"])
    version_dir = config.VERSION_DIR / slug / f"v{version:04d}"
    if version_dir.exists():
        shutil.rmtree(version_dir)
    version_dir.mkdir(parents=True, exist_ok=True)
    version_recipe_path = version_dir / "recipe.yaml"
    version_recipe_path.write_text(yaml_text, encoding="utf-8")

    if screenshots:
        screenshot_dir = version_dir / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        for source in screenshots:
            source_path = Path(source)
            if not source_path.exists():
                continue
            target = screenshot_dir / source_path.name
            shutil.copy2(source_path, target)
            logger.debug("Copied screenshot %s -> %s", source_path, target)

    return recipe_path


__all__ = ["slugify", "materialize_yaml", "save_recipe_and_version"]

