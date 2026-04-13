"""FastAPI application entrypoint."""

from fastapi import FastAPI

from sentinel_prism.api.routes import health


def create_app() -> FastAPI:
    app = FastAPI(title="Sentinel Prism", version="0.1.0")
    app.include_router(health.router)
    return app


app = create_app()
