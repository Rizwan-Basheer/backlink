"""FastAPI powered admin panel for the backlink bot."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config
from ..bot.executor import RecipeExecutor
from ..db import CategoryRequestStatus, ExecutionStatus, RecipeStatus, init_db
from ..services import AdminService
from ..utils.logging import get_logger

logger = get_logger("backlink.admin")

app = FastAPI(title="Backlink Admin Panel")
templates = Jinja2Templates(directory=str(config.BASE_DIR / "backlink_bot" / "admin" / "templates"))
templates.env.globals["now"] = datetime.utcnow
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "backlink_bot" / "admin" / "static")), name="static")


@app.on_event("startup")
async def startup_event() -> None:
    init_db()


def get_admin_service() -> AdminService:
    return AdminService()


def get_executor(service: AdminService = Depends(get_admin_service)) -> RecipeExecutor:
    return RecipeExecutor(admin_service=service)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, service: AdminService = Depends(get_admin_service)) -> HTMLResponse:
    metrics = service.dashboard_metrics()
    categories = service.list_categories(include_inactive=True)
    executions = service.list_executions()[:10]
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "metrics": metrics,
            "categories": categories,
            "executions": executions,
        },
    )


@app.get("/recipes", response_class=HTMLResponse)
async def recipes_page(
    request: Request,
    category: Optional[str] = None,
    status_filter: Optional[RecipeStatus] = None,
    search: Optional[str] = None,
    service: AdminService = Depends(get_admin_service),
) -> HTMLResponse:
    recipes = service.list_recipes(category=category, status=status_filter, search=search)
    categories = service.list_categories(include_inactive=True)
    return templates.TemplateResponse(
        "recipes.html",
        {
            "request": request,
            "recipes": recipes,
            "categories": categories,
            "selected_category": category,
            "status_filter": status_filter,
            "search": search,
        },
    )


@app.get("/recipes/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(
    recipe_id: int,
    request: Request,
    service: AdminService = Depends(get_admin_service),
) -> HTMLResponse:
    recipe = service.recipe_detail(recipe_id)
    executions = service.list_executions(recipe_id=recipe_id)
    return templates.TemplateResponse(
        "recipe_detail.html",
        {"request": request, "recipe": recipe, "executions": executions},
    )


@app.post("/recipes/{recipe_id}/run")
async def run_recipe(
    recipe_id: int,
    request: Request,
    service: AdminService = Depends(get_admin_service),
    executor: RecipeExecutor = Depends(get_executor),
) -> RedirectResponse:
    recipe = service.recipe_detail(recipe_id)
    try:
        await asyncio.get_running_loop().run_in_executor(None, executor.execute_recipe, recipe, None, None)
    except RuntimeError as exc:
        logger.error("Failed to run recipe: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    url = request.url_for("recipe_detail", recipe_id=recipe_id)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@app.post("/recipes/bulk")
async def bulk_action(
    request: Request,
    action: str = Form(...),
    recipe_ids: List[int] = Form(...),
    service: AdminService = Depends(get_admin_service),
    executor: RecipeExecutor = Depends(get_executor),
) -> RedirectResponse:
    for recipe_id in recipe_ids:
        recipe = service.recipe_detail(recipe_id)
        if action == "pause":
            service.toggle_recipe_pause(recipe_id, True)
        elif action == "resume":
            service.toggle_recipe_pause(recipe_id, False)
        elif action == "run":
            await asyncio.get_running_loop().run_in_executor(None, executor.execute_recipe, recipe, None, None)
        elif action == "archive":
            service.update_recipe_status(recipe_id, RecipeStatus.ARCHIVED)
    url = request.url_for("recipes_page")
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@app.post("/recipes/{recipe_id}/schedule")
async def update_schedule(
    recipe_id: int,
    request: Request,
    schedule: Optional[str] = Form(None),
    service: AdminService = Depends(get_admin_service),
) -> RedirectResponse:
    service.update_recipe_schedule(recipe_id, schedule)
    url = request.url_for("recipe_detail", recipe_id=recipe_id)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@app.get("/executions", response_class=HTMLResponse)
async def executions_page(
    request: Request,
    status_filter: Optional[ExecutionStatus] = None,
    service: AdminService = Depends(get_admin_service),
) -> HTMLResponse:
    executions = service.list_executions(status=status_filter)
    return templates.TemplateResponse(
        "executions.html",
        {
            "request": request,
            "executions": executions,
            "status_filter": status_filter,
        },
    )


@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request, service: AdminService = Depends(get_admin_service)) -> HTMLResponse:
    categories = service.list_categories(include_inactive=True)
    requests = service.list_category_requests()
    return templates.TemplateResponse(
        "categories.html",
        {"request": request, "categories": categories, "requests": requests},
    )


@app.post("/categories")
async def create_category(
    request: Request,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    service: AdminService = Depends(get_admin_service),
) -> RedirectResponse:
    service.create_category(name, description)
    url = request.url_for("categories_page")
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@app.post("/category-requests/{request_id}/approve")
async def approve_category_request(
    request_id: int,
    request: Request,
    reviewer: str = Form("admin"),
    service: AdminService = Depends(get_admin_service),
) -> RedirectResponse:
    service.update_category_request(request_id, CategoryRequestStatus.APPROVED, reviewer)
    url = request.url_for("categories_page")
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@app.post("/category-requests/{request_id}/reject")
async def reject_category_request(
    request_id: int,
    request: Request,
    reviewer: str = Form("admin"),
    service: AdminService = Depends(get_admin_service),
) -> RedirectResponse:
    service.update_category_request(request_id, CategoryRequestStatus.REJECTED, reviewer)
    url = request.url_for("categories_page")
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@app.get("/analytics/export")
async def export_state(service: AdminService = Depends(get_admin_service)):
    export_path = config.DATA_DIR / f"export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    path = service.export_state(export_path)
    return {"export_path": str(path)}


@app.post("/analytics/import")
async def import_state(file_path: str = Form(...), service: AdminService = Depends(get_admin_service)) -> Dict[str, str]:
    service.import_state(config.DATA_DIR / file_path)
    return {"status": "ok"}


__all__ = ["app"]
