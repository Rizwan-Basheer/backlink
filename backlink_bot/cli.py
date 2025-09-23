"""Typer based CLI for the backlink bot."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .bot.actions import ActionType
from .bot.executor import RecipeExecutor
from .bot.trainer import Trainer
from .db import CategoryRequestStatus, ExecutionStatus, RecipeStatus, init_db
from .services import AdminService

app = typer.Typer(add_completion=False, help="Backlink Creator CLI")
console = Console()

recipes_app = typer.Typer(help="Manage recipes")
categories_app = typer.Typer(help="Manage categories")
requests_app = typer.Typer(help="Category requests")
executions_app = typer.Typer(help="Monitor executions")

app.add_typer(recipes_app, name="recipes")
app.add_typer(categories_app, name="categories")
app.add_typer(requests_app, name="requests")
app.add_typer(executions_app, name="executions")


@app.command()
def init() -> None:
    """Initialise the database."""
    init_db()
    console.print("[green]Database initialised[/green]")


def _ensure_db() -> None:
    init_db()


@recipes_app.command("list")
def list_recipes(
    category: Optional[str] = typer.Option(None, help="Filter by category name"),
    search: Optional[str] = typer.Option(None, help="Search term"),
) -> None:
    _ensure_db()
    service = AdminService()
    recipes = service.list_recipes(category=category, search=search)
    table = Table("ID", "Name", "Site", "Category", "Status", "Updated")
    for recipe in recipes:
        table.add_row(
            str(recipe.id),
            recipe.name,
            recipe.site,
            recipe.category.name if recipe.category else "-",
            recipe.status.value,
            recipe.updated_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@recipes_app.command("train")
def train_recipe() -> None:
    _ensure_db()
    service = AdminService()
    trainer = Trainer(admin_service=service)
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

    name = typer.prompt("Recipe name")
    site = typer.prompt("Target site")
    description = typer.prompt("Description")

    session = trainer.create_session(name=name, site=site, description=description, category=category)
    console.print("Enter actions (type `done` when finished)")
    while True:
        action_value = typer.prompt("Action type (goto, click, fill, wait_for, wait, select_option, screenshot, done)")
        if action_value.lower() == "done":
            break
        try:
            action_type = ActionType(action_value.lower())
        except ValueError:
            console.print("[red]Unknown action type[/red]")
            continue
        selector = typer.prompt("Selector", default="") or None
        value = typer.prompt("Value", default="") or None
        description_value = typer.prompt("Description", default="") or None
        wait_for = typer.prompt("Wait for selector", default="") or None
        session.record(
            action_type=action_type,
            selector=selector,
            value=value,
            description=description_value,
            wait_for=wait_for,
        )

    if not session.actions:
        console.print("[red]No actions recorded; aborting.[/red]")
        raise typer.Exit(code=1)

    trainer.save_session(session)
    console.print(f"[green]Recipe '{session.name}' saved[/green]")


@recipes_app.command("run")
def run_recipe(recipe_id: int, headless: bool = typer.Option(True, help="Run browser in headless mode")) -> None:
    _ensure_db()
    service = AdminService()
    recipe = service.recipe_detail(recipe_id)
    executor = RecipeExecutor(admin_service=service, headless=headless)
    try:
        executor.execute_recipe(recipe)
    except Exception as exc:  # pragma: no cover - runtime errors
        console.print(f"[red]Execution failed: {exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Recipe '{recipe.name}' executed[/green]")


@recipes_app.command("run-category")
def run_category(category: str) -> None:
    _ensure_db()
    executor = RecipeExecutor()
    executor.execute_by_category(category)
    console.print(f"[green]Triggered execution for category {category}[/green]")


@recipes_app.command("run-all")
def run_all() -> None:
    _ensure_db()
    executor = RecipeExecutor()
    executor.execute_all()
    console.print("[green]Triggered execution for all recipes[/green]")


@recipes_app.command("pause")
def pause_recipe(recipe_id: int) -> None:
    _ensure_db()
    service = AdminService()
    service.toggle_recipe_pause(recipe_id, True)
    console.print(f"Recipe {recipe_id} paused")


@recipes_app.command("resume")
def resume_recipe(recipe_id: int) -> None:
    _ensure_db()
    service = AdminService()
    service.toggle_recipe_pause(recipe_id, False)
    console.print(f"Recipe {recipe_id} resumed")


@recipes_app.command("schedule")
def schedule_recipe(recipe_id: int, schedule: Optional[str] = typer.Option(None, help="cron or keyword schedule")) -> None:
    _ensure_db()
    service = AdminService()
    service.update_recipe_schedule(recipe_id, schedule)
    console.print(f"Recipe {recipe_id} schedule set to {schedule}")


@categories_app.command("list")
def list_categories() -> None:
    _ensure_db()
    service = AdminService()
    categories = service.list_categories(include_inactive=True)
    table = Table("ID", "Name", "Description", "Active")
    for category in categories:
        table.add_row(str(category.id), category.name, category.description or "-", "yes" if category.is_active else "no")
    console.print(table)


@categories_app.command("create")
def create_category(name: str, description: Optional[str] = typer.Option(None)) -> None:
    _ensure_db()
    service = AdminService()
    service.create_category(name, description)
    console.print(f"[green]Category '{name}' created[/green]")


@requests_app.command("submit")
def submit_request(requester: str, name: str, description: Optional[str] = typer.Option(None)) -> None:
    _ensure_db()
    service = AdminService()
    request = service.submit_category_request(requester=requester, requested_name=name, description=description)
    console.print(f"Submitted request #{request.id} for category '{name}'")


@requests_app.command("list")
def list_requests(status: Optional[CategoryRequestStatus] = typer.Option(None)) -> None:
    _ensure_db()
    service = AdminService()
    requests = service.list_category_requests(status=status)
    table = Table("ID", "Requester", "Name", "Status")
    for request in requests:
        table.add_row(str(request.id), request.requester, request.requested_name, request.status.value)
    console.print(table)


@requests_app.command("review")
def review_request(request_id: int, decision: CategoryRequestStatus, reviewer: str = typer.Option("admin")) -> None:
    _ensure_db()
    service = AdminService()
    service.update_category_request(request_id, decision, reviewer)
    console.print(f"Request {request_id} marked {decision.value}")


@executions_app.command("list")
def list_executions(status: Optional[ExecutionStatus] = typer.Option(None), limit: int = typer.Option(20)) -> None:
    _ensure_db()
    service = AdminService()
    executions = service.list_executions(status=status)[:limit]
    table = Table("ID", "Recipe", "Status", "Started", "Finished")
    for execution in executions:
        table.add_row(
            str(execution.id),
            execution.recipe.name if execution.recipe else str(execution.recipe_id),
            execution.status.value,
            execution.started_at.strftime("%Y-%m-%d %H:%M"),
            execution.finished_at.strftime("%Y-%m-%d %H:%M") if execution.finished_at else "-",
        )
    console.print(table)


@app.command("export")
def export_state(output: Path = typer.Option(Path("data/export"), help="Output file without extension")) -> None:
    _ensure_db()
    service = AdminService()
    path = service.export_state(output)
    console.print(f"Exported state to {path}")


@app.command("import")
def import_state(input_path: Path) -> None:
    _ensure_db()
    service = AdminService()
    service.import_state(input_path)
    console.print("Import completed")


@app.command("serve-admin")
def serve_admin(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the FastAPI admin panel."""
    init_db()
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - uvicorn optional
        console.print("[red]uvicorn is required to run the admin server[/red]")
        raise typer.Exit(code=1) from exc
    uvicorn.run("backlink_bot.admin.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
