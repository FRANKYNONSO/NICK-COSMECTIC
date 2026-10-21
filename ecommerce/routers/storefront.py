from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import httpx

from api_client import api_client
from config import settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    try:
        featured = await api_client.list_products(page=1)
        categories = await api_client.list_categories()
    except httpx.HTTPError:
        featured, categories = {"items": [], "total": 0}, []

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "site_name": settings.SITE_NAME,
            "products": featured.get("items", [])[:8],
            "categories": categories,
        },
    )


@router.get("/shop", response_class=HTMLResponse)
async def shop(
    request: Request,
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    try:
        result = await api_client.list_products(category=category, search=search, page=page)
        categories = await api_client.list_categories()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not reach the catalog service. Try again shortly.")

    return templates.TemplateResponse(
        request,
        "product_list.html",
        {
            "site_name": settings.SITE_NAME,
            "products": result.get("items", []),
            "total": result.get("total", 0),
            "page": page,
            "category": category,
            "search": search,
            "categories": categories,
        },
    )


@router.get("/product/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: int):
    try:
        product = await api_client.get_product(product_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Product not found")
        raise HTTPException(status_code=502, detail="Could not reach the catalog service.")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not reach the catalog service.")

    return templates.TemplateResponse(
        request,
        "product_detail.html",
        {"site_name": settings.SITE_NAME, "product": product},
    )