# Tests for patch 18c: the dead duplicate /order-confirmation route is gone
# from the shop main.py. Plain sync tests with ast. They never import the shop.
import ast
import os

SHOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "ecommerce")
MAIN = os.path.join(SHOP, "main.py")
CHECKOUT = os.path.join(SHOP, "routers", "checkout.py")


def _raw(path):
    with open(path, "rb") as fh:
        return fh.read()


def _tree(path):
    return ast.parse(_raw(path).decode("utf-8-sig"))


def _route_paths(tree):
    paths = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
                    paths.append(dec.args[0].value)
    return paths


def test_main_has_no_order_confirmation_route():
    assert "/order-confirmation" not in _route_paths(_tree(MAIN))


def test_checkout_still_serves_order_confirmation():
    assert "/order-confirmation" in _route_paths(_tree(CHECKOUT))


def test_checkout_still_redirects_to_it():
    assert b'RedirectResponse(url="/order-confirmation"' in _raw(CHECKOUT)


def test_main_keeps_error_handlers_and_routers():
    tree = _tree(MAIN)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    assert "bad_gateway" in names
    assert "not_found" in names
    text = _raw(MAIN).decode("utf-8")
    assert text.count("app.include_router(") == 5


def test_main_is_valid_lf_without_bom():
    raw = _raw(MAIN)
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    ast.parse(raw.decode("utf-8"))
