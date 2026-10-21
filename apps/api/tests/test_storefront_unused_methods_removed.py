# Tests for patch 18b: unused storefront API-client methods are removed.
# Plain sync tests. They read files and use ast. They never import the shop.
import ast
import os

SHOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "ecommerce")
CLIENT = os.path.join(SHOP, "api_client.py")

REMOVED = ("create_customer", "redeem_coupon", "checkout", "get_stock_levels")
KEPT = (
    "_get", "_get_service_token", "_authed_post", "_authed_get",
    "is_listed", "attach_stock", "list_products", "get_combined_stock",
    "_stock_by_product", "unit_price_for", "create_storefront_order",
    "check_coupon", "list_products_with_stock", "get_product", "list_categories",
)


def _raw():
    with open(CLIENT, "rb") as fh:
        return fh.read()


def _text():
    return _raw().decode("utf-8")


def _function_names():
    tree = ast.parse(_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def test_removed_methods_are_gone():
    names = _function_names()
    for name in REMOVED:
        assert name not in names, "still defined: " + name


def test_outdated_comment_is_gone():
    text = _text()
    assert "no longer accepts GET" not in text
    assert "Stock authority" not in text


def test_needed_methods_still_exist():
    names = _function_names()
    for name in KEPT:
        assert name in names, "missing: " + name


def test_client_file_is_valid_lf_without_bom():
    raw = _raw()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    ast.parse(raw.decode("utf-8"))


def _shop_python_files():
    files = []
    for name in sorted(os.listdir(SHOP)):
        if name.endswith(".py") and name != "api_client.py" and not name.startswith("_"):
            files.append(os.path.join(SHOP, name))
    routers = os.path.join(SHOP, "routers")
    for name in sorted(os.listdir(routers)):
        if name.endswith(".py"):
            files.append(os.path.join(routers, name))
    return files


def test_no_shop_file_calls_the_removed_methods():
    for path in _shop_python_files():
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            tree = ast.parse(raw.decode("utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in REMOVED:
                raise AssertionError(os.path.basename(path) + " uses ." + node.attr)
