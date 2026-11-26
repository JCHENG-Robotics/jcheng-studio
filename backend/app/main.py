"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import uuid
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from . import __version__
from .adapters.hardware import HardwareAdapter
from .adapters.mock import MockRobotAdapter
from .api.routes import router
from .config import Settings
from .domain.enums import Mode
from .errors import ServiceError
from .persistence.store import EventStore
from .service import RobotService


class SPAStaticFiles(StaticFiles):
    """Static files with an index.html fallback for client-side routes.

    The frontend uses history routing (`/joints`, `/settings`, ...). Serving
    `dist/` with a plain StaticFiles mount returns 404 for a deep link because
    no such file exists. Fall back to index.html for non-API paths so the
    router can handle them; keep unknown `/api/...` paths as real 404s.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path == "api" or path.startswith("api/"):
            return await super().get_response(path, scope)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = await super().get_response("index.html", scope)
        self._apply_cache_headers(path, response)
        return response

    @staticmethod
    def _apply_cache_headers(path: str, response: Response) -> None:
        """Keep the SPA shell revalidated; hashed assets can be cached forever.

        Without this the browser heuristically caches index.html while the
        rebuild deletes the asset hashes it referenced, so the UI keeps running
        an old bundle (missing newer pages) until a cache-busted reload.
        """
        name = path.rsplit("/", 1)[-1]
        is_html = response.headers.get("content-type", "").startswith("text/html")
        if is_html or name.endswith(".html") or name in ("", ".", "index"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        elif path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"


async def _ticker(service: RobotService) -> None:
    while True:
        # The adapter talks to the gateway over blocking urllib. Keep that
        # timeout off the event loop; sync HTTP handlers already run in the
        # AnyIO worker pool, so the ticker must not be the only blocking path.
        await asyncio.to_thread(service.tick)
        await asyncio.sleep(0.05)


def _make_service(settings: Settings, clock: Callable[[], float] | None) -> RobotService:
    mapping = settings.load_mapping()
    takeover = settings.load_takeover()
    store = EventStore(settings.db_path)
    adapter = (
        MockRobotAdapter(mapping, clock=clock, takeover=takeover)
        if settings.mode is Mode.MOCK
        else HardwareAdapter(
            mapping,
            clock=clock,
            gateway_url=settings.gateway_url,
            gateway_token=settings.gateway_token,
            camera_gateway_url=settings.camera_gateway_url,
            camera_gateway_token=settings.camera_gateway_token,
            motion_enabled=settings.hardware_motion_enabled,
            supervised=settings.supervised_motion,
            motion_scope=settings.motion_scope,
            takeover=takeover,
        )
    )
    return RobotService(
        settings, mapping, adapter, store, clock=clock, takeover=takeover
    )


def create_app(
    settings: Settings | None = None,
    *,
    mode: str | None = None,
    db_path: str | None = None,
    clock: Callable[[], float] | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env(mode=mode, db_path=db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = _make_service(resolved, clock)
        app.state.service = service
        ticker = asyncio.create_task(_ticker(service))
        try:
            yield
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
            service.close()

    app = FastAPI(
        title="ArmPi Debug Studio",
        version=__version__,
        description=(
            "ArmPi 安全调试工作台：Mock 完整演示；硬件默认只读，运动需显式启用并按 "
            "G6 单关节 / G7 姿态 / G8 任务接管确认。"
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.clock = clock

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Armpi-Token"],
    )

    @app.middleware("http")
    async def backend_token_guard(request: Request, call_next):
        """Optional shared-token auth for a non-loopback deployment.

        The default localhost bind has no token and no behavior change. A
        non-loopback bind is rejected at startup unless ARMPI_BACKEND_TOKEN is
        set, so there is no unauthenticated remote mode.
        """
        token = resolved.api_token
        if (
            token
            and request.method != "OPTIONS"
            and request.url.path.startswith("/api")
            and request.url.path != "/api/health"
        ):
            supplied = request.headers.get("X-Armpi-Token") or (
                request.query_params.get("token") or ""
            )
            if not hmac.compare_digest(supplied, token):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "missing or invalid X-Armpi-Token",
                        }
                    },
                )
        return await call_next(request)

    app.include_router(router)

    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
                "trace_id": uuid.uuid4().hex,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "request validation failed",
                    "details": {"errors": exc.errors()},
                },
                "trace_id": uuid.uuid4().hex,
            },
        )

    dist = resolved.frontend_dist
    if (dist / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="frontend")

    return app


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
