"""CLI wiring for the automated training recorder."""
from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from typing import Iterable, List

import typer
from rich.console import Console

from .. import config
from ..services import AdminService
from ..utils.logging import get_logger
from .recipe_serializer import materialize_yaml, save_recipe_and_version, slugify
from .trainer_recorder import RecordedAction, RecordingResult, TrainerRecorder

logger = get_logger("backlink.cli.train")


def _cleanup_temporary_artifacts(result: RecordingResult) -> None:
    parents = {path.parent for path in result.screenshots if path}
    for parent in parents:
        try:
            shutil.rmtree(parent)
        except FileNotFoundError:  # pragma: no cover - best effort cleanup
            continue
        except OSError:
            logger.debug("Could not remove temporary directory %s", parent)


def _copy_action(action: RecordedAction) -> RecordedAction:
    return RecordedAction(
        type=action.type,
        selector=action.selector,
        url=action.url,
        value=action.value,
        description=action.description,
        wait_for=action.wait_for,
        meta=dict(action.meta or {}),
    )


def _merge_fill_actions(actions: Iterable[RecordedAction]) -> List[RecordedAction]:
    merged: List[RecordedAction] = []
    for action in actions:
        current = _copy_action(action)
        if (
            current.type == "fill"
            and merged
            and merged[-1].type == "fill"
            and merged[-1].selector == current.selector
        ):
            merged[-1].value = current.value
            merged[-1].meta.update(current.meta or {})
        else:
            merged.append(current)
    return merged


def _describe_selector(selector: str | None) -> str:
    if not selector:
        return "element"
    if selector.startswith("#"):
        return selector
    if selector.startswith("["):
        return selector
    parts = selector.split(" ")
    return parts[-1]


def _build_description(action: RecordedAction) -> str | None:
    meta = action.meta or {}
    if action.type == "goto":
        if action.url:
            return f"Navigate to {action.url}"
        if action.value:
            return f"Navigate to {action.value}"
        return "Navigate"
    if action.type == "click":
        label = meta.get("label") or meta.get("text") or _describe_selector(action.selector)
        return f"Click {label}".strip()
    if action.type == "fill":
        label = meta.get("label") or meta.get("placeholder") or _describe_selector(action.selector)
        if meta.get("input_type", "").lower() == "password" or (action.value == "***"):
            return f"Enter password for {label}" if label else "Enter password"
        return f"Enter {label}" if label else "Enter value"
    if action.type == "select_option":
        label = meta.get("label") or _describe_selector(action.selector)
        labels = meta.get("labels")
        if isinstance(labels, list) and labels:
            choice = ", ".join(item for item in labels if item)
        else:
            choice = action.value or "option"
        if label:
            return f"Select {choice} from {label}"
        return f"Select {choice}"
    if action.type == "wait_for":
        target = _describe_selector(action.selector)
        return f"Wait for {target}"
    if action.type == "screenshot":
        return "Capture screenshot"
    return None


def _add_default_descriptions(actions: Iterable[RecordedAction]) -> List[RecordedAction]:
    described: List[RecordedAction] = []
    for action in actions:
        current = _copy_action(action)
        if not current.description:
            current.description = _build_description(current)
        described.append(current)
    return described


def _time_gap(before: RecordedAction, after: RecordedAction) -> float:
    try:
        before_ts = float((before.meta or {}).get("timestamp"))
        after_ts = float((after.meta or {}).get("timestamp"))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, after_ts - before_ts)


def _insert_waits(actions: List[RecordedAction]) -> List[RecordedAction]:
    enriched: List[RecordedAction] = []
    for idx, action in enumerate(actions):
        enriched.append(action)
        if action.type not in {"click", "goto", "select_option"}:
            continue
        next_action: RecordedAction | None = None
        for candidate in actions[idx + 1 :]:
            if candidate.type in {"wait", "wait_for"}:
                continue
            next_action = candidate
            break
        if not next_action or not next_action.selector:
            continue
        if next_action.selector == action.selector:
            continue
        navigated = bool((action.meta or {}).get("navigated")) or action.type == "goto"
        if not navigated and _time_gap(action, next_action) < 0.75:
            continue
        wait_description = next_action.description or _build_description(next_action)
        wait_action = RecordedAction(
            type="wait_for",
            selector=next_action.selector,
            description=wait_description and f"Wait for {wait_description.split(' ', 1)[-1]}",
            meta={"auto": True, "timestamp": (next_action.meta or {}).get("timestamp")},
        )
        enriched.append(wait_action)
    return enriched


def post_process_actions(actions: List[RecordedAction]) -> List[RecordedAction]:
    merged = _merge_fill_actions(actions)
    described = _add_default_descriptions(merged)
    return _insert_waits(described)


def run_training(console: Console, service: AdminService) -> None:
    categories = service.list_categories()
    if not categories:
        console.print("[yellow]No categories found. Create one first using `categories create`." )
        raise typer.Exit(code=1)

    console.print("Available categories:")
    for idx, category in enumerate(categories, start=1):
        console.print(f" {idx}. {category.name}")
    category_idx = typer.prompt("Select category", type=int)
    try:
        category = categories[category_idx - 1]
    except IndexError:
        console.print("[red]Invalid category selection[/red]")
        raise typer.Exit(code=1)

    name = typer.prompt("Recipe name").strip()
    site = typer.prompt("Target site").strip()
    description = typer.prompt("Description").strip()

    console.print(
        "[cyan]Recording started. Press Ctrl+Shift+Q to finish, Ctrl+Shift+S to capture a screenshot.[/cyan]"
    )
    recorder = TrainerRecorder(headless=False)
    try:
        result = asyncio.run(recorder.record())
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:  # pragma: no cover - CLI convenience
        console.print("[yellow]Recording aborted by user[/yellow]")
        raise typer.Exit(code=1)

    if not result.actions:
        _cleanup_temporary_artifacts(result)
        console.print("[red]No actions recorded; aborting.[/red]")
        raise typer.Exit(code=1)

    processed_actions = post_process_actions(result.actions)
    processed_result = RecordingResult(actions=processed_actions, screenshots=result.screenshots)

    existing = getattr(service, "find_recipe_by_name", None)
    current_recipe = existing(name) if callable(existing) else None
    next_version = current_recipe.version + 1 if current_recipe else 1
    created_at = datetime.utcnow().isoformat()

    meta = {
        "name": name,
        "site": site,
        "description": description,
        "version": next_version,
        "created_at": created_at,
    }
    yaml_text = materialize_yaml(meta, processed_result)
    slug = slugify(name, site)
    recipe_path = save_recipe_and_version(meta, yaml_text, config.RECIPES_DIR, processed_result.screenshots)

    notes = f"Recorded via CLI on {created_at}."
    recipe = service.register_recipe(
        name=name,
        site=site,
        path=recipe_path,
        category=category,
        description=description,
        notes=notes,
    )

    if recipe.version != next_version:
        wrong_dir = config.VERSION_DIR / slug / f"v{next_version:04d}"
        if wrong_dir.exists():
            shutil.rmtree(wrong_dir)
        meta["version"] = recipe.version
        yaml_text = materialize_yaml(meta, processed_result)
        recipe_path = save_recipe_and_version(meta, yaml_text, config.RECIPES_DIR, processed_result.screenshots)

    _cleanup_temporary_artifacts(result)

    version_dir = config.VERSION_DIR / slug / f"v{recipe.version:04d}"
    console.print(
        f"[green]Recipe '{name}' recorded (ID: {recipe.id}, version {recipe.version}).[/green]"
    )
    console.print(f"Main recipe file: {recipe_path}")
    console.print(f"Version snapshot: {version_dir}")
    if processed_result.screenshots:
        console.print(f"Screenshots stored in: {version_dir / 'screenshots'}")


__all__ = ["run_training", "post_process_actions"]

