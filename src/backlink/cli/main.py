"""Command line interface for the Backlink automation framework."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from ..database import init_db, session_scope
from ..models import Category, Execution, Recipe, RecipeStatus
from ..services.admin import AdminService
from ..services.auth import AuthService
from ..services.analytics import AnalyticsService
from ..services.executor import RecipeExecutor
from ..services.recipes import RecipeAction, RecipeDefinition, RecipeManager, RecipeMetadata
from ..utils.files import read_yaml

app = typer.Typer(help="Backlink automation CLI")
recipes_app = typer.Typer(help="Manage recipes")
executions_app = typer.Typer(help="Execute recipes and show history")
analytics_app = typer.Typer(help="Reporting and analytics")
targets_app = typer.Typer(help="Manage backlink targets")
users_app = typer.Typer(help="User management")
app.add_typer(recipes_app, name="recipes")
app.add_typer(executions_app, name="executions")
app.add_typer(analytics_app, name="analytics")
app.add_typer(targets_app, name="targets")
app.add_typer(users_app, name="users")

console = Console()


@app.command("init-db")
def init_db_command() -> None:
    """Initialise the SQLite database."""

    init_db()
    console.print("[green]Database initialised[/green]")


@recipes_app.command("list")
def list_recipes() -> None:
    with session_scope() as session:
        manager = RecipeManager(session)
        recipes = manager.list_recipes()
    table = Table(title="Recipes")
    table.add_column("ID", justify="right")
    table.add_column("Name")
    table.add_column("Site")
    table.add_column("Category")
    table.add_column("Status")
    table.add_column("Version")
    table.add_column("Owner")
    for recipe in recipes:
        table.add_row(
            str(recipe.id),
            recipe.name,
            recipe.site,
            recipe.category,
            recipe.status.value,
            str(recipe.version),
            recipe.owner or "-",
        )
    console.print(table)


@recipes_app.command("create")
def create_recipe(
    definition_path: Path = typer.Argument(..., help="Path to recipe YAML"),
    category_id: int = typer.Option(..., help="Category identifier"),
    site: str = typer.Option(..., help="Target website"),
    name: str = typer.Option(..., help="Recipe name"),
    description: str = typer.Option("", help="Optional description"),
    status: RecipeStatus = typer.Option(RecipeStatus.READY, help="Recipe status"),
) -> None:
    data = read_yaml(definition_path)
    if not data:
        raise typer.BadParameter("definition file is empty", param_name="definition_path")
    metadata = RecipeMetadata(
        name=name,
        site=site,
        description=description or data.get("metadata", {}).get("description"),
        category_id=category_id,
        status=status,
    )
    actions_data = data.get("actions", data)
    if isinstance(actions_data, dict):
        actions_data = actions_data.get("actions", [])
    if not isinstance(actions_data, list) or not actions_data:
        raise typer.BadParameter("definition must include an actions list", param_name="definition_path")
    actions = [RecipeAction(**action) for action in actions_data]
    definition = RecipeDefinition(
        metadata=metadata,
        actions=actions,
        variables=data.get("variables", {}),
        config=data.get("config", {}),
        content_requirements=data.get("content_requirements", {}),
    )
    with session_scope() as session:
        manager = RecipeManager(session)
        recipe = manager.create_recipe(definition)
    console.print(f"[green]Recipe created with id {recipe.id}[/green]")


@recipes_app.command("export")
def export_recipe(recipe_id: int, destination: Path) -> None:
    with session_scope() as session:
        manager = RecipeManager(session)
        manager.export_recipe(recipe_id, destination)
    console.print(f"[green]Recipe exported to {destination}[/green]")


@recipes_app.command("delete")
def delete_recipe(recipe_id: int) -> None:
    with session_scope() as session:
        manager = RecipeManager(session)
        manager.delete_recipe(recipe_id)
    console.print(f"[yellow]Recipe {recipe_id} deleted[/yellow]")


@executions_app.command("run")
def run_recipe(
    recipe_id: int,
    target: int = typer.Option(..., help="Target URL identifier"),
    dry_run: bool = typer.Option(False, help="Log actions without running Playwright"),
    headless: bool = typer.Option(True, help="Run browser in headless mode"),
    refresh_content: bool = typer.Option(False, help="Ignore cached generated content"),
) -> None:
    with session_scope() as session:
        executor = RecipeExecutor(session)
        execution = executor.execute_recipe(
            recipe_id,
            target=target,
            dry_run=dry_run,
            headless=headless,
            refresh_content=refresh_content,
        )
        console.print(
            f"[green]Execution {execution.id} finished with status {execution.status.value}. Log: {execution.log_path}[/green]"
        )


@executions_app.command("history")
def execution_history(limit: int = typer.Option(10, help="Number of executions")) -> None:
    with session_scope() as session:
        rows = (
            session.exec(
                select(Execution).order_by(Execution.started_at.desc()).limit(limit)
            ).all()
        )
    table = Table(title="Execution history")
    table.add_column("ID", justify="right")
    table.add_column("Recipe")
    table.add_column("Status")
    table.add_column("Started")
    table.add_column("Finished")
    table.add_column("Log")
    table.add_column("Target")
    for execution in rows:
        table.add_row(
            str(execution.id),
            str(execution.recipe_id),
            execution.status.value,
            execution.started_at.isoformat(sep=" ", timespec="seconds"),
            execution.finished_at.isoformat(sep=" ", timespec="seconds") if execution.finished_at else "-",
            execution.log_path or "-",
            str(execution.target_id or "-"),
        )
    console.print(table)


@recipes_app.command("plan")
def plan_recipe(
    recipe_id: int,
    target: int = typer.Option(..., help="Target URL identifier"),
) -> None:
    with session_scope() as session:
        executor = RecipeExecutor(session)
        planned_actions = executor.plan_recipe(recipe_id, target=target)
    table = Table(title=f"Planned actions for recipe {recipe_id}")
    table.add_column("#", justify="right")
    table.add_column("Action")
    table.add_column("Selector")
    table.add_column("Value")
    for index, action in enumerate(planned_actions, start=1):
        table.add_row(
            str(index),
            str(action.get("action")),
            action.get("selector") or "-",
            str(action.get("value")) if action.get("value") else "-",
        )
    console.print(table)


@targets_app.command("add")
def add_target(
    url: str,
    title: Optional[str] = typer.Option(None, help="Optional title"),
    description: Optional[str] = typer.Option(None, help="Optional description"),
    keywords: Optional[str] = typer.Option(None, help="Comma separated keywords"),
    summary: Optional[str] = typer.Option(None, help="Short summary"),
) -> None:
    with session_scope() as session:
        admin = AdminService(session)
        target = admin.register_target_url(
            url,
            title=title,
            description=description,
            keywords=keywords,
            summary=summary,
        )
    console.print(f"[green]Target registered with id {target.id}[/green]")


@targets_app.command("list")
def list_targets(search: Optional[str] = typer.Option(None, help="Filter targets")) -> None:
    with session_scope() as session:
        admin = AdminService(session)
        targets = admin.list_targets(search)
    table = Table(title="Targets")
    table.add_column("ID", justify="right")
    table.add_column("URL")
    table.add_column("Title")
    table.add_column("Updated")
    for target in targets:
        table.add_row(
            str(target.id),
            target.url,
            target.title or "-",
            target.updated_at.isoformat(sep=" ", timespec="seconds"),
        )
    console.print(table)


@targets_app.command("enrich")
def enrich_target(target_id: int) -> None:
    with session_scope() as session:
        admin = AdminService(session)
        target = admin.fetch_and_enrich_target(target_id)
    console.print(
        f"[green]Target {target.id} enriched. Title='{target.title}' Keywords='{target.keywords or ''}'[/green]"
    )


@app.command("run-target")
def run_target(
    target: int = typer.Option(..., help="Target URL identifier"),
    category: Optional[str] = typer.Option(None, help="Filter recipes by category name"),
    headless: bool = typer.Option(True, help="Run browsers headless"),
    refresh_content: bool = typer.Option(False, help="Refresh generated assets"),
    dry_run: bool = typer.Option(False, help="Log actions without driving the browser"),
) -> None:
    with session_scope() as session:
        admin = AdminService(session)
        target_obj = admin.get_target(target)
        query = select(Recipe).where(Recipe.is_active == True, Recipe.status == RecipeStatus.READY)
        if category:
            query = query.join(Category, Recipe.category_id == Category.id).where(Category.name == category)
        recipes = session.exec(query).all()
        if not recipes:
            console.print("[yellow]No active recipes found for the selection[/yellow]")
            return
        executor = RecipeExecutor(session)
        for recipe in recipes:
            execution = executor.execute_recipe(
                recipe,
                target=target_obj.id,
                headless=headless,
                refresh_content=refresh_content,
                dry_run=dry_run,
            )
            console.print(
                f"[cyan]Recipe {recipe.id} executed for target {target_obj.id} -> {execution.status.value}[/cyan]"
            )


@app.command("run-queue")
def run_queue(
    category: Optional[str] = typer.Option(None, help="Filter recipes by category"),
    limit: Optional[int] = typer.Option(None, help="Limit the number of targets"),
    headless: bool = typer.Option(True, help="Run browsers headless"),
    refresh_content: bool = typer.Option(False, help="Refresh generated content"),
    dry_run: bool = typer.Option(False, help="Dry run without browser"),
) -> None:
    with session_scope() as session:
        admin = AdminService(session)
        targets = admin.list_targets()
        if limit is not None:
            targets = targets[:limit]
        query = select(Recipe).where(Recipe.is_active == True, Recipe.status == RecipeStatus.READY)
        if category:
            query = query.join(Category, Recipe.category_id == Category.id).where(Category.name == category)
        recipes = session.exec(query).all()
        if not recipes:
            console.print("[yellow]No recipes available for the selected filters[/yellow]")
            return
        executor = RecipeExecutor(session)
        for target_obj in targets:
            for recipe in recipes:
                execution = executor.execute_recipe(
                    recipe,
                    target=target_obj.id,
                    headless=headless,
                    refresh_content=refresh_content,
                    dry_run=dry_run,
                )
                console.print(
                    f"[cyan]Recipe {recipe.id} executed for target {target_obj.id} -> {execution.status.value}[/cyan]"
                )


@users_app.command("seed-admin")
def seed_admin_user(
    email: str,
    name: str = typer.Option(..., "--name", help="Display name for the admin user"),
    password: str = typer.Option(
        ..., "--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Password for the admin"
    ),
) -> None:
    """Create or update an administrative user."""

    with session_scope() as session:
        auth = AuthService(session)
        user = auth.seed_admin(email, name=name, password=password)
    console.print(f"[green]Admin user ready: {user.email} (id={user.id})[/green]")


@analytics_app.command("summary")
def analytics_summary() -> None:
    with session_scope() as session:
        analytics = AnalyticsService(session)
        total_backlinks = analytics.total_backlinks()
        categories = analytics.recipes_per_category()
        execution_summary = analytics.execution_summary()
    console.print(f"Total backlinks created: [bold]{total_backlinks}[/bold]")
    table = Table(title="Recipes by category")
    table.add_column("Category")
    table.add_column("Recipes")
    for row in categories:
        table.add_row(row.category, str(row.recipe_count))
    console.print(table)
    console.print(
        "Execution success ratio: {:.0%} (success={}, failure={}, pending={})".format(
            execution_summary.success_ratio,
            execution_summary.success,
            execution_summary.failure,
            execution_summary.pending,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    app()
