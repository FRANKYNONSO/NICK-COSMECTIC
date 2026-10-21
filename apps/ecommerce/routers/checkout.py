import json
import logging
import re
import time
import uuid

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from api_client import api_client
from config import settings
from nigeria_states_lgas import NIGERIA_STATE_LGAS

router = APIRouter()
logger = logging.getLogger("storefront.checkout")
templates = Jinja2Templates(directory="templates")

NIGERIA_STATES = list(NIGERIA_STATE_LGAS.keys())

FREE_LGA = "Takum"
FREE_LGA_STATE = "Taraba"
TARABA_FLAT_FEE = 2000


def _get_cart(request: Request) -> dict:
    return request.session.setdefault("cart", {})


def _shipping_fee(state: str, lga: str | None) -> tuple[float, str]:
    """Returns (fee, message). Fee of -1 means 'confirm later' (unknown cost)."""
    if state == FREE_LGA_STATE:
        if lga == FREE_LGA:
            return 0, "Free delivery (Takum, Taraba)."
        return TARABA_FLAT_FEE, f"Delivery fee: ₦{TARABA_FLAT_FEE:,.0f} (Taraba, outside Takum)."
    return -1, "Delivery cost will be confirmed via phone or email before your order ships."


# PATCH12A-CONFIRM-TOTAL
def _confirmation_summary(items_naira, shipping_fee):
    """Numbers for the order confirmation page.

    items_naira is the order total from the API (items, after any coupon).
    shipping_fee -1 means the delivery cost is not known yet: no total is shown.
    """
    items = float(items_naira)
    fee = float(shipping_fee)
    payable = None if fee < 0 else items + fee
    return {"items_naira": items, "shipping_fee": fee, "payable_naira": payable}


# PATCH12B-PAYMENT-INSTRUCTIONS
def _payment_info(s):
    """What the confirmation page shows about how to pay.

    The bank block is shown only when bank name, account name and account
    number are ALL set. The payment phone is optional. Nothing partial is
    ever returned: if the bank block is off, its fields are empty.
    """
    bank = (getattr(s, "BANK_NAME", "") or "").strip()
    holder = (getattr(s, "BANK_ACCOUNT_NAME", "") or "").strip()
    number = (getattr(s, "BANK_ACCOUNT_NUMBER", "") or "").strip()
    phone = (getattr(s, "PAYMENT_PHONE", "") or "").strip()
    enabled = bool(bank and holder and number)
    return {
        "bank_enabled": enabled,
        "bank_name": bank if enabled else "",
        "account_name": holder if enabled else "",
        "account_number": number if enabled else "",
        "payment_phone": phone,
    }


def _checkout_context(request: Request, items: list, subtotal: float, error: str | None = None) -> dict:
    ctx = {
        "site_name": settings.SITE_NAME,
        "items": items,
        "subtotal": subtotal,
        "states": NIGERIA_STATES,
        "state_lgas_json": json.dumps(NIGERIA_STATE_LGAS),
    }
    if error:
        ctx["error"] = error
    return ctx


@router.get("/checkout", response_class=HTMLResponse)
async def checkout_form(request: Request):
    items, subtotal = _session_items(_get_cart(request))

    return templates.TemplateResponse(
        request, "checkout.html", _checkout_context(request, items, subtotal)
    )


def _session_items(cart: dict) -> tuple[list[dict], float]:
    """Cart lines from the session with the wholesale rule applied, for display."""
    items = []
    for pid, item in cart.items():
        unit = api_client.unit_price_for(
            item.get("price"), item.get("wholesale_price"), item.get("wholesale_min"), item["quantity"]
        )
        items.append({"product_id": pid, **item, "price": unit})
    return items, sum(i["price"] * i["quantity"] for i in items)


def _build_order_payload(
    *, reference, full_name, email, phone, state, lga, city, address_line,
    coupon_code, shipping_fee, shipping_message, items,
) -> dict:
    """The body sent to POST /api/v1/storefront/orders. Prices are NOT sent:
    the API prices the order itself."""
    return {
        "reference": reference,
        "customer": {
            "full_name": full_name.strip(),
            "phone": phone.strip(),
            "email": (email or "").strip() or None,
        },
        "delivery": {
            "address_line": address_line.strip(),
            "city": (city or "").strip() or None,
            "lga": (lga or "").strip() or None,
            "state": state.strip(),
        },
        "items": [{"product_id": i["product_id"], "quantity": i["quantity"]} for i in items],
        "coupon_code": (coupon_code or "").strip() or None,
        # -1 means "delivery cost to be confirmed by phone": send 0 plus the note.
        "shipping_fee_naira": max(shipping_fee, 0),
        "shipping_note": shipping_message,
    }


def _api_error_message(exc: httpx.HTTPStatusError, default: str) -> str:
    """Show the backend's own message only for plain business-rule errors
    (e.g. insufficient stock); never dump raw responses on customers."""
    if exc.response.status_code in (400, 409):
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        if isinstance(detail, str) and len(detail) < 200:
            return detail
    return default


async def _revalidate_cart(cart: dict) -> tuple[list[dict], list[str]]:
    """Re-read every cart line from the catalog and live inventory so the
    price and stock used for the order are current, not whatever was saved
    in the session when the item was added. Raises httpx.HTTPError if the
    catalog/inventory can't be reached."""
    items: list[dict] = []
    problems: list[str] = []
    for pid, line in cart.items():
        product = await api_client.get_product(pid)
        name = product.get("name") or line.get("name") or "An item"
        qty = int(line.get("quantity", 0))
        price = api_client.unit_price_for(
            product.get("price"),
            product.get("wholesale_price_naira"),
            product.get("wholesale_min_quantity"),
            qty,
        )
        stock = product.get("stock_qty", 0)

        if product.get("status") != "active" or price <= 0:
            problems.append(f"{name} is no longer available.")
        elif stock <= 0:
            problems.append(f"{name} is out of stock.")
        elif qty > stock:
            problems.append(f"Only {stock} of {name} available (you have {qty} in your cart).")
        else:
            items.append(
                {
                    "product_id": pid,
                    "name": name,
                    "price": price,
                    "quantity": qty,
                    "image_url": product.get("image_url"), "size": product.get("size"),  # PATCH16A-SIZE-DISPLAY
                }
            )
    return items, problems


# PATCH15A-EMAIL-CHECK: the email must look like name@host.tld
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_BAD_EMAIL_TEXT = "Please enter a valid email address, for example name@example.com."


def _email_looks_valid(value) -> bool:
    return _EMAIL_RE.fullmatch((value or "").strip()) is not None


@router.post("/checkout")
async def submit_checkout(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    state: str = Form(...),
    lga: str = Form(""),
    city: str = Form(""),
    address_line: str = Form(...),
    coupon_code: str = Form(""),
):
    cart = _get_cart(request)
    session_items, session_subtotal = _session_items(cart)

    def _render_error(message: str, status_code: int = 200):
        return templates.TemplateResponse(
            request,
            "checkout.html",
            _checkout_context(request, session_items, session_subtotal, error=message),
            status_code=status_code,
        )

    if not cart:
        return _render_error("Your cart is empty.")

    if not _email_looks_valid(email):
        return _render_error(_BAD_EMAIL_TEXT)

    # Guessing protection: only submissions that include a coupon code count.
    if (coupon_code or "").strip() and _coupon_attempts_blocked(request):
        return _render_error(
            "Too many coupon attempts. Please wait a few minutes and try again, "
            "or place your order without a coupon.",
            status_code=429,
        )

    # 1. Revalidate price + stock against live inventory.
    try:
        items, problems = await _revalidate_cart(cart)
    except httpx.HTTPError:
        logger.exception("Could not revalidate cart at checkout")
        return _render_error("We couldn't check stock right now. Please try again in a moment.")
    if problems:
        return _render_error(" ".join(problems) + " Please update your cart and try again.")

    shipping_fee, shipping_message = _shipping_fee(state, lga)

    # 2. One call places the whole order. The API takes stock from every
    #    branch that has it, redeems the coupon in the same transaction
    #    (a failed order can't burn it), and returns the order record.
    #    The reference is created once per checkout session so a
    #    double-click or retry can't place the order twice.
    reference = request.session.setdefault("checkout_ref", str(uuid.uuid4()))
    payload = _build_order_payload(
        reference=reference, full_name=full_name, email=email, phone=phone,
        state=state, lga=lga, city=city, address_line=address_line,
        coupon_code=coupon_code, shipping_fee=shipping_fee,
        shipping_message=shipping_message, items=items,
    )
    try:
        order = await api_client.create_storefront_order(payload)
    except httpx.HTTPError as e:
        logger.exception("create_storefront_order failed")
        default = "We couldn't complete your order. Please try again or contact us."
        if isinstance(e, httpx.HTTPStatusError):
            return _render_error(_api_error_message(e, default))
        return _render_error(default)

    request.session["cart"] = {}
    request.session.pop("checkout_ref", None)
    request.session["last_order"] = {
        "id": str(order["id"])[:8].upper(),
        "total_naira": order["total_naira"],
        "shipping_message": shipping_message,
        **_confirmation_summary(order["total_naira"], shipping_fee),
    }
    return RedirectResponse(url="/order-confirmation", status_code=303)


# ---------------------------------------------------------- coupon preview
# Shows the customer the discount BEFORE they pay. It is only a preview: the
# API re-checks and applies the real discount when the order is placed.

_COUPON_CHECK_LIMIT = 10  # checks allowed per key ...
_COUPON_CHECK_WINDOW = 600  # ... in this many seconds
_coupon_check_log: dict[str, list[float]] = {}


def _too_many_coupon_checks(key: str, now: float | None = None) -> bool:
    """Sliding window so nobody can guess codes through the preview box.
    Kept in memory: it resets when the storefront restarts."""
    now = time.time() if now is None else now
    recent = [t for t in _coupon_check_log.get(key, []) if now - t < _COUPON_CHECK_WINDOW]
    if len(recent) >= _COUPON_CHECK_LIMIT:
        _coupon_check_log[key] = recent
        return True
    recent.append(now)
    _coupon_check_log[key] = recent
    if len(_coupon_check_log) > 5000:  # keep memory bounded
        for k in [k for k, v in _coupon_check_log.items() if not v or now - v[-1] >= _COUPON_CHECK_WINDOW]:
            _coupon_check_log.pop(k, None)
    return False


def _coupon_attempts_blocked(request: Request) -> bool:
    """True when this visitor has tried too many coupon codes recently.
    Shares the counter used by the preview box, so tries in both places
    count toward one limit."""
    ip = request.client.host if request.client else "unknown"
    session_key = request.session.setdefault("cc_id", str(uuid.uuid4()))
    return _too_many_coupon_checks(f"ip:{ip}") or _too_many_coupon_checks(f"s:{session_key}")


def _coupon_preview_payload(result: dict, subtotal: float) -> dict:
    """Turns the API's answer into the message the customer sees."""
    if not result.get("valid"):
        return {
            "ok": True,
            "valid": False,
            "message": "This code is not valid. It may have expired, been used already, or been typed wrongly.",
        }
    if subtotal <= 0:
        return {"ok": True, "valid": True, "message": "This code is valid. Add items to your cart to use it."}
    amount = float(result.get("discount_amount_naira") or 0)
    discount = min(amount, subtotal)
    after = subtotal - discount
    return {
        "ok": True,
        "valid": True,
        "discount_naira": discount,
        "subtotal_after": after,
        "message": (
            f"Coupon accepted: you save \u20A6{discount:,.0f}. Subtotal after discount: \u20A6{after:,.0f}. "
            "Delivery is extra, and the final amount is confirmed when you place the order."
        ),
    }


@router.get("/checkout/coupon-preview")
async def coupon_preview(request: Request, code: str = ""):
    code = (code or "").strip()
    if not code:
        return JSONResponse({"ok": False, "valid": False, "message": "Enter a coupon code first."})
    if len(code) > 50:
        return JSONResponse(_coupon_preview_payload({"valid": False}, 0))

    ip = request.client.host if request.client else "unknown"
    session_key = request.session.setdefault("cc_id", str(uuid.uuid4()))
    if _too_many_coupon_checks(f"ip:{ip}") or _too_many_coupon_checks(f"s:{session_key}"):
        return JSONResponse(
            {"ok": False, "valid": None, "message": "Too many attempts. Please wait a few minutes and try again."},
            status_code=429,
        )

    _, subtotal = _session_items(_get_cart(request))
    try:
        result = await api_client.check_coupon(code)
    except httpx.HTTPError:
        logger.exception("Coupon preview could not reach the API")
        return JSONResponse(
            {
                "ok": False,
                "valid": None,
                "message": "We couldn't check the code right now. You can still place your order; the code is checked again then.",
            }
        )
    return JSONResponse(_coupon_preview_payload(result, subtotal))


@router.get("/order-confirmation", response_class=HTMLResponse)
async def order_confirmation(request: Request):
    order = request.session.get("last_order")
    return templates.TemplateResponse(
        request,
        "order-confirmation.html",
        {"site_name": settings.SITE_NAME, "order": order, "payment": _payment_info(settings)},
    )