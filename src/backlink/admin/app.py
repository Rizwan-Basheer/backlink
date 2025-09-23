"""FastAPI powered administration interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from starlette.templating import Jinja2Templates

from ..database import init_db, session_scope
from ..models import (
    Category,
    CategoryRequestStatus,
    Execution,
    ExecutionStatus,
    Recipe,
    RecipeStatus,
)
from ..services.analytics import AnalyticsService
from ..services.categories import CategoryService
from ..services.executor import RecipeExecutor
from ..services.notifications import NotificationService
from .dependencies import get_session

app = FastAPI(title="Backlink Admin Panel")

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@app.on_event("startup")
def startup() -> None:
    init_db()


def run_recipe_async(recipe_id: int) -> None:
    with session_scope() as session:
        executor = RecipeExecutor(session)
        executor.execute_recipe(recipe_id)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> Any:
    analytics = AnalyticsService(session)
    notification_service = NotificationService(session)

    total_backlinks = analytics.total_backlinks()
    category_stats = analytics.recipes_per_category()
    execution_stats = analytics.execution_summary()
    historical = analytics.historical_stats(days=30)
    notifications = notification_service.list_unread()

    context = {
        "request": request,
        "total_backlinks": total_backlinks,
        "category_stats": category_stats,
        "execution_stats": execution_stats,
        "historical": historical,
        "notifications": notifications,
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/recipes", response_class=HTMLResponse)
def recipes_view(
    request: Request,
    search: str | None = None,
    status: RecipeStatus | None = None,
    category_id: int | None = None,
    session: Session = Depends(get_session),
) -> Any:
    statement = select(Recipe, Category).join(Category, Recipe.category_id == Category.id)
    if search:
        like = f"%{search}%"
        statement = statement.where(Recipe.name.ilike(like) | Recipe.site.ilike(like))
    if status:
        statement = statement.where(Recipe.status == status)
    if category_id:
        statement = statement.where(Recipe.category_id == category_id)
    rows = session.exec(statement).all()

    training = (
        session.exec(select(Recipe).where(Recipe.status == RecipeStatus.TRAINING)).all()
    )
    categories = CategoryService(session).list_categories(include_inactive=True)

    context = {
        "request": request,
        "recipes": rows,
        "training": training,
        "categories": categories,
        "selected_status": status,
        "selected_category": category_id,
        "search": search or "",
    }
    return templates.TemplateResponse("recipes.html", context)


@app.post("/recipes/{recipe_id}/run")
def run_recipe(
    recipe_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    background_tasks.add_task(run_recipe_async, recipe_id)
    return RedirectResponse(url="/recipes", status_code=303)


@app.post("/recipes/rerun-all")
def rerun_all(background_tasks: BackgroundTasks, session: Session = Depends(get_session)) -> RedirectResponse:
    recipe_ids = [recipe.id for recipe in session.exec(select(Recipe.id)).all()]
    for recipe_id in recipe_ids:
        background_tasks.add_task(run_recipe_async, recipe_id)
    return RedirectResponse(url="/recipes", status_code=303)


@app.post("/categories/{category_id}/rerun")
def rerun_category(
    category_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    recipe_ids = [
        recipe.id
        for recipe in session.exec(select(Recipe).where(Recipe.category_id == category_id)).all()
    ]
    for recipe_id in recipe_ids:
        background_tasks.add_task(run_recipe_async, recipe_id)
    return RedirectResponse(url="/recipes", status_code=303)


@app.get("/executions", response_class=HTMLResponse)
def executions_view(
    request: Request,
    status: ExecutionStatus | None = None,
    session: Session = Depends(get_session),
) -> Any:
    statement = select(Execution).order_by(Execution.started_at.desc())
    if status:
        statement = statement.where(Execution.status == status)
    executions = session.exec(statement).all()
    context = {
        "request": request,
        "executions": executions,
        "selected_status": status,
    }
    return templates.TemplateResponse("executions.html", context)


@app.get("/categories", response_class=HTMLResponse)
def categories_view(request: Request, session: Session = Depends(get_session)) -> Any:
    service = CategoryService(session)
    categories = service.list_categories(include_inactive=True)
    requests = service.list_requests(status=None)
    context = {
        "request": request,
        "categories": categories,
        "requests": requests,
    }
    return templates.TemplateResponse("categories.html", context)


@app.post("/categories")
def create_category(
    name: str = Form(...),
    description: str = Form(""),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    service = CategoryService(session)
    service.create_category(name=name, description=description or None)
    return RedirectResponse(url="/categories", status_code=303)


@app.post("/category-requests/{request_id}/approve")
def approve_request(
    request_id: int,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    service = CategoryService(session)
    service.approve_request(request_id)
    return RedirectResponse(url="/categories", status_code=303)


@app.post("/category-requests/{request_id}/reject")
def reject_request(
    request_id: int,
    reason: str = Form(""),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    service = CategoryService(session)
    service.reject_request(request_id, reason=reason or None)
    return RedirectResponse(url="/categories", status_code=303)


@app.get("/category-requests", response_class=HTMLResponse)
def category_requests(request: Request, session: Session = Depends(get_session)) -> Any:
    service = CategoryService(session)
    pending = service.list_requests(status=CategoryRequestStatus.PENDING)
    context = {
        "request": request,
        "pending": pending,
    }
    return templates.TemplateResponse("category_requests.html", context)
