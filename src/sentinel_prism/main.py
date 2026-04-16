"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from sentinel_prism.api.routes import auth, health, sources


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from sentinel_prism.services.auth.providers.factory import get_auth_provider
    from sentinel_prism.services.auth.tokens import _algorithm, _secret
    from sentinel_prism.workers.poll_scheduler import get_poll_scheduler

    _secret()
    _algorithm()
    get_auth_provider()  # fail-fast: raises ValueError for unknown AUTH_PROVIDER at startup
    sched = get_poll_scheduler()
    scheduler_started = await sched.start()
    try:
        yield
    finally:
        if scheduler_started:
            await sched.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Sentinel Prism", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(sources.router)
    return app


app = create_app()
