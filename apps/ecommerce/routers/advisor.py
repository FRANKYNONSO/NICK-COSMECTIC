"""Skincare advisor routes (storefront only).

GET  /advisor            the 7-step assessment page
POST /advisor/recommend  JSON in, JSON out

The shortlist is built by advisor_engine (deterministic, catalog-only,
in-stock-only). The optional AI only rewords the explanations of that
shortlist, and its output is validated. Any failure of weather or AI just
means plainer results; browsing, cart and checkout are never touched.
"""
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import advisor_engine as engine
import advisor_extras as extras
from api_client import api_client
from config import settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "advisor_catalog.json")
_meta_cache = {"mtime": None, "meta": {}}


def _load_meta():
    """Reload the catalog file whenever it changes on disk. A broken file
    means 'no products enabled', never a crash."""
    path = os.path.normpath(_CATALOG_PATH)
    try:
        mtime = os.path.getmtime(path)
        if mtime != _meta_cache["mtime"]:
            _meta_cache["meta"] = engine.load_catalog_meta(path)
            _meta_cache["mtime"] = mtime
    except Exception:
        _meta_cache["meta"] = {}
        _meta_cache["mtime"] = None
    return _meta_cache["meta"]


def _client_id(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return "adv:" + ip


@router.get("/advisor", response_class=HTMLResponse)
async def advisor_page(request: Request):
    return templates.TemplateResponse(request, "advisor.html", {"site_name": settings.SITE_NAME})


@router.post("/advisor/recommend")
async def advisor_recommend(request: Request):
    if extras.too_many(_client_id(request)):
        return JSONResponse(
            {"ok": False, "message": "Too many requests. Please wait a few minutes and try again."},
            status_code=429,
        )
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "Something went wrong with your answers."}, status_code=400)

    answers = extras.sanitize_answers(raw)
    meta = _load_meta()

    try:
        products = await api_client.list_products_with_stock()
    except Exception:
        return JSONResponse(
            {"ok": False, "message": "We could not check the shop right now. Please try again shortly, or browse the shop."},
            status_code=502,
        )
    stock = {str(p.get("id")): int(p.get("stock_qty") or 0) for p in products}

    weather = await extras.fetch_weather(httpx, answers)
    result = engine.recommend(answers, products, stock, meta, weather)
    items = result["items"]

    ai_used = False
    if items:
        raw_llm = await extras.ask_llm(httpx, items, answers)
        if raw_llm:
            names = [p.get("name") for p in products]
            good = engine.validate_llm_output(raw_llm, items, names)
            for it in items:
                if it["product_id"] in good:
                    it["reasons"] = [good[it["product_id"]]]
                    ai_used = True

    by_id = {str(p.get("id")): p for p in products}
    out_items = []
    for it in items:
        p = by_id.get(it["product_id"], {})
        out_items.append({
            "product_id": it["product_id"],
            "name": it["name"],
            "price_naira": it["price_naira"],
            "size": it.get("size"),
            "reasons": it["reasons"],
            "details_on_file": it["details_on_file"],
            "image_url": p.get("image_url"),
            "url": "/product/" + it["product_id"],
        })

    return JSONResponse({
        "ok": True,
        "items": out_items,
        "message": result["message"],
        "weather": (weather or {}).get("label"),
        "ai_used": ai_used,
    })
