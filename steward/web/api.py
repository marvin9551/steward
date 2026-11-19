"""The FastAPI application: routes, SSE, static files.

This layer is deliberately thin. Validation is Pydantic, errors are normalized
to `{"error": ...}` (the shape the page already speaks), and everything with
behavior lives in `service.py`.

The page is a single built bundle in `steward/web/static/`: `/assets/*` is
served by StaticFiles, and any other unknown path falls back to `index.html`.
`/docs` comes free with FastAPI and is genuinely useful when poking at the API.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from steward import __version__
from steward.config import Config
from steward.providers.base import Provider
from steward.web.auth import SESSION_COOKIE, TOKEN_TTL_SECONDS
from steward.web.service import Busy, UnknownSession, WebService

STATIC_DIR = Path(__file__).parent / "static"
INDEX = STATIC_DIR / "index.html"
ASSETS = STATIC_DIR / "assets"

POLL_INTERVAL = 0.05
PING_INTERVAL = 12.0

MISSING_BUILD = (
    "前端还没有构建。\n\n"
    "在仓库根目录运行：\n"
    "    make web        # 或 cd web && npm ci && npm run build\n\n"
    "然后刷新这个页面。\n"
)


class TurnRequest(BaseModel):
    session: str | None = None
    text: str


class ApprovalDecision(BaseModel):
    id: str
    decision: str


class PermissionChange(BaseModel):
    mode: str


class ProviderUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    stream_usage: bool | None = None


class ModelSync(BaseModel):
    """Unsaved dialog values, so you can sync before you save."""

    base_url: str | None = None
    api_key: str | None = None


class ProfileSave(BaseModel):
    name: str
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    stream_usage: bool | None = None
    # The settings dialog applies the same values to the active provider, so
    # the profile it files them under is the one in effect.
    mark_active: bool = False


class ProfileApply(BaseModel):
    name: str


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


def get_service(request: Request) -> WebService:
    return request.app.state.service


ServiceDep = Depends(get_service)


def get_user(request: Request) -> str:
    """Session dependency: the username behind a valid session cookie.

    JavaScript never sees the token — it lives in an HttpOnly cookie, which
    also makes the SSE connection (which cannot set headers) work.
    """
    service = request.app.state.service
    token = request.cookies.get(SESSION_COOKIE) or ""
    username = service.verify_session(token) if token else None
    if username is None:
        raise HTTPException(401, "login required")
    return username


UserDep = Depends(get_user)


def create_app(
    config: Config,
    provider: Provider | None = None,
    on_ready: Callable[[], None] | None = None,
) -> FastAPI:
    """Build the app. `provider` is the test seam; `on_ready` opens the browser."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.service = WebService(config, provider=provider)
        if on_ready is not None:
            on_ready()
        try:
            yield
        finally:
            await app.state.service.close()

    app = FastAPI(title="steward", version=__version__, lifespan=lifespan)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request: Request, exc: RequestValidationError) -> JSONResponse:
        first: dict[str, Any] = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
        return JSONResponse(
            {"error": f"invalid {where}: {first.get('msg', 'bad request')}"},
            status_code=400,
        )

    # -- auth -----------------------------------------------------------

    @app.post("/api/login")
    async def login(
        body: LoginRequest, response: Response, service: WebService = ServiceDep
    ) -> dict[str, Any]:
        try:
            token = service.login(body.username, body.password)
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from None
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=TOKEN_TTL_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return {"username": body.username}

    @app.get("/api/me")
    async def me(user: str = UserDep) -> dict[str, Any]:
        return {"username": user}

    @app.post("/api/password")
    async def change_password(
        body: PasswordChange, user: str = UserDep, service: WebService = ServiceDep
    ) -> dict[str, Any]:
        try:
            service.change_password(user, body.current_password, body.new_password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"changed": True}

    # -- reads ----------------------------------------------------------

    @app.get("/api/status")
    async def status(service: WebService = ServiceDep, user: str = UserDep) -> dict[str, Any]:
        return service.status()

    @app.get("/api/sessions")
    async def sessions(service: WebService = ServiceDep, user: str = UserDep) -> dict[str, Any]:
        return {"sessions": service.sessions()}

    @app.get("/api/sessions/{session_id}")
    async def transcript(
        session_id: str, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            return service.transcript(session_id)
        except UnknownSession:
            raise HTTPException(404, f"no such session: {session_id}") from None

    # -- writes ---------------------------------------------------------

    @app.post("/api/sessions")
    async def new_session(service: WebService = ServiceDep, user: str = UserDep) -> dict[str, Any]:
        return {"session_id": service.new_session()}

    @app.post("/api/turn")
    async def turn(
        body: TurnRequest, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            session_id = await service.start_turn(body.session or "", body.text)
        except Busy as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"session_id": session_id}

    @app.post("/api/approval")
    async def approval(
        body: ApprovalDecision, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            resolved = service.resolve_approval(body.id, body.decision)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if not resolved:
            raise HTTPException(404, f"no pending approval: {body.id}")
        return {"resolved": True}

    @app.post("/api/permission")
    async def permission(
        body: PermissionChange, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            return {"permission": service.set_permission(body.mode)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    # -- provider settings ----------------------------------------------

    @app.get("/api/provider")
    async def get_provider(service: WebService = ServiceDep, user: str = UserDep) -> dict[str, Any]:
        return service.provider_settings()

    @app.post("/api/provider")
    async def update_provider(
        body: ProviderUpdate, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            return service.set_provider(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/provider/test")
    async def test_provider(service: WebService = ServiceDep, user: str = UserDep) -> dict[str, Any]:
        return await service.probe_provider()

    @app.post("/api/models")
    async def models(
        body: ModelSync, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        return await service.list_models(body.base_url, body.api_key)

    # -- provider profiles ----------------------------------------------

    @app.get("/api/profiles")
    async def get_profiles(service: WebService = ServiceDep, user: str = UserDep) -> dict[str, Any]:
        return service.profiles()

    @app.post("/api/profiles")
    async def upsert_profile(
        body: ProfileSave, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            values = {key: value for key, value in body.model_dump().items() if key != "mark_active"}
            return service.save_profile(body.name, values, mark_active=body.mark_active)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/profiles/apply")
    async def apply_profile(
        body: ProfileApply, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            return service.apply_profile(body.name)
        except KeyError:
            raise HTTPException(404, f"no such profile: {body.name}") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.delete("/api/profiles/{name}")
    async def remove_profile(
        name: str, service: WebService = ServiceDep, user: str = UserDep
    ) -> dict[str, Any]:
        try:
            return service.delete_profile(name)
        except KeyError:
            raise HTTPException(404, f"no such profile: {name}") from None

    # -- stream ---------------------------------------------------------

    @app.get("/api/stream")
    async def stream(
        request: Request, service: WebService = ServiceDep, user: str = UserDep
    ) -> StreamingResponse:
        cursor = _cursor(request)
        return StreamingResponse(
            stream_events(service, cursor, stop=request.is_disconnected),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # -- the page -------------------------------------------------------

    if ASSETS.is_dir():
        app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        return _index_response()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith("api/"):
            raise HTTPException(404, f"no route for GET /{path}")
        target = _resolve_static(path)
        if target is not None and target.is_file():
            return FileResponse(target)
        return _index_response()

    # Without this, an unmatched POST would hit the GET catch-all above and
    # surface as a confusing "405 Method Not Allowed" — which is exactly what
    # a page newer than its backend process looks like after a `git pull`.
    @app.api_route(
        "/{path:path}",
        methods=["POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        include_in_schema=False,
    )
    async def unknown_method(path: str) -> Response:
        raise HTTPException(404, f"no route for /{path}")

    return app


async def stream_events(
    service: WebService,
    cursor: int = 0,
    stop: Callable[[], Awaitable[bool]] | None = None,
    poll: float = POLL_INTERVAL,
    ping: float = PING_INTERVAL,
) -> AsyncIterator[str]:
    """Tail the event log as SSE.

    A cursor, not a queue: the client sends `Last-Event-ID` on reconnect and we
    answer with everything after it. If it fell off the bounded log we ask it to
    reload from SQLite (`resync`) and continue from now, rather than replaying
    events it may already have.
    """
    last_ping = time.monotonic()
    while True:
        if stop is not None and await stop():
            return

        batch = service.events.since(cursor)
        if batch.lost:
            yield _sse(None, {"type": "resync"})
            cursor = service.events.cursor
            batch = service.events.since(cursor)

        for item in batch.events:
            cursor = int(item["seq"])
            yield _sse(cursor, item)

        if not batch.events:
            now = time.monotonic()
            if now - last_ping > ping:
                yield ": ping\n\n"
                last_ping = now
            await asyncio.sleep(poll)


def _sse(event_id: int | None, payload: dict[str, Any]) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _cursor(request: Request) -> int:
    header = request.headers.get("last-event-id")
    raw = header if header else request.query_params.get("cursor", "0")
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return 0


def _resolve_static(path: str) -> Path | None:
    """Map a URL path to a file under the build output, refusing traversal."""
    relative = path.lstrip("/")
    if not relative or ".." in Path(relative).parts:
        return None
    root = STATIC_DIR.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _index_response() -> FileResponse | PlainTextResponse:
    if not INDEX.is_file():
        return PlainTextResponse(MISSING_BUILD, status_code=503)
    return FileResponse(INDEX, headers={"Cache-Control": "no-store"})
