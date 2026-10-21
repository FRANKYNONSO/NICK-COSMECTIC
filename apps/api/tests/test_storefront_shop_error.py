# Script tests for patch 14a: shop error text and logging. PATCH14A-SHOP-ERROR-TEXT
# Plain sync tests. They read the storefront files with ast and never import them.
import ast
from pathlib import Path

ECO = Path(__file__).resolve().parents[2] / "ecommerce"
FILES = {
    "storefront": ECO / "routers" / "storefront.py",
    "cart": ECO / "routers" / "cart.py",
}
EXPECTED_502 = {"storefront": 3, "cart": 2}
OLD_WORDS = "Could not reach the catalog service"
NEW_TEXT = "Sorry, the shop could not load right now. Please try again in a few minutes. If it keeps happening, please contact us."


def _source(name):
    return FILES[name].read_text(encoding="utf-8")


def _tree(name):
    return ast.parse(_source(name))


def _http_exception_keywords(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPException":
            out.append({k.arg: k.value for k in node.keywords})
    return out


def _status(kw):
    value = kw.get("status_code")
    return value.value if isinstance(value, ast.Constant) else None


def _shop_error_value(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_SHOP_ERROR":
                    if isinstance(node.value, ast.Constant):
                        return node.value.value
    return None


def _logger_exception_calls(tree):
    count = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "exception"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            count += 1
    return count


def test_a_storefront_old_words_are_gone():
    assert OLD_WORDS not in _source("storefront")


def test_b_cart_old_words_are_gone():
    assert OLD_WORDS not in _source("cart")


def test_c_storefront_every_502_uses_the_shop_error():
    kws = [k for k in _http_exception_keywords(_tree("storefront")) if _status(k) == 502]
    assert len(kws) == EXPECTED_502["storefront"], len(kws)
    for kw in kws:
        detail = kw.get("detail")
        assert isinstance(detail, ast.Name) and detail.id == "_SHOP_ERROR", ast.dump(detail)


def test_d_cart_every_502_uses_the_shop_error():
    kws = [k for k in _http_exception_keywords(_tree("cart")) if _status(k) == 502]
    assert len(kws) == EXPECTED_502["cart"], len(kws)
    for kw in kws:
        detail = kw.get("detail")
        assert isinstance(detail, ast.Name) and detail.id == "_SHOP_ERROR", ast.dump(detail)


def test_e_message_text_is_exact_and_plain():
    for name in FILES:
        value = _shop_error_value(_tree(name))
        assert value == NEW_TEXT, (name, value)
    assert "login" not in NEW_TEXT.lower()
    assert "401" not in NEW_TEXT


def test_f_each_handler_logs_the_real_reason():
    assert _logger_exception_calls(_tree("storefront")) >= EXPECTED_502["storefront"]
    assert _logger_exception_calls(_tree("cart")) >= EXPECTED_502["cart"]


def test_g_not_found_still_gives_404():
    for name in FILES:
        found = [k for k in _http_exception_keywords(_tree(name)) if _status(k) == 404]
        assert len(found) >= 1, name
        details = [k["detail"].value for k in found if isinstance(k.get("detail"), ast.Constant)]
        assert "Product not found" in details, (name, details)
