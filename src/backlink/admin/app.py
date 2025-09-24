"""FastAPI-powered administration interface with HTML views and JSON APIs."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, ConfigDict, field_validator
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from starlette.websockets import WebSocketState
# add with other imports
from playwright.async_api import async_playwright
# --- put these imports right at the top of app.py ---
import sys, asyncio

# On Windows, use Proactor loop so asyncio subprocess works (required by Playwright)
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from ..config import LOG_DIR, SCREENSHOT_DIR, SECRET_KEY
from ..database import session_scope
from ..models import (
    Category,
    CategoryRequest,
    CategoryRequestStatus,
    Execution,
    ExecutionStatus,
    GeneratedAsset,
    Recipe,
    RecipeStatus,
    Role,
    TargetURL,
    User,
)
from ..services.admin import AdminService
from ..services.analytics import AnalyticsService
from ..services.auth import AuthService
from ..services.categories import CategoryService
from ..services.executor import RecipeExecutor
from ..services.notifications import NotificationService
from ..services.recipes import RecipeAction, RecipeDefinition, RecipeManager, RecipeMetadata
from ..services.settings import SettingsData, SettingsService
from ..services.training import RecipeTrainer
from ..utils.files import read_yaml
from .dependencies import get_session

app = FastAPI(title="Backlink Creator Bot Admin")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="backlink_session",
    same_site="lax",
)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["now"] = datetime.utcnow


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


templates.env.globals["csrf_token"] = get_csrf_token

trainer = RecipeTrainer()
_trainer_streams: dict[str, asyncio.Queue[str]] = {}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TargetCreate(BaseModel):
    url: HttpUrl
    title: str | None = None
    description: str | None = None
    keywords: str | None = None
    summary: str | None = None


class TargetUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    keywords: str | None = None
    summary: str | None = None


class TargetRead(BaseModel):
    id: int
    url: str
    title: str | None
    description: str | None
    keywords: str | None
    summary: str | None
    html_snapshot_path: str | None
    created_at: datetime
    updated_at: datetime
    has_enrichment: bool = Field(default=False)

    model_config = ConfigDict(from_attributes=True)


class CategoryPayload(BaseModel):
    name: str
    description: str | None = None
    is_active: bool = True


class CategoryRead(BaseModel):
    id: int
    name: str
    description: str | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class CategoryRequestPayload(BaseModel):
    name: str
    reason: str | None = None


class CategoryRequestDecision(BaseModel):
    reason: str | None = None


class CategoryRequestRead(BaseModel):
    id: int
    name: str
    reason: str | None
    status: CategoryRequestStatus
    requested_by: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecipePayload(BaseModel):
    metadata: dict[str, Any]
    actions: Any
    variables: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    content_requirements: dict[str, Any] = Field(default_factory=dict)

    @field_validator("actions", mode="before")
    @classmethod
    def _ensure_actions(cls, value: Any) -> list[dict[str, Any]]:  # noqa: D417
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("actions must be valid JSON") from exc
            value = parsed
        if not isinstance(value, list):
            raise ValueError("actions must be a list of steps")
        return value

    def to_definition(self) -> RecipeDefinition:
        metadata = RecipeMetadata.model_validate(self.metadata)
        actions = [
            action if isinstance(action, RecipeAction) else RecipeAction.model_validate(action)
            for action in self.actions
        ]
        return RecipeDefinition(
            metadata=metadata,
            actions=actions,
            variables=self.variables,
            config=self.config,
            content_requirements=self.content_requirements,
        )


class RecipeRead(BaseModel):
    id: int
    name: str
    site: str
    category: str
    status: str
    version: int
    owner: str | None


class ExecutionRunRequest(BaseModel):
    recipe_id: int
    target_id: int
    headless: bool | None = None
    refresh_content: bool = False
    runtime_variables: dict[str, Any] | None = None


class ExecutionBatchRequest(BaseModel):
    recipe_ids: list[int]
    target_ids: list[int]
    headless: bool | None = None
    refresh_content: bool = False


class ExecutionRead(BaseModel):
    id: int
    recipe_id: int
    target_id: int | None
    status: ExecutionStatus
    log_path: str | None
    screenshot_path: str | None
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)


class AIGenerateRequest(BaseModel):
    target_id: int
    recipe_id: int | None = None
    category_name: str | None = None
    kinds: Sequence[str] = Field(default_factory=list)

    @field_validator("kinds")
    @classmethod
    def validate_kinds(cls, value: Sequence[str]) -> Sequence[str]:  # noqa: D417
        allowed = {"profile_bio", "caption", "blog_post", "summary", "keywords"}
        invalid = [item for item in value if item not in allowed]
        if invalid:
            raise ValueError(f"Unsupported kinds: {', '.join(invalid)}")
        return value


class TrainerStartRequest(BaseModel):
    name: str
    site: str
    description: str | None = None
    category_id: int


class TrainerActionIn(BaseModel):
    name: str
    action: str
    selector: str | None = None
    value: str | None = None
    wait_for: float | None = None
    screenshot: bool = False


class TrainerStopRequest(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)
    content_requirements: dict[str, Any] = Field(default_factory=dict)
    change_summary: str = "Recorded via trainer"


class SettingsUpdate(BaseModel):
    openai_api_key: str | None = None
    headless_default: bool | None = None
    playwright_timeout_ms: int | None = None
    recipes_path: str | None = None
    versions_path: str | None = None
    log_path: str | None = None
    screenshots_path: str | None = None
    snapshots_path: str | None = None
    rate_limit_per_minute: int | None = None


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------


def optional_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = session.get(User, user_id)
    if not user or not user.is_active:
        request.session.clear()
        return None
    return user


def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    user = optional_current_user(request, session)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_operator(user: User = Depends(get_current_user)) -> User:
    if user.role not in {Role.ADMIN, Role.OPERATOR}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator access required")
    return user


# ---------------------------------------------------------------------------
# CSRF utilities
# ---------------------------------------------------------------------------


def _verify_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def csrf_header_dependency(request: Request) -> None:
    if request.method in {"POST", "PUT", "DELETE"}:
        header_token = request.headers.get("X-CSRF-Token")
        _verify_csrf(request, header_token)


def render_template(
    request: Request,
    template_name: str,
    *,
    user: User,
    context: dict[str, Any] | None = None,
) -> HTMLResponse:
    data = context.copy() if context else {}
    data.update({"request": request, "user": user, "csrf_token": get_csrf_token(request)})
    return templates.TemplateResponse(request, template_name, data)


def _category_request_to_read(request: CategoryRequest) -> CategoryRequestRead:
    return CategoryRequestRead(
        id=request.id,
        name=request.name,
        reason=request.reason,
        status=request.status,
        requested_by=request.requester.email if request.requester else None,
        created_at=request.created_at,
    )


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


def _run_enrichment(target_id: int) -> None:
    with session_scope() as session:
        admin = AdminService(session)
        notifications = NotificationService(session)
        target = admin.fetch_and_enrich_target(target_id)
        notifications.create(f"Enrichment completed for {target.url}", type="success")


def _run_ai_generation(
    target_id: int,
    recipe_id: int | None,
    category_name: str | None,
    kinds: Sequence[str],
) -> None:
    with session_scope() as session:
        admin = AdminService(session)
        recipe = session.get(Recipe, recipe_id) if recipe_id else None
        admin.generate_content_for_target(
            target_id,
            recipe=recipe,
            category_name=category_name,
            kinds=kinds,
            refresh=True,
        )
        NotificationService(session).create(
            f"AI generation ready for target #{target_id}", type="success"
        )


def _run_execution(
    execution_id: int,
    recipe_id: int,
    target_id: int,
    headless: bool | None,
    runtime_variables: dict[str, Any] | None,
    refresh_content: bool,
) -> None:
    with session_scope() as session:
        executor = RecipeExecutor(session)
        execution = session.get(Execution, execution_id)
        recipe = session.get(Recipe, recipe_id)
        if not execution or not recipe:
            return
        executor.execute_recipe(
            recipe,
            target=target_id,
            headless=headless,
            runtime_variables=runtime_variables,
            refresh_content=refresh_content,
            execution=execution,
        )
        NotificationService(session).create(
            f"Execution {execution_id} finished with status {execution.status.value}",
            type="success" if execution.status == ExecutionStatus.SUCCESS else "warning",
        )


async def _tail_execution_logs(websocket: WebSocket, execution_id: int) -> None:
    await websocket.accept()
    last_position = 0
    try:
        while True:
            with session_scope() as session:
                execution = session.get(Execution, execution_id)
                if not execution:
                    await websocket.send_text("Execution not found")
                    break
                log_path = Path(execution.log_path) if execution.log_path else None
                status_value = execution.status
            if log_path and log_path.exists():
                with log_path.open("r", encoding="utf-8") as handle:
                    handle.seek(last_position)
                    chunk = handle.read()
                    last_position = handle.tell()
                if chunk:
                    await websocket.send_text(chunk)
            if status_value in {ExecutionStatus.SUCCESS, ExecutionStatus.FAILURE}:
                break
            await asyncio.sleep(1)
    finally:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close()


def _trainer_queue(session_id: str) -> asyncio.Queue[str]:
    if session_id not in _trainer_streams:
        _trainer_streams[session_id] = asyncio.Queue()
    return _trainer_streams[session_id]

# ---- Trainer (Playwright) ----
async def _launch_trainer_browser(session_id: str, site: str) -> None:
    """
    Launch a visible Chromium window, navigate to `site`, and record actions.
    """
    queue = _trainer_queue(session_id)

    def _record(payload: dict[str, Any]) -> None:
        # Save to in-memory trainer + push to websocket feed
        try:
            trainer.record_action(session_id, **payload)
            queue.put_nowait(json.dumps({"type": "action", "payload": payload}))
        except KeyError:
            # session was closed/discarded
            pass

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)  # <-- VISIBLE
            context = await browser.new_context()
            page = await context.new_page()

            # Record navigations
            page.on(
                "framenavigated",
                lambda frame: _record({
                    "name": f"navigate {frame.url}",
                    "action": "navigate",
                    "selector": None,
                    "value": None,
                    "wait_for": None,
                    "screenshot": False,
                    # optional: you can store url in 'value' or extend TrainerActionIn schema to include 'url'
                })
            )

            # Expose a Python function so content script can call back without CORS
            await page.expose_function("trainerRecord", lambda data: _record(dict(data)))

            # Inject a small content script to capture clicks & inputs
            await page.add_init_script(f"""
                (() => {{
                  const toSelector = (el) => {{
                    if (!el) return '';
                    if (el.id) return '#' + el.id;
                    let s = el.tagName ? el.tagName.toLowerCase() : '';
                    if (el.name) s += `[name="${{el.name}}"]`;
                    if (el.type) s += `[type="${{el.type}}"]`;
                    if (!el.id && el.classList && el.classList.length) {{
                      s += '.' + Array.from(el.classList).join('.');
                    }}
                    return s || 'unknown';
                  }};
                  document.addEventListener('click', (e) => {{
                    const sel = toSelector(e.target);
                    window.trainerRecord({{
                      name: 'click ' + sel,
                      action: 'click',
                      selector: sel,
                      value: null,
                      wait_for: null,
                      screenshot: false
                    }});
                  }}, true);
                  document.addEventListener('input', (e) => {{
                    const sel = toSelector(e.target);
                    const val = (e.target && 'value' in e.target) ? e.target.value : null;
                    window.trainerRecord({{
                      name: 'input ' + sel,
                      action: 'input',
                      selector: sel,
                      value: val,
                      wait_for: null,
                      screenshot: false
                    }});
                  }}, true);
                }})();
            """)

            # Go to the requested site; this will emit a 'navigate' action via framenavigated
            await page.goto(site if site.startswith("http") else f"https://{site}")

            # Keep the task alive until browser closes
            await browser.wait_for_event("disconnected")
    except Exception as exc:
        # Push an error message into the trainer feed, then close the session
        queue.put_nowait(json.dumps({"type": "error", "message": str(exc)}))
        try:
            trainer.cancel_session(session_id)
            queue.put_nowait(json.dumps({"type": "discarded"}))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: Optional[User] = Depends(optional_current_user)) -> HTMLResponse:
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    context = {"request": request, "csrf_token": get_csrf_token(request)}
    return templates.TemplateResponse(request, "login.html", context)


@app.post("/login")
def login_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    form = request.scope.get("form") if hasattr(request, "scope") else None
    token = None
    if isinstance(form, dict):
        token = form.get("csrf_token")
    else:  # fallback
        token = request.session.get("csrf_token")
    _verify_csrf(request, token)
    auth = AuthService(session)
    user = auth.authenticate(email, password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    request.session["user_id"] = user.id
    request.session["role"] = user.role.value
    get_csrf_token(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    analytics = AnalyticsService(session)
    notification_service = NotificationService(session)
    executions = session.exec(
        select(Execution).order_by(Execution.started_at.desc()).limit(10)
    ).all()
    notifications = notification_service.list_recent(limit=5)
    categories = analytics.recipes_per_category()
    summary = analytics.execution_summary()
    history = analytics.historical_stats(days=30)
    context = {
        "stats": {
            "total_backlinks": analytics.total_backlinks(),
            "success": summary.success,
            "failure": summary.failure,
            "pending": summary.pending,
        },
        "category_data": categories,
        "history": history,
        "notifications": notifications,
        "executions": executions,
    }
    return render_template(request, "dashboard.html", user=user, context=context)


@app.get("/targets", response_class=HTMLResponse)
def targets_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    admin_service = AdminService(session)
    targets = admin_service.list_targets()
    categories = CategoryService(session).list_categories(include_inactive=False)
    context = {"targets": targets, "categories": categories}
    return render_template(request, "targets.html", user=user, context=context)


@app.get("/categories", response_class=HTMLResponse)
def categories_page(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    service = CategoryService(session)
    categories = service.list_categories(include_inactive=True)
    requests = [
        _category_request_to_read(item)
        for item in service.list_requests(status=CategoryRequestStatus.PENDING)
    ]
    context = {"categories": categories, "requests": requests}
    return render_template(request, "categories.html", user=user, context=context)


@app.get("/recipes", response_class=HTMLResponse)
def recipes_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    manager = RecipeManager(session)
    recipes = manager.list_recipes()
    categories = CategoryService(session).list_categories(include_inactive=True)
    context = {"recipes": recipes, "categories": categories, "statuses": list(RecipeStatus)}
    return render_template(request, "recipes.html", user=user, context=context)


@app.get("/executions", response_class=HTMLResponse)
def executions_page(
    request: Request,
    user: User = Depends(get_current_user),
    status_filter: ExecutionStatus | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    statement = select(Execution).order_by(Execution.started_at.desc())
    if status_filter:
        statement = statement.where(Execution.status == status_filter)
    executions = session.exec(statement.limit(100)).all()
    context = {"executions": executions, "status_filter": status_filter, "statuses": list(ExecutionStatus)}
    return render_template(request, "executions.html", user=user, context=context)


@app.get("/trainer", response_class=HTMLResponse)
def trainer_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    categories = CategoryService(session).list_categories(include_inactive=False)
    active_sessions = trainer.list_sessions()
    context = {"categories": categories, "sessions": active_sessions}
    return render_template(request, "trainer.html", user=user, context=context)


@app.get("/ai", response_class=HTMLResponse)
def ai_content_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    assets = session.exec(
        select(GeneratedAsset).order_by(GeneratedAsset.created_at.desc()).limit(50)
    ).all()
    targets = AdminService(session).list_targets()
    recipes = RecipeManager(session).list_recipes()
    context = {"assets": assets, "targets": targets, "recipes": recipes}
    return render_template(request, "ai_content.html", user=user, context=context)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: User = Depends(require_admin),
) -> HTMLResponse:
    settings_service = SettingsService()
    settings = settings_service.load()
    context = {"settings": settings}
    return render_template(request, "settings.html", user=user, context=context)


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------


@app.get("/partials/targets-table", response_class=HTMLResponse)
def partial_targets_table(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    targets = AdminService(session).list_targets()
    return render_template(request, "partials/targets_table.html", user=user, context={"targets": targets})


@app.get("/partials/recipes-table", response_class=HTMLResponse)
def partial_recipes_table(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    recipes = RecipeManager(session).list_recipes()
    return render_template(request, "partials/recipes_table.html", user=user, context={"recipes": recipes})


@app.get("/partials/ai-assets", response_class=HTMLResponse)
def partial_ai_assets(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    assets = session.exec(
        select(GeneratedAsset).order_by(GeneratedAsset.created_at.desc()).limit(50)
    ).all()
    return render_template(request, "partials/ai_assets_table.html", user=user, context={"assets": assets})


@app.get("/partials/categories-table", response_class=HTMLResponse)
def partial_categories_table(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    categories = CategoryService(session).list_categories(include_inactive=True)
    return render_template(
        request,
        "partials/categories_table.html",
        user=user,
        context={"categories": categories},
    )


@app.get("/partials/category-requests", response_class=HTMLResponse)
def partial_category_requests(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    requests = [_category_request_to_read(item) for item in CategoryService(session).list_requests()]
    return render_template(
        request,
        "partials/category_requests.html",
        user=user,
        context={"requests": requests},
    )


@app.get("/partials/trainer-sessions", response_class=HTMLResponse)
def partial_trainer_sessions(
    request: Request,
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    sessions = trainer.list_sessions()
    return render_template(
        request,
        "partials/trainer_sessions.html",
        user=user,
        context={"sessions": sessions},
    )


# ---------------------------------------------------------------------------
# API router
# ---------------------------------------------------------------------------


api = APIRouter(prefix="/api")


@api.get("/targets", response_model=list[TargetRead])
def api_targets(
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> list[TargetRead]:
    admin_service = AdminService(session)
    targets = admin_service.list_targets()
    result: list[TargetRead] = []
    for target in targets:
        payload = TargetRead.model_validate(target)
        payload.has_enrichment = bool(target.summary and target.keywords)
        result.append(payload)
    return result


@api.post("/targets", response_model=TargetRead, dependencies=[Depends(csrf_header_dependency)])
def api_create_target(
    payload: TargetCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _: User = Depends(require_operator),
) -> TargetRead:
    admin_service = AdminService(session)
    target = admin_service.register_target_url(
        payload.url,
        title=payload.title,
        description=payload.description,
        keywords=payload.keywords,
        summary=payload.summary,
    )
    NotificationService(session).create(f"Target {target.url} queued for enrichment")
    background_tasks.add_task(_run_enrichment, target.id)
    response = TargetRead.model_validate(target)
    response.has_enrichment = bool(target.summary and target.keywords)
    return response


@api.get("/targets/{target_id}", response_model=TargetRead)
def api_get_target(
    target_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> TargetRead:
    target = session.get(TargetURL, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    payload = TargetRead.model_validate(target)
    payload.has_enrichment = bool(target.summary and target.keywords)
    return payload


@api.put("/targets/{target_id}", response_model=TargetRead, dependencies=[Depends(csrf_header_dependency)])
def api_update_target(
    target_id: int,
    payload: TargetUpdate,
    session: Session = Depends(get_session),
    _: User = Depends(require_operator),
) -> TargetRead:
    target = session.get(TargetURL, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(target, field, value)
    target.updated_at = datetime.utcnow()
    session.add(target)
    session.flush()
    data = TargetRead.model_validate(target)
    data.has_enrichment = bool(target.summary and target.keywords)
    return data


@api.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_header_dependency)])
def api_delete_target(
    target_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin),
) -> None:
    target = session.get(TargetURL, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    session.delete(target)


@api.post("/targets/{target_id}/enrich", response_model=TargetRead, dependencies=[Depends(csrf_header_dependency)])
def api_enrich_target(
    target_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _: User = Depends(require_operator),
) -> TargetRead:
    target = session.get(TargetURL, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    background_tasks.add_task(_run_enrichment, target.id)
    NotificationService(session).create(f"Enrichment started for {target.url}")
    payload = TargetRead.model_validate(target)
    payload.has_enrichment = bool(target.summary and target.keywords)
    return payload


@api.get("/categories", response_model=list[CategoryRead])
def api_categories(
    include_inactive: bool = False,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> list[CategoryRead]:
    service = CategoryService(session)
    categories = service.list_categories(include_inactive=include_inactive)
    return [CategoryRead.model_validate(cat) for cat in categories]


@api.post("/categories", response_model=CategoryRead, dependencies=[Depends(csrf_header_dependency)])
def api_create_category(
    payload: CategoryPayload,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin),
) -> CategoryRead:
    service = CategoryService(session)
    category = service.create_category(name=payload.name, description=payload.description)
    category.is_active = payload.is_active
    session.add(category)
    session.flush()
    return CategoryRead.model_validate(category)


@api.put("/categories/{category_id}", response_model=CategoryRead, dependencies=[Depends(csrf_header_dependency)])
def api_update_category(
    category_id: int,
    payload: CategoryPayload,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin),
) -> CategoryRead:
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    category.name = payload.name
    category.description = payload.description
    category.is_active = payload.is_active
    session.add(category)
    session.flush()
    return CategoryRead.model_validate(category)


@api.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_header_dependency)])
def api_delete_category(
    category_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin),
) -> None:
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    session.delete(category)


@api.get("/category-requests", response_model=list[CategoryRequestRead])
def api_list_category_requests(
    status_filter: CategoryRequestStatus | None = None,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin),
) -> list[CategoryRequestRead]:
    requests = CategoryService(session).list_requests(status=status_filter)
    return [_category_request_to_read(item) for item in requests]


@api.post(
    "/category-requests",
    response_model=CategoryRequestRead,
    dependencies=[Depends(csrf_header_dependency)],
)
def api_create_category_request(
    payload: CategoryRequestPayload,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CategoryRequestRead:
    service = CategoryService(session)
    request = service.create_request(requested_by=user.id, name=payload.name, reason=payload.reason)
    NotificationService(session).create(
        f"Category request '{payload.name}' submitted by {user.email}", type="info"
    )
    return _category_request_to_read(request)


@api.post(
    "/category-requests/{request_id}/approve",
    response_model=CategoryRead,
    dependencies=[Depends(csrf_header_dependency)],
)
def api_approve_category_request(
    request_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin),
) -> CategoryRead:
    service = CategoryService(session)
    category = service.approve_request(request_id)
    NotificationService(session).create(
        f"Category '{category.name}' created from request {request_id}", type="success"
    )
    return CategoryRead.model_validate(category)


@api.post(
    "/category-requests/{request_id}/reject",
    response_model=CategoryRequestRead,
    dependencies=[Depends(csrf_header_dependency)],
)
def api_reject_category_request(
    request_id: int,
    payload: CategoryRequestDecision,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin),
) -> CategoryRequestRead:
    service = CategoryService(session)
    request = service.reject_request(request_id, reason=payload.reason)
    NotificationService(session).create(
        f"Category request '{request.name}' rejected", type="warning"
    )
    return _category_request_to_read(request)


@api.get("/recipes", response_model=list[RecipeRead])
def api_recipes(
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> list[RecipeRead]:
    manager = RecipeManager(session)
    recipes = manager.list_recipes()
    return [
        RecipeRead(
            id=recipe.id,
            name=recipe.name,
            site=recipe.site,
            category=recipe.category,
            status=recipe.status.value,
            version=recipe.version,
            owner=recipe.owner,
        )
        for recipe in recipes
    ]


@api.post("/recipes", response_model=RecipeRead, dependencies=[Depends(csrf_header_dependency)])
def api_create_recipe(
    payload: RecipePayload,
    session: Session = Depends(get_session),
    user: User = Depends(require_admin),
) -> RecipeRead:
    definition = payload.to_definition()
    manager = RecipeManager(session)
    recipe = manager.create_recipe(definition, owner_id=user.id)
    NotificationService(session).create(f"Recipe '{recipe.name}' created", type="success")
    category_name = recipe.category.name if recipe.category else ""
    return RecipeRead(
        id=recipe.id,
        name=recipe.name,
        site=recipe.site,
        category=category_name,
        status=recipe.status.value,
        version=1,
        owner=user.name,
    )


@api.put("/recipes/{recipe_id}", response_model=RecipeRead, dependencies=[Depends(csrf_header_dependency)])
def api_update_recipe(
    recipe_id: int,
    payload: RecipePayload,
    change_summary: str = Form("Updated via API"),
    session: Session = Depends(get_session),
    user: User = Depends(require_admin),
) -> RecipeRead:
    definition = payload.to_definition()
    manager = RecipeManager(session)
    recipe = manager.update_recipe(recipe_id, definition, change_summary=change_summary)
    category_name = recipe.category.name if recipe.category else ""
    owner_name = recipe.owner.name if recipe.owner else None
    version = recipe.versions[-1].version if recipe.versions else 1
    return RecipeRead(
        id=recipe.id,
        name=recipe.name,
        site=recipe.site,
        category=category_name,
        status=recipe.status.value,
        version=version,
        owner=owner_name,
    )


@api.post(
    "/recipes/{recipe_id}/upload-yaml",
    response_model=RecipeRead,
    dependencies=[Depends(csrf_header_dependency)],
)
def api_upload_recipe_yaml(
    recipe_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: User = Depends(require_admin),
) -> RecipeRead:
    content = file.file.read()
    if len(content) > 200_000:
        raise HTTPException(status_code=400, detail="YAML file too large")
    try:
        data = read_yaml(content.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - malformed yaml
        raise HTTPException(status_code=400, detail="Invalid YAML payload") from exc
    payload = RecipePayload(
        metadata=data.get("metadata", {}),
        actions=data.get("actions", []),
        variables=data.get("variables", {}),
        config=data.get("config", {}),
        content_requirements=data.get("content_requirements", {}),
    )
    definition = payload.to_definition()
    manager = RecipeManager(session)
    recipe = manager.update_recipe(recipe_id, definition, change_summary="YAML upload")
    category_name = recipe.category.name if recipe.category else ""
    owner_name = recipe.owner.name if recipe.owner else None
    version = recipe.versions[-1].version if recipe.versions else 1
    return RecipeRead(
        id=recipe.id,
        name=recipe.name,
        site=recipe.site,
        category=category_name,
        status=recipe.status.value,
        version=version,
        owner=owner_name,
    )


@api.get("/recipes/{recipe_id}/versions")
def api_recipe_versions(
    recipe_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    versions = []
    for version in recipe.versions:
        versions.append(
            {
                "id": version.id,
                "version": version.version,
                "yaml_path": version.yaml_path,
                "change_summary": version.change_summary,
                "created_at": version.created_at,
            }
        )
    return versions


@api.post("/recipes/{recipe_id}/run", response_model=ExecutionRead, dependencies=[Depends(csrf_header_dependency)])
def api_run_recipe(
    recipe_id: int,
    background_tasks: BackgroundTasks,
    target_id: int = Form(...),
    headless: bool | None = Form(None),
    refresh_content: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(require_operator),
) -> ExecutionRead:
    recipe = session.get(Recipe, recipe_id)
    target = session.get(TargetURL, target_id)
    if not recipe or not target:
        raise HTTPException(status_code=404, detail="Recipe or target not found")
    execution = Execution(recipe_id=recipe.id, target_id=target.id, status=ExecutionStatus.PENDING)
    session.add(execution)
    session.flush()
    background_tasks.add_task(
        _run_execution,
        execution.id,
        recipe.id,
        target.id,
        headless,
        None,
        refresh_content,
    )
    return ExecutionRead.model_validate(execution)


@api.post("/executions/run", response_model=ExecutionRead, dependencies=[Depends(csrf_header_dependency)])
def api_execute_recipe(
    payload: ExecutionRunRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _: User = Depends(require_operator),
) -> ExecutionRead:
    recipe = session.get(Recipe, payload.recipe_id)
    target = session.get(TargetURL, payload.target_id)
    if not recipe or not target:
        raise HTTPException(status_code=404, detail="Recipe or target not found")
    execution = Execution(recipe_id=recipe.id, target_id=target.id, status=ExecutionStatus.PENDING)
    session.add(execution)
    session.flush()
    background_tasks.add_task(
        _run_execution,
        execution.id,
        recipe.id,
        target.id,
        payload.headless,
        payload.runtime_variables,
        payload.refresh_content,
    )
    return ExecutionRead.model_validate(execution)


@api.post("/executions/run-batch", response_model=list[ExecutionRead], dependencies=[Depends(csrf_header_dependency)])
def api_execute_batch(
    payload: ExecutionBatchRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _: User = Depends(require_operator),
) -> list[ExecutionRead]:
    executions: list[ExecutionRead] = []
    for target_id in payload.target_ids:
        target = session.get(TargetURL, target_id)
        if not target:
            continue
        for recipe_id in payload.recipe_ids:
            recipe = session.get(Recipe, recipe_id)
            if not recipe:
                continue
            execution = Execution(
                recipe_id=recipe.id,
                target_id=target.id,
                status=ExecutionStatus.PENDING,
            )
            session.add(execution)
            session.flush()
            background_tasks.add_task(
                _run_execution,
                execution.id,
                recipe.id,
                target.id,
                payload.headless,
                None,
                payload.refresh_content,
            )
            executions.append(ExecutionRead.model_validate(execution))
    return executions


@api.get("/executions", response_model=list[ExecutionRead])
def api_list_executions(
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
    limit: int = 100,
) -> list[ExecutionRead]:
    statement = select(Execution).order_by(Execution.started_at.desc()).limit(limit)
    executions = session.exec(statement).all()
    return [ExecutionRead.model_validate(item) for item in executions]


@api.get("/executions/{execution_id}", response_model=ExecutionRead)
def api_get_execution(
    execution_id: int,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> ExecutionRead:
    execution = session.get(Execution, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionRead.model_validate(execution)


@api.post("/ai/generate", dependencies=[Depends(csrf_header_dependency)])
def api_generate_ai(
    payload: AIGenerateRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _: User = Depends(require_operator),
) -> dict[str, Any]:
    target = session.get(TargetURL, payload.target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    recipe = session.get(Recipe, payload.recipe_id) if payload.recipe_id else None
    admin_service = AdminService(session)
    result = admin_service.generate_content_for_target(
        target,
        recipe=recipe,
        category_name=payload.category_name,
        kinds=payload.kinds,
        refresh=False,
    )
    if payload.kinds:
        background_tasks.add_task(
            _run_ai_generation,
            target.id,
            recipe.id if recipe else None,
            payload.category_name,
            payload.kinds,
        )
    return result


@api.get("/notifications")
def api_notifications(
    limit: int = 20,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    service = NotificationService(session)
    notifications = service.list_recent(limit=limit)
    return [
        {
            "id": notification.id,
            "message": notification.message,
            "type": notification.type,
            "created_at": notification.created_at,
        }
        for notification in notifications
    ]


@api.get("/settings", response_model=SettingsData)
def api_settings(
    _: User = Depends(require_admin),
) -> SettingsData:
    return SettingsService().load()


@api.put("/settings", response_model=SettingsData, dependencies=[Depends(csrf_header_dependency)])
def api_update_settings(
    updates: SettingsUpdate,
    _: User = Depends(require_admin),
) -> SettingsData:
    service = SettingsService()
    settings = service.update(updates.model_dump(exclude_unset=True))
    return settings


@api.post("/trainer/start")
async def api_trainer_start(
    payload: TrainerStartRequest,
    _: User = Depends(require_operator),
) -> dict[str, str]:
    session_id = uuid.uuid4().hex
    trainer.start_session(
        session_id,
        name=payload.name,
        site=payload.site,
        description=payload.description,
        category_id=payload.category_id,
    )
    _trainer_queue(session_id)  # ensure queue exists

    # Launch visible Chromium in the background
    asyncio.create_task(_launch_trainer_browser(session_id, payload.site))

    return {"session_id": session_id}



@api.post("/trainer/{session_id}/actions")
def api_trainer_action(
    session_id: str,
    payload: TrainerActionIn,
    _: User = Depends(require_operator),
) -> dict[str, Any]:
    try:
        trainer.record_action(session_id, **payload.dict())
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    queue = _trainer_queue(session_id)
    queue.put_nowait(json.dumps({"type": "action", "payload": payload.dict()}))
    return {"status": "recorded"}


@api.post("/trainer/{session_id}/stop")
def api_trainer_stop(
    session_id: str,
    payload: TrainerStopRequest,
    session: Session = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict[str, Any]:
    try:
        definition = trainer.finish_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    if payload.variables:
        definition.variables.update(payload.variables)
    if payload.content_requirements:
        definition.content_requirements.update(payload.content_requirements)
    manager = RecipeManager(session)
    recipe = manager.create_recipe(definition, owner_id=user.id)
    queue = _trainer_queue(session_id)
    queue.put_nowait(json.dumps({"type": "closed"}))
    return {"recipe_id": recipe.id}


@api.post("/trainer/{session_id}/discard")
def api_trainer_discard(
    session_id: str,
    _: User = Depends(require_operator),
) -> dict[str, str]:
    trainer.cancel_session(session_id)
    queue = _trainer_queue(session_id)
    queue.put_nowait(json.dumps({"type": "discarded"}))
    return {"status": "discarded"}


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------


@app.websocket("/api/executions/{execution_id}/logs")
async def websocket_execution_logs(websocket: WebSocket, execution_id: int) -> None:
    await _tail_execution_logs(websocket, execution_id)


@app.websocket("/api/trainer/{session_id}/events")
async def websocket_trainer_events(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    queue = _trainer_queue(session_id)
    try:
        while True:
            message = await queue.get()
            await websocket.send_text(message)
    except WebSocketDisconnect:
        return


# ---------------------------------------------------------------------------
# Include router and static helpers
# ---------------------------------------------------------------------------


app.include_router(api)
