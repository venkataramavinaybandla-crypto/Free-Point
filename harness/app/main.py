"""Main FastAPI application entry point."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .auth.gmail_oauth import gmail_oauth_router
from .auth.routes import auth_router
from .core.worker import run_execute_consumer
from .security.headers import SecurityHeadersMiddleware
from .web import page_router, web_router

WEB_STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = None
    if os.environ.get("EXECUTE_CONSUMER_ENABLED", "false").lower() == "true":
        task = asyncio.create_task(run_execute_consumer())
    yield
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Free Point Agent Harness",
    description="Agent Harness as a Service (HaaS) API Gateway",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(WEB_STATIC_DIR)), name="static")

app.include_router(auth_router)
app.include_router(gmail_oauth_router)
app.include_router(router)
app.include_router(page_router)
app.include_router(web_router)
