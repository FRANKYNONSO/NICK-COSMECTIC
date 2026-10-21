# Patch 12a tests: storefront confirmation page Items / Delivery / Total to pay.
# The helper is read out of checkout.py with ast (no import of the storefront app,
# which needs secrets). The template is rendered with jinja2 if it is installed.
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

ECOM = Path(__file__).resolve().parents[2] / "ecommerce"
CHECKOUT = ECOM / "routers" / "checkout.py"
NAIRA = "\u20a6"


def _load_helper():
    src = CHECKOUT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_confirmation_summary":
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {}
            exec(compile(mod, "checkout_helper", "exec"), ns)
            return ns["_confirmation_summary"]
    raise AssertionError("_confirmation_summary not found in checkout.py")


def _render(order):
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(ECOM / "templates")), autoescape=True
    )
    tpl = env.get_template("order-confirmation.html")
    return tpl.render(site_name="Nick", request=SimpleNamespace(session={}), order=order)


def _order(fee, message):
    helper = _load_helper()
    base = {"id": "ABC12345", "total_naira": 10000.0, "shipping_message": message}
    base.update(helper(10000.0, fee))
    return base


def test_a_helper_takum_free():
    r = _load_helper()(10000.0, 0)
    print("EVIDENCE a:", r)
    assert r == {"items_naira": 10000.0, "shipping_fee": 0.0, "payable_naira": 10000.0}


def test_b_helper_taraba_fee_added():
    r = _load_helper()(10000.0, 2000)
    print("EVIDENCE b:", r)
    assert r["payable_naira"] == 12000.0 and r["shipping_fee"] == 2000.0


def test_c_helper_unknown_fee_has_no_total():
    r = _load_helper()(10000.0, -1)
    print("EVIDENCE c:", r)
    assert r["payable_naira"] is None and r["shipping_fee"] == -1.0


def test_d_page_takum():
    html = _render(_order(0, "Free delivery (Takum, Taraba)."))
    print("EVIDENCE d page has Items/Delivery/Total lines:", "Items:" in html)
    assert "Items: " + NAIRA + "10,000" in html
    assert "Delivery: Free" in html
    assert "Total to pay: " + NAIRA + "10,000" in html


def test_e_page_taraba():
    html = _render(_order(2000, "Delivery fee."))
    assert "Items: " + NAIRA + "10,000" in html
    assert "Delivery: " + NAIRA + "2,000" in html
    assert "Total to pay: " + NAIRA + "12,000" in html


def test_f_page_other_state():
    html = _render(_order(-1, "Delivery cost will be confirmed via phone or email before your order ships."))
    assert "Items: " + NAIRA + "10,000" in html
    assert "Delivery: to be confirmed by phone or email" in html
    assert "Total to pay: to be confirmed" in html
    assert "Total to pay: " + NAIRA not in html


def test_g_old_session_still_shows_old_total():
    order = {"id": "ABC12345", "total_naira": 10000.0, "shipping_message": "m"}
    html = _render(order)
    assert "Total: " + NAIRA + "10,000" in html
    assert "Items:" not in html


def test_h_checkout_stores_the_summary():
    src = CHECKOUT.read_text(encoding="utf-8")
    needle = '**_confirmation_summary(order["total_naira"], shipping_fee)'
    print("EVIDENCE h wiring present:", needle in src)
    assert needle in src
