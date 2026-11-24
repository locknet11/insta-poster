from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from insta_poster.auth import LoginRateLimiter, verify_media_signature
from insta_poster.config import (
    AppPaths,
    get_api_key,
    get_cookie_secure_override,
    get_cors_origins,
    get_session_ttl_days,
    is_auth_disabled,
)
from insta_poster.image_prep import prepare_for_instagram
from insta_poster.models import (
    ActivityLogEntry,
    AdapterConfigUpdate,
    AdapterStatus,
    AuthLoginResponse,
    AuthStatusResponse,
    ChangePasswordRequest,
    DashboardStats,
    FolderSyncRequest,
    FolderSyncResponse,
    LoginRequest,
    PoolImage,
    PoolImageUpdate,
    Publication,
    PublicationCreate,
    PublicationUpdate,
    UserCreateRequest,
    UserPasswordUpdateRequest,
    UserPublic,
)
from insta_poster.scheduler import BackgroundScheduler
from insta_poster.service import InstaPosterService

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE_NAME = "insta_poster_session"


@dataclass(frozen=True)
class AuthContext:
    username: str
    auth_method: Literal["cookie", "bearer", "api_key", "disabled"]
    user: UserPublic | None = None
    session_token: str | None = None


def _extract_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        tok = auth_header[7:].strip()
        return tok or None
    return None


def _is_secure_request(request: Request) -> bool:
    override = get_cookie_secure_override()
    if override is not None:
        return override
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    max_age = get_session_ttl_days() * 86400
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_is_secure_request(request),
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_is_secure_request(request),
    )


def _safe_media_file(base_dir: Path, filename: str) -> Path:
    if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    resolved_base = base_dir.resolve()
    target = (resolved_base / filename).resolve()
    if target.parent != resolved_base or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return target


def create_app(
    data_dir: Path | None = None,
    *,
    enable_scheduler: bool = True,
    require_auth: bool | None = None,
) -> FastAPI:
    paths = AppPaths.from_data_dir(data_dir)
    service = InstaPosterService(paths)
    scheduler = BackgroundScheduler(service)
    rate_limiter = LoginRateLimiter()
    auth_enabled = (not is_auth_disabled()) if require_auth is None else require_auth

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if enable_scheduler:
            await scheduler.start()
        try:
            yield
        finally:
            if enable_scheduler:
                await scheduler.stop()

    app = FastAPI(
        title="Insta-Poster Local API",
        version="0.1.0",
        description=(
            "Local-first REST API & Webapp for agent-driven Instagram publishing "
            "(feed posts, carousels & stories) from a managed image pool."
        ),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.service = service
    app.state.paths = paths

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _resolve_auth(request: Request) -> AuthContext | None:
        if not auth_enabled:
            header_actor = (request.headers.get("x-actor") or "local").strip() or "local"
            return AuthContext(username=header_actor, auth_method="disabled")

        expected_key = get_api_key()
        provided_key = request.headers.get("x-api-key")
        if expected_key and provided_key and hmac.compare_digest(provided_key, expected_key):
            header_actor = (request.headers.get("x-actor") or "api-key").strip() or "api-key"
            return AuthContext(username=header_actor, auth_method="api_key")

        bearer = _extract_bearer_token(request)
        if bearer:
            user = service.verify_session_token(bearer)
            if user:
                return AuthContext(
                    username=user.username,
                    auth_method="bearer",
                    user=user,
                    session_token=bearer,
                )

        cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
        if cookie_token:
            user = service.verify_session_token(cookie_token)
            if user:
                return AuthContext(
                    username=user.username,
                    auth_method="cookie",
                    user=user,
                    session_token=cookie_token,
                )

        return None

    def _require_auth(request: Request) -> AuthContext:
        ctx = _resolve_auth(request)
        if ctx is None:
            raise HTTPException(status_code=401, detail="Autenticación requerida")
        return ctx

    def _parse_actor(
        x_actor: str | None = Header(default=None),
        auth: AuthContext = Depends(_require_auth),
    ) -> str:
        raw = (x_actor or "").strip()
        if raw and raw not in ("web-ui", "api"):
            return raw
        if auth.user is not None:
            return auth.user.username
        return raw or "api"

    # ------------------------------------------------------------------
    # Protected OpenAPI / Swagger / ReDoc
    # ------------------------------------------------------------------
    @app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(_require_auth)])
    def protected_openapi() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=[Depends(_require_auth)])
    def protected_swagger_ui():
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")

    @app.get("/redoc", include_in_schema=False, dependencies=[Depends(_require_auth)])
    def protected_redoc():
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")

    # ------------------------------------------------------------------
    # Protected media serving
    # ------------------------------------------------------------------
    @app.api_route(
        "/media/pool/{filename}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
        dependencies=[Depends(_require_auth)],
    )
    def serve_pool_media(filename: str) -> FileResponse:
        return FileResponse(str(_safe_media_file(paths.pool_dir, filename)))

    @app.api_route(
        "/media/thumbnails/{filename}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
        dependencies=[Depends(_require_auth)],
    )
    def serve_thumbnail_media(filename: str) -> FileResponse:
        return FileResponse(str(_safe_media_file(paths.thumbnails_dir, filename)))

    @app.api_route("/media/prepared/{filename}", methods=["GET", "HEAD"], include_in_schema=False)
    def serve_prepared_media(
        filename: str,
        request: Request,
        exp: int | None = Query(default=None),
        sig: str | None = Query(default=None),
    ) -> FileResponse:
        ctx = _resolve_auth(request)
        if ctx is None:
            if exp is None or not sig or not verify_media_signature(
                filename, exp, sig, service.get_media_signing_secret()
            ):
                raise HTTPException(status_code=401, detail="Autenticación requerida")
        return FileResponse(str(_safe_media_file(paths.prepared_dir, filename)))

    # ------------------------------------------------------------------
    # Authentication & Users
    # ------------------------------------------------------------------
    @app.get("/api/auth/status", response_model=AuthStatusResponse)
    def auth_status(request: Request) -> AuthStatusResponse:
        ctx = _resolve_auth(request)
        needs_setup = service.needs_setup()
        return AuthStatusResponse(
            authenticated=ctx is not None,
            needs_setup=needs_setup,
            user=ctx.user if ctx else None,
            auth_method=ctx.auth_method if ctx else None,
        )

    @app.post("/api/auth/setup", response_model=AuthLoginResponse)
    def auth_setup(
        payload: UserCreateRequest,
        request: Request,
        response: Response,
    ) -> AuthLoginResponse:
        try:
            user = service.setup_first_user(payload.username, payload.password, actor="setup")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        token, expires_at = service.create_session(
            user.id,
            user_agent=request.headers.get("user-agent", ""),
        )
        _set_session_cookie(response, request, token)
        return AuthLoginResponse(user=user, token=token, expires_at=expires_at)

    @app.post("/api/auth/login", response_model=AuthLoginResponse)
    def auth_login(
        payload: LoginRequest,
        request: Request,
        response: Response,
    ) -> AuthLoginResponse:
        ip = _client_ip(request)
        allowed, retry_after = rate_limiter.check_allowed(ip, payload.username)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Demasiados intentos fallidos. Probá de nuevo en {retry_after} segundos.",
                headers={"Retry-After": str(retry_after)},
            )

        user = service.authenticate_user(payload.username, payload.password)
        if user is None:
            rate_limiter.record_failure(ip, payload.username)
            raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")

        rate_limiter.clear(ip, payload.username)
        token, expires_at = service.create_session(
            user.id,
            user_agent=request.headers.get("user-agent", ""),
        )
        service.db.log_activity("auth.login", actor=user.username, entity_type="user", entity_id=user.id)
        _set_session_cookie(response, request, token)
        return AuthLoginResponse(user=user, token=token, expires_at=expires_at)

    @app.post("/api/auth/logout")
    def auth_logout(request: Request, response: Response) -> dict[str, Any]:
        token = _extract_bearer_token(request) or request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            service.revoke_session_token(token)
        _clear_session_cookie(response, request)
        return {"ok": True}

    @app.get("/api/auth/me", response_model=AuthStatusResponse)
    def auth_me(auth: AuthContext = Depends(_require_auth)) -> AuthStatusResponse:
        return AuthStatusResponse(
            authenticated=True,
            needs_setup=service.needs_setup(),
            user=auth.user,
            auth_method=auth.auth_method,
        )

    @app.post("/api/auth/change-password", response_model=UserPublic)
    def change_own_password(
        payload: ChangePasswordRequest,
        auth: AuthContext = Depends(_require_auth),
    ) -> UserPublic:
        if auth.user is None:
            raise HTTPException(
                status_code=400,
                detail="Este endpoint requiere una sesión de usuario activa.",
            )
        try:
            return service.change_own_password(
                auth.user.id,
                payload.current_password,
                payload.new_password,
                keep_token=auth.session_token,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/users", response_model=list[UserPublic], dependencies=[Depends(_require_auth)])
    def list_users() -> list[UserPublic]:
        return service.list_users()

    @app.post("/api/users", response_model=UserPublic)
    def create_user(
        payload: UserCreateRequest,
        actor: str = Depends(_parse_actor),
    ) -> UserPublic:
        try:
            return service.create_user(payload.username, payload.password, actor=actor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/users/{user_id}/password", response_model=UserPublic)
    def update_user_password(
        user_id: str,
        payload: UserPasswordUpdateRequest,
        auth: AuthContext = Depends(_require_auth),
        actor: str = Depends(_parse_actor),
    ) -> UserPublic:
        keep_token = auth.session_token if (auth.user and auth.user.id == user_id) else None
        try:
            return service.set_user_password(
                user_id,
                payload.password,
                actor=actor,
                keep_token=keep_token,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/users/{user_id}")
    def delete_user(
        user_id: str,
        auth: AuthContext = Depends(_require_auth),
        actor: str = Depends(_parse_actor),
    ) -> dict[str, Any]:
        try:
            service.delete_user(
                user_id,
                actor=actor,
                current_user_id=auth.user.id if auth.user else None,
            )
            return {"deleted": True, "id": user_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Health, stats & agent discovery
    # ------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": "0.1.0",
        }

    @app.get("/api/stats", response_model=DashboardStats, dependencies=[Depends(_require_auth)])
    def stats() -> DashboardStats:
        return service.get_stats()

    @app.get("/api/activity", response_model=list[ActivityLogEntry], dependencies=[Depends(_require_auth)])
    def activity(limit: int = Query(default=50, ge=1, le=500)) -> list[ActivityLogEntry]:
        return service.list_activity(limit=limit)

    @app.get("/api/agent/schema", dependencies=[Depends(_require_auth)])
    def agent_schema() -> dict[str, Any]:
        """Compact, token-efficient agent integration guide (CLI + REST + Code Mode)."""
        return {
            "name": "insta-poster",
            "modes": ["cli", "rest_api", "python_code_mode"],
            "auth": {
                "api_key_header": "X-API-Key: <INSTA_POSTER_API_KEY>",
                "bearer_login": "POST /api/auth/login -> Authorization: Bearer <token>",
            },
            "cli_examples": [
                "insta-poster status --json",
                "insta-poster pool list --status available --unused --json",
                "insta-poster pool pick --type story --unused --count 1 --json",
                "insta-poster pool sync /ruta/a/imagenes --tags producto,verano --json",
                "insta-poster pool update <IMAGE_ID> --notes 'Foto producto fondo blanco' --tags producto --json",
                "insta-poster post create --image <IMAGE_ID> --caption 'Nuevo lanzamiento' --hashtags '#marca,#novedad' --now --json",
                "insta-poster post create --image <ID1> --image <ID2> --caption 'Carrusel' --schedule '2026-11-25T18:00:00Z' --json",
                "insta-poster story create --image <IMAGE_ID> --fit pad_blur --now --json",
                "insta-poster queue list --status scheduled --json",
                "insta-poster queue publish <PUB_ID> --json",
                "insta-poster adapter list --json",
            ],
            "rest_endpoints": {
                "openapi_spec": "/openapi.json",
                "swagger_ui": "/docs",
                "login": "POST /api/auth/login",
                "list_pool": "GET /api/pool?status=available&unused_for=post|story&tag=...",
                "pick_images": "GET /api/pool/pick?pub_type=post|story&count=1&unused_only=true&tag=...",
                "sync_folder": "POST /api/pool/sync",
                "upload_images": "POST /api/pool/upload",
                "update_image": "PATCH /api/pool/{image_id}",
                "create_publication": "POST /api/publications",
                "publish_now": "POST /api/publications/{pub_id}/publish",
                "list_publications": "GET /api/publications?status=scheduled&pub_type=story",
                "adapters": "GET /api/adapters",
            },
            "python_code_mode": (
                "from insta_poster import InstaPoster\n"
                "ip = InstaPoster.http('http://127.0.0.1:8000', api_key='TU_API_KEY')  # o username/password, o InstaPoster.local()\n"
                "img = ip.pick_images(pub_type='story', unused_only=True)[0]\n"
                "pub = ip.create_story(img['id'], fit_mode='pad_blur', publish_now=True)\n"
                "print(pub['status'], pub['external_id'])"
            ),
        }

    # ------------------------------------------------------------------
    # Pool endpoints
    # ------------------------------------------------------------------
    @app.get("/api/pool", response_model=list[PoolImage], dependencies=[Depends(_require_auth)])
    def list_pool(
        status: str | None = None,
        tag: str | None = None,
        suitable_for: str | None = None,
        unused_for: str | None = None,
        search: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[PoolImage]:
        return service.list_pool(
            status=status,
            tag=tag,
            suitable_for=suitable_for,
            unused_for=unused_for,
            search=search,
            limit=limit,
        )

    @app.get("/api/pool/pick", response_model=list[PoolImage], dependencies=[Depends(_require_auth)])
    def pick_pool(
        pub_type: str = Query(default="post", pattern="^(post|carousel|story)$"),
        count: int = Query(default=1, ge=1, le=10),
        tag: str | None = None,
        unused_only: bool = True,
        randomize: bool = True,
    ) -> list[PoolImage]:
        effective_type = "post" if pub_type == "carousel" else pub_type
        return service.pick_images(
            pub_type=effective_type,
            count=count,
            tag=tag,
            unused_only=unused_only,
            randomize=randomize,
        )

    @app.post("/api/pool/upload", response_model=dict[str, Any])
    async def upload_pool_images(
        files: list[UploadFile] = File(...),
        tags: str = Form(default=""),
        notes: str = Form(default=""),
        actor: str = Depends(_parse_actor),
    ) -> dict[str, Any]:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        added: list[PoolImage] = []
        duplicates: list[PoolImage] = []
        errors: list[str] = []

        for file in files:
            filename = file.filename or "upload.jpg"
            try:
                content = await file.read()
                img, created = service.add_image_bytes(
                    content,
                    filename,
                    tags=tag_list,
                    notes=notes,
                    actor=actor,
                )
                if created:
                    added.append(img)
                else:
                    duplicates.append(img)
            except Exception as exc:
                errors.append(f"{filename}: {exc}")

        return {
            "added": [a.model_dump() for a in added],
            "duplicates": [d.model_dump() for d in duplicates],
            "errors": errors,
        }

    @app.post("/api/pool/sync", response_model=FolderSyncResponse)
    def sync_pool_folder(
        payload: FolderSyncRequest,
        actor: str = Depends(_parse_actor),
    ) -> FolderSyncResponse:
        try:
            return service.sync_folder(
                payload.folder_path,
                tags=payload.tags,
                recursive=payload.recursive,
                actor=actor,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/pool/{image_id}", response_model=PoolImage, dependencies=[Depends(_require_auth)])
    def get_pool_image(image_id: str) -> PoolImage:
        try:
            return service.get_image(image_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/pool/{image_id}/preview", dependencies=[Depends(_require_auth)])
    def preview_pool_image(
        image_id: str,
        pub_type: str = Query(default="post", pattern="^(post|carousel|story)$"),
        fit_mode: str = Query(default="auto", pattern="^(auto|crop|pad_blur|pad_solid)$"),
    ) -> Response:
        try:
            img = service.get_image(image_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        tmp = paths.data_dir / f"_preview_{image_id}_{pub_type}_{fit_mode}.jpg"
        try:
            prepare_for_instagram(Path(img.filepath), tmp, pub_type=pub_type, fit_mode=fit_mode)
            data = tmp.read_bytes()
        finally:
            if tmp.exists():
                tmp.unlink()
        return Response(content=data, media_type="image/jpeg")

    @app.patch("/api/pool/{image_id}", response_model=PoolImage)
    def update_pool_image(
        image_id: str,
        payload: PoolImageUpdate,
        actor: str = Depends(_parse_actor),
    ) -> PoolImage:
        try:
            return service.update_image(image_id, payload, actor=actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/pool/{image_id}")
    def delete_pool_image(image_id: str, actor: str = Depends(_parse_actor)) -> dict[str, Any]:
        try:
            service.delete_image(image_id, actor=actor)
            return {"deleted": True, "id": image_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Publications endpoints (Posts, Carousels, Stories)
    # ------------------------------------------------------------------
    @app.get("/api/publications", response_model=list[Publication], dependencies=[Depends(_require_auth)])
    def list_publications(
        status: str | None = None,
        pub_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[Publication]:
        return service.list_publications(status=status, pub_type=pub_type, limit=limit)

    @app.post("/api/publications", response_model=Publication)
    def create_publication(
        payload: PublicationCreate,
        actor: str = Depends(_parse_actor),
    ) -> Publication:
        if payload.created_by in ("user", "web-ui") and actor != "api":
            payload = payload.model_copy(update={"created_by": actor})
        try:
            return service.create_publication(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/publications/{pub_id}", response_model=Publication, dependencies=[Depends(_require_auth)])
    def get_publication(pub_id: str) -> Publication:
        try:
            return service.get_publication(pub_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/publications/{pub_id}", response_model=Publication)
    def update_publication(
        pub_id: str,
        payload: PublicationUpdate,
        actor: str = Depends(_parse_actor),
    ) -> Publication:
        try:
            return service.update_publication(pub_id, payload, actor=actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/publications/{pub_id}/publish", response_model=Publication)
    def publish_now(
        pub_id: str,
        adapter: str | None = None,
        actor: str = Depends(_parse_actor),
    ) -> Publication:
        try:
            return service.publish_publication(pub_id, adapter_override=adapter, actor=actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/publications/{pub_id}")
    def delete_publication(pub_id: str, actor: str = Depends(_parse_actor)) -> dict[str, Any]:
        try:
            service.delete_publication(pub_id, actor=actor)
            return {"deleted": True, "id": pub_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/scheduler/run-due", response_model=list[Publication], dependencies=[Depends(_require_auth)])
    def trigger_scheduler() -> list[Publication]:
        return service.run_due_scheduled()

    # ------------------------------------------------------------------
    # Adapters & settings
    # ------------------------------------------------------------------
    @app.get("/api/adapters", response_model=list[AdapterStatus], dependencies=[Depends(_require_auth)])
    def list_adapters(verify_remote: bool = False) -> list[AdapterStatus]:
        return service.list_adapters(verify_remote=verify_remote)

    @app.post("/api/adapters/{name}/check", response_model=AdapterStatus, dependencies=[Depends(_require_auth)])
    def check_adapter(name: str, verify_remote: bool = True) -> AdapterStatus:
        try:
            return service.check_adapter(name, verify_remote=verify_remote)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/settings", dependencies=[Depends(_require_auth)])
    def get_settings() -> dict[str, Any]:
        return service.get_sanitized_settings()

    @app.patch("/api/settings")
    def update_settings(
        payload: AdapterConfigUpdate,
        actor: str = Depends(_parse_actor),
    ) -> dict[str, Any]:
        return service.update_settings(payload, actor=actor)

    # ------------------------------------------------------------------
    # Frontend SPA static serving (when built into backend/insta_poster/static)
    # ------------------------------------------------------------------
    if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend_assets")

        resolved_static = STATIC_DIR.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str):
            if full_path in ("api", "media") or full_path.startswith(("api/", "media/")):
                raise HTTPException(status_code=404, detail="Not found")
            if full_path:
                candidate = (resolved_static / full_path).resolve()
                if resolved_static in candidate.parents and candidate.is_file():
                    return FileResponse(str(candidate))
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app


def app_factory() -> FastAPI:
    return create_app()
