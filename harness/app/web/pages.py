"""Page routes for the Free Point web UI."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

PAGE_DIR = Path(__file__).resolve().parents[3] / "web" / "pages"

page_router = APIRouter(include_in_schema=False)


def page_response(name: str) -> FileResponse:
    return FileResponse(PAGE_DIR / name, media_type="text/html")


@page_router.get("/")
async def landing_page() -> FileResponse:
    return page_response("index.html")


@page_router.get("/signup")
async def signup_page() -> FileResponse:
    return page_response("signup.html")


@page_router.get("/login")
async def login_page() -> FileResponse:
    return page_response("login.html")


@page_router.get("/dashboard")
async def dashboard_page() -> FileResponse:
    return page_response("dashboard.html")


@page_router.get("/settings")
async def settings_page() -> FileResponse:
    return page_response("settings.html")
