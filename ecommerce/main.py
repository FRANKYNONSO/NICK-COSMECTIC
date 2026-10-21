import sys
import os

# Ensure this directory is on the import path — needed because uvicorn's
# --reload subprocess doesn't always inherit the working directory's path
# the same way the parent process does (seen on Python 3.14 / Windows).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates

from routers import storefront
from config import settings

app = FastAPI(title=f"{settings.SITE_NAME} — Storefront")

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(storefront.router)

templates = Jinja2Templates(directory="templates")


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