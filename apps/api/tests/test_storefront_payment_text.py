# Patch 12b tests: payment instructions on the storefront confirmation page.
# Code is read out of config.py and checkout.py with ast (the storefront app is
# never imported: it needs secrets). The template is rendered with jinja2.
import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ECOM = Path(__file__).resolve().parents[2] / "ecommerce"
CHECKOUT = ECOM / "routers" / "checkout.py"
CONFIG = ECOM / "config.py"
NAIRA = "\u20a6"
ENV_NAMES = (
    "NICK_BANK_NAME",
    "NICK_BANK_ACCOUNT_NAME",
    "NICK_BANK_ACCOUNT_NUMBER",
    "NICK_PAYMENT_PHONE",
)


def _load_fn(name):
    tree = ast.parse(CHECKOUT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {}
            exec(compile(mod, "checkout_" + name, "exec"), ns)
            return ns[name]
    raise AssertionError(name + " not found in checkout.py")


def _settings_class():
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {"os": os}
            exec(compile(mod, "config_settings", "exec"), ns)
            return ns["Settings"]
    raise AssertionError("Settings class not found in config.py")


def _render(order, payment):
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(ECOM / "templates")), autoescape=True
    )
    tpl = env.get_template("order-confirmation.html")
    return tpl.render(
        site_name="Nick",
        request=SimpleNamespace(session={}),
        order=order,
        payment=payment,
    )


def _order(fee):
    summary = _load_fn("_confirmation_summary")
    base = {"id": "ABC12345", "total_naira": 10000.0, "shipping_message": "msg"}
    base.update(summary(10000.0, fee))
    return base


def _bank(**over):
    vals = dict(
        BANK_NAME="Test Bank",
        BANK_ACCOUNT_NAME="Nick Cosmetics Ltd",
        BANK_ACCOUNT_NUMBER="0123456789",
        PAYMENT_PHONE="",
    )
    vals.update(over)
    return _load_fn("_payment_info")(SimpleNamespace(**vals))


def test_a_settings_defaults_are_empty(monkeypatch):
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)
    s = _settings_class()()
    got = (s.BANK_NAME, s.BANK_ACCOUNT_NAME, s.BANK_ACCOUNT_NUMBER, s.PAYMENT_PHONE)
    print("EVIDENCE a defaults:", got)
    assert got == ("", "", "", "")


def test_b_settings_read_environment(monkeypatch):
    monkeypatch.setenv("NICK_BANK_NAME", "  First Test Bank  ")
    monkeypatch.setenv("NICK_BANK_ACCOUNT_NAME", "Nick Cosmetics Ltd")
    monkeypatch.setenv("NICK_BANK_ACCOUNT_NUMBER", "0123456789")
    monkeypatch.setenv("NICK_PAYMENT_PHONE", "0800 111 2222")
    s = _settings_class()()
    print("EVIDENCE b bank name after strip:", repr(s.BANK_NAME))
    assert s.BANK_NAME == "First Test Bank"
    assert s.BANK_ACCOUNT_NAME == "Nick Cosmetics Ltd"
    assert s.BANK_ACCOUNT_NUMBER == "0123456789"
    assert s.PAYMENT_PHONE == "0800 111 2222"


def test_c_payment_info_all_set():
    r = _bank()
    print("EVIDENCE c:", r)
    assert r == {
        "bank_enabled": True,
        "bank_name": "Test Bank",
        "account_name": "Nick Cosmetics Ltd",
        "account_number": "0123456789",
        "payment_phone": "",
    }


def test_d_payment_info_incomplete_shows_nothing_partial():
    r = _bank(BANK_ACCOUNT_NUMBER="")
    print("EVIDENCE d:", r)
    assert r["bank_enabled"] is False
    assert r["bank_name"] == "" and r["account_name"] == "" and r["account_number"] == ""


def test_e_payment_info_phone_only():
    r = _bank(BANK_NAME="", BANK_ACCOUNT_NAME="", BANK_ACCOUNT_NUMBER="", PAYMENT_PHONE="0800 111 2222")
    print("EVIDENCE e:", r)
    assert r["bank_enabled"] is False
    assert r["payment_phone"] == "0800 111 2222"


def test_f_page_bank_with_phone():
    html = _render(_order(2000), _bank(PAYMENT_PHONE="0800 111 2222"))
    print("EVIDENCE f has bank block:", "Bank transfer" in html)
    assert "1. Bank transfer" in html
    assert "Bank: Test Bank" in html
    assert "Account name: Nick Cosmetics Ltd" in html
    assert "Account number: 0123456789" in html
    assert "Use your order number #ABC12345 as the reason for payment." in html
    assert "call or message us on 0800 111 2222 so we can confirm." in html
    assert "2. Pay on delivery" in html


def test_g_page_bank_without_phone():
    html = _render(_order(2000), _bank(PAYMENT_PHONE=""))
    assert "1. Bank transfer" in html
    assert "contact us so we can confirm your payment" in html
    assert "call or message us on" not in html


def test_h_page_no_bank_pay_on_delivery():
    html = _render(_order(2000), _bank(BANK_ACCOUNT_NUMBER=""))
    print("EVIDENCE h pay-on-delivery text:", "You can pay on delivery" in html)
    assert "You can pay on delivery." in html
    assert "please contact us" in html
    assert "Account number" not in html
    assert "Bank transfer" not in html


def test_i_page_unknown_delivery_cost_line():
    other = _render(_order(-1), _bank(BANK_ACCOUNT_NUMBER=""))
    taraba = _render(_order(2000), _bank(BANK_ACCOUNT_NUMBER=""))
    print("EVIDENCE i other state has final-amount line:", "final amount" in other)
    assert "We will tell you the final amount before you pay." in other
    assert "final amount" not in taraba


def test_j_old_session_no_final_amount_line():
    order = {"id": "ABC12345", "total_naira": 10000.0, "shipping_message": "m"}
    html = _render(order, _bank(BANK_ACCOUNT_NUMBER=""))
    assert "final amount" not in html
    assert "Total: " + NAIRA + "10,000" in html


def test_k_bank_text_is_escaped():
    html = _render(_order(0), _bank(BANK_NAME="<b>X</b>"))
    assert "&lt;b&gt;X&lt;/b&gt;" in html
    assert "<b>X</b>" not in html


def test_l_route_passes_payment_to_page():
    src = CHECKOUT.read_text(encoding="utf-8")
    needle = '"payment": _payment_info(settings)'
    print("EVIDENCE l wiring present:", needle in src)
    assert needle in src
