from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import logging

import httpx

from api_client import api_client
from config import settings

router = APIRouter()
logger = logging.getLogger("storefront")

# PATCH14A-SHOP-ERROR-TEXT
_SHOP_ERROR = "Sorry, the shop could not load right now. Please try again in a few minutes. If it keeps happening, please contact us."
PER_PAGE = 12
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # PATCH18D-HOME-LOAD-ERROR
    load_error = False
    try:
        featured = await api_client.list_products_with_stock(page=1)
        categories = await api_client.list_categories()
    except httpx.HTTPError:
        logger.exception("Home page: could not load products or categories")
        featured, categories = [], []
        load_error = True

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "site_name": settings.SITE_NAME,
            "products": featured[:8],
            "categories": categories,
            "load_error": load_error,
            "error_text": _SHOP_ERROR,
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
        all_results = await api_client.list_products_with_stock(category=category, search=search)
        categories = await api_client.list_categories()
    except httpx.HTTPError:
        logger.exception("Shop page: catalog or stock call failed")
        raise HTTPException(status_code=502, detail=_SHOP_ERROR)

    total = len(all_results)
    start = (page - 1) * PER_PAGE
    result = all_results[start:start + PER_PAGE]

    return templates.TemplateResponse(
        request,
        "product_list.html",
        {
            "site_name": settings.SITE_NAME,
            "products": result,
            "total": total,
            "page": page,
            "has_next": start + PER_PAGE < total,
            "category": category,
            "search": search,
            "categories": categories,
        },
    )


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"site_name": settings.SITE_NAME})


@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request):
    return templates.TemplateResponse(
        request,
        "contact.html",
        {
            "site_name": settings.SITE_NAME,
            "phone": settings.CONTACT_PHONE,
            "whatsapp": settings.CONTACT_WHATSAPP,
            "email": settings.CONTACT_EMAIL,
            "address": settings.CONTACT_ADDRESS,
        },
    )


@router.get("/policy", response_class=HTMLResponse)
async def policy(request: Request):
    return templates.TemplateResponse(request, "policy.html", {"site_name": settings.SITE_NAME})


@router.get("/product/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: str):
    try:
        product = await api_client.get_product(product_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Product not found")
        logger.exception("Catalog lookup failed (the API answered with an error)")
        raise HTTPException(status_code=502, detail=_SHOP_ERROR)
    except httpx.HTTPError:
        logger.exception("Catalog lookup failed (could not reach the API)")
        raise HTTPException(status_code=502, detail=_SHOP_ERROR)

    if product.get("status") != "active":
        raise HTTPException(status_code=404, detail="Product not found")

    return templates.TemplateResponse(
        request,
        "product_detail.html",
        {"site_name": settings.SITE_NAME, "product": product},
    )