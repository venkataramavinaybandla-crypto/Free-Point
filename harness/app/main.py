"""Main FastAPI application entry point."""

from fastapi import FastAPI

from .api.routes import router

app = FastAPI(
    title="Free Point Agent Harness",
    description="Agent Harness as a Service (HaaS) API Gateway",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(router)
