"""Web UI package: HTML pages and session-protected JSON routes."""

from .pages import page_router
from .routes import web_router

__all__ = ["page_router", "web_router"]
