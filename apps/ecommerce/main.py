import sys
import os

# Ensure this directory is on the import path â€” needed because uvicorn's
# --reload subprocess doesn't always inherit the working directory's path
# the same way the parent process does (seen on Python 3.14 / Windows).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from routers import storefront, cart, checkout, advisor, advisor_chat
from config import settings

app = FastAPI(title=f"{settings.SITE_NAME} — Storefront")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    same_site="lax",
    https_only=settings.SESSION_HTTPS_ONLY,
    max_age=60 * 60 * 24 * 7,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(storefront.router)
app.include_router(cart.router)
app.include_router(checkout.router)
app.include_router(advisor_chat.router)
app.include_router(advisor.router)

templates = Jinja2Templates(directory="templates")


# PATCH18C-DEAD-ROUTE-REMOVED: the live /order-confirmation route is in routers/checkout.py


@app.exception_handler(502)
async def bad_gateway(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"site_name": settings.SITE_NAME, "message": exc.detail},
        status_code=502,
    )


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"site_name": settings.SITE_NAME, "message": "Page not found"},
        status_code=404,
    )