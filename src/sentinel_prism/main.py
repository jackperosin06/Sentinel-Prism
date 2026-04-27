"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware

from sentinel_prism.api.routes import (
    audit_events,
    auth,
    briefings,
    classification_policy,
    dashboard,
    delivery_attempts,
    feedback_metrics,
    golden_set_policy,
    health,
    notifications,
    ops,
    routing_rules,
    runs,
    sources,
    updates,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from sentinel_prism.graph import compile_regulatory_pipeline_graph
    from sentinel_prism.graph.checkpoints import (
        dev_memory_checkpointer,
        postgres_uri_for_langgraph,
        use_postgres_pipeline_checkpointer,
    )
    from sentinel_prism.graph.runtime import set_regulatory_graph
    from sentinel_prism.services.auth.providers.factory import get_auth_provider
    from sentinel_prism.services.auth.tokens import _algorithm, _secret
    from sentinel_prism.workers.digest_scheduler import get_digest_scheduler
    from sentinel_prism.workers.poll_scheduler import get_poll_scheduler

    _secret()
    _algorithm()
    get_auth_provider()  # fail-fast: raises ValueError for unknown AUTH_PROVIDER at startup

    # An ``AsyncExitStack`` guarantees that every resource acquired during
    # startup is torn down during shutdown, even if a later startup step
    # raises before the ``yield`` is reached. Previously the checkpointer
    # context would leak when e.g. ``compile_regulatory_pipeline_graph`` or
    # ``sched.start`` raised after ``checkpointer_cm.__aenter__``.
    async with AsyncExitStack() as stack:
        if use_postgres_pipeline_checkpointer():
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            db_url = os.environ.get("DATABASE_URL", "").strip()
            if not db_url:
                raise RuntimeError(
                    "Postgres pipeline checkpointer selected but DATABASE_URL is empty"
                )
            uri = postgres_uri_for_langgraph(db_url)
            checkpointer_cm = AsyncPostgresSaver.from_conn_string(uri)
            pipeline_checkpointer = await stack.enter_async_context(checkpointer_cm)
            await pipeline_checkpointer.setup()
        else:
            pipeline_checkpointer = dev_memory_checkpointer()

        _graph = compile_regulatory_pipeline_graph(checkpointer=pipeline_checkpointer)
        app.state.regulatory_graph = _graph
        set_regulatory_graph(_graph)

        sched = get_poll_scheduler()
        scheduler_started = await sched.start()

        async def _shutdown_scheduler() -> None:
            if not scheduler_started:
                return
            try:
                await sched.shutdown()
            except Exception:
                # Swallow scheduler-shutdown failures so the exit stack still
                # runs the checkpointer's ``__aexit__`` and we do not leak a
                # Postgres connection.
                logger.exception("poll_scheduler shutdown raised during lifespan exit")

        stack.push_async_callback(_shutdown_scheduler)

        digest_sched = get_digest_scheduler()
        digest_scheduler_started = await digest_sched.start()

        async def _shutdown_digest_scheduler() -> None:
            if not digest_scheduler_started:
                return
            try:
                await digest_sched.shutdown()
            except Exception:
                logger.exception("digest_scheduler shutdown raised during lifespan exit")

        stack.push_async_callback(_shutdown_digest_scheduler)

        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Sentinel Prism", version="0.1.0", lifespan=lifespan)

    def _request_id_from(request: Request) -> str:
        rid = getattr(getattr(request, "state", None), "request_id", None)
        return str(rid or "")

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_request_id_handler(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        # `exc.detail` may be any object (including Exception instances) depending
        # on call sites; ensure the payload is JSON-serializable.
        resp = JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder({"detail": exc.detail}),
        )
        rid = _request_id_from(request)
        if rid:
            resp.headers["X-Request-Id"] = rid
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_request_id_handler(
        request: Request, exc: RequestValidationError
    ) -> Response:
        # Pydantic error contexts can include non-JSON-serializable objects
        # (e.g., ValueError instances in `ctx`); encode defensively.
        resp = JSONResponse(
            status_code=422,
            content=jsonable_encoder({"detail": exc.errors()}),
        )
        rid = _request_id_from(request)
        if rid:
            resp.headers["X-Request-Id"] = rid
        return resp

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Simple, dependency-free request correlation id for NFR8. Use a header
        # so operators can copy/paste it into log search tools.
        rid = request.headers.get("x-request-id")
        try:
            request_id = str(uuid.UUID(str(rid))) if rid else str(uuid.uuid4())
        except ValueError:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        resp: Response = await call_next(request)
        resp.headers["X-Request-Id"] = request_id
        logger.info(
            "http_request",
            extra={
                "event": "http_request",
                "ctx": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": resp.status_code,
                },
            },
        )
        return resp

    _cors = os.environ.get("CORS_ORIGINS", "").strip()
    if _cors:
        cors_origins = [x.strip() for x in _cors.split(",") if x.strip()]
    else:
        cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Browsers reject ``Access-Control-Allow-Origin: *`` alongside
    # ``Allow-Credentials: true``, so an operator setting ``CORS_ORIGINS=*``
    # with the previous ``allow_credentials=True`` config produced silent
    # preflight failures in every dev-origin browser. Detect the wildcard
    # and drop credentials instead (Bearer tokens in the auth header do not
    # require credentialed cookies anyway — the ``Authorization`` header is
    # not a "credential" in CORS parlance).
    cors_allow_credentials = "*" not in cors_origins
    if not cors_allow_credentials:
        logger.warning(
            "cors_wildcard_drops_credentials",
            extra={
                "event": "cors_config",
                "ctx": {"origins": cors_origins, "allow_credentials": False},
            },
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(dashboard.router)
    app.include_router(updates.router)
    app.include_router(audit_events.router)
    app.include_router(auth.router)
    app.include_router(sources.router)
    app.include_router(runs.router)
    app.include_router(runs.review_queue_router)
    app.include_router(briefings.router)
    app.include_router(notifications.router)
    app.include_router(delivery_attempts.router)
    app.include_router(routing_rules.router)
    app.include_router(feedback_metrics.router)
    app.include_router(classification_policy.router)
    app.include_router(golden_set_policy.router)
    app.include_router(ops.router)
    return app


app = create_app()
