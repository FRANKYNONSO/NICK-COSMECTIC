"""Beauty Advisor chat routes (storefront only; never touches POS or inventory).

GET  /advisor          the chat page
GET  /advisor/classic  the original 7-step wizard (still works, still free)
POST /advisor/chat     one chat turn, JSON in / JSON out

This router is included BEFORE routers.advisor in main.py, so its /advisor
page is the one served. The wizard's own POST /advisor/recommend is untouched.
Any failure here returns a plain message; browsing, cart and checkout are
never affected.
"""
import secrets

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import advisor_flow as flow
import advisor_turn as turn
from api_client import api_client
from config import settings
from routers.advisor import _load_meta  # same catalog file and reload logic as the wizard

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _ip(request: Request) -> str:
    return "advchat:" + (request.client.host if request.client else "unknown")


def _visitor_id(request: Request) -> str:
    vid = request.session.get("adv_vid")
    if not vid:
        vid = secrets.token_hex(8)
        request.session["adv_vid"] = vid
    return vid


@router.get("/advisor", response_class=HTMLResponse)
async def advisor_chat_page(request: Request):
    return templates.TemplateResponse(
        request, "advisor_chat.html",
        {"site_name": settings.SITE_NAME, "disclaimer": flow.DISCLAIMER},
    )


@router.get("/advisor/classic", response_class=HTMLResponse)
async def advisor_classic_page(request: Request):
    return templates.TemplateResponse(request, "advisor.html", {"site_name": settings.SITE_NAME})


@router.post("/advisor/chat")
async def advisor_chat(request: Request):
    # Coarse limit per network address (generous: many phones share one address).
    if flow.chat_rate_limited(_ip(request), limit=240, window=600):
        return JSONResponse({"ok": False, "message": "Too many requests. Please wait a few minutes and try again."},
                            status_code=429)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "Something went wrong. Please try again."}, status_code=400)
    # Tighter limit per visitor session.
    vid = _visitor_id(request)
    if flow.chat_rate_limited("v:" + vid, limit=80, window=600):
        return JSONResponse({"ok": False, "message": "You are sending messages very quickly. Please wait a moment."},
                            status_code=429)
    try:
        products = await api_client.list_products_with_stock()
    except Exception:
        return JSONResponse(
            {"ok": False, "message": "We could not check the shop right now. Please try again shortly, or browse the shop."},
            status_code=502,
        )
    stock = {str(p.get("id")): int(p.get("stock_qty") or 0) for p in products}
    try:
        data = await turn.handle(body, request.session, vid, products, stock, _load_meta(), httpx)
    except Exception:
        return JSONResponse({"ok": False, "message": "Something went wrong on our side. Please try again."},
                            status_code=500)
    return JSONResponse(data)
