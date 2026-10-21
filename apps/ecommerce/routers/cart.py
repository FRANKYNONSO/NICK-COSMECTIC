import logging

import httpx
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

from api_client import api_client
from config import settings

router = APIRouter(prefix="/cart")
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("storefront")

# PATCH14A-SHOP-ERROR-TEXT
_SHOP_ERROR = "Sorry, the shop could not load right now. Please try again in a few minutes. If it keeps happening, please contact us."


def _get_cart(request: Request) -> dict:
    return request.session.setdefault("cart", {})


async def _fetch_product(product_id: str) -> dict:
    try:
        return await api_client.get_product(product_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Product not found")
        logger.exception("Catalog lookup failed (the API answered with an error)")
        raise HTTPException(status_code=502, detail=_SHOP_ERROR)
    except httpx.HTTPError:
        logger.exception("Catalog lookup failed (could not reach the API)")
        raise HTTPException(status_code=502, detail=_SHOP_ERROR)


@router.post("/add")
async def add_to_cart(
    request: Request,
    product_id: str = Form(...),
    quantity: int = Form(1),
):
    quantity = max(quantity, 1)
    product = await _fetch_product(product_id)

    # Stock is read live from inventory; never trust the browser. The backend
    # still performs the final check at checkout.
    available = product.get("stock_qty", 0)
    if product.get("status") != "active" or available <= 0:
        return RedirectResponse(url=f"/product/{product_id}", status_code=303)

    cart = _get_cart(request)
    already = cart.get(product_id, {}).get("quantity", 0)
    cart[product_id] = {
        "name": product.get("name"), "size": product.get("size"),  # PATCH16A-SIZE-DISPLAY
        "price": product.get("price"),
        "wholesale_price": product.get("wholesale_price_naira"),
        "wholesale_min": product.get("wholesale_min_quantity"),
        "image_url": product.get("image_url"),
        "quantity": min(already + quantity, available),
    }
    request.session["cart"] = cart
    return RedirectResponse(url="/cart", status_code=303)


@router.get("", response_class=HTMLResponse)
async def view_cart(request: Request):
    cart = _get_cart(request)
    items = []
    for pid, item in cart.items():
        unit = api_client.unit_price_for(
            item.get("price"), item.get("wholesale_price"), item.get("wholesale_min"), item["quantity"]
        )
        items.append({"product_id": pid, **item, "price": unit})
    subtotal = sum(item["price"] * item["quantity"] for item in items)

    return templates.TemplateResponse(
        request,
        "cart.html",
        {"site_name": settings.SITE_NAME, "items": items, "subtotal": subtotal},
    )


@router.post("/remove")
async def remove_from_cart(request: Request, product_id: str = Form(...)):
    cart = _get_cart(request)
    cart.pop(product_id, None)
    request.session["cart"] = cart
    return RedirectResponse(url="/cart", status_code=303)


@router.post("/update")
async def update_cart_quantity(
    request: Request, product_id: str = Form(...), quantity: int = Form(...)
):
    cart = _get_cart(request)
    if product_id in cart:
        if quantity < 1:
            cart.pop(product_id, None)
        else:
            product = await _fetch_product(product_id)
            cart[product_id]["quantity"] = min(quantity, max(product.get("stock_qty", 0), 1))
    request.session["cart"] = cart
    return RedirectResponse(url="/cart", status_code=303)
