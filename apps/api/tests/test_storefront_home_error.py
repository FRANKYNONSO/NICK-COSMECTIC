# Tests for patch 18d: the Home page shows the shop error text when the API fails.
# Plain sync tests. They never import the shop (it needs secrets).
import ast
from pathlib import Path

import jinja2

ECO = Path(__file__).resolve().parents[3] / "apps" / "ecommerce"
STORE = ECO / "routers" / "storefront.py"
INDEX = ECO / "templates" / "index.html"
ERROR_TEXT = "Sorry, the shop could not load right now. Please try again in a few minutes. If it keeps happening, please contact us."
OLD_PRODUCTS = "No products to show yet."
OLD_CATEGORIES = "No categories to show yet."


def _env():
    base = "{% block title %}{% endblock %}{% block content %}{% endblock %}"
    loaders = [
        jinja2.DictLoader({"base.html": base}),
        jinja2.FileSystemLoader(str(ECO / "templates"), encoding="utf-8-sig"),
    ]
    return jinja2.Environment(loader=jinja2.ChoiceLoader(loaders), autoescape=True)


def _product(name, size, price):
    return {"id": "id-" + name, "name": name, "size": size, "price": price,
            "image_url": None, "stock_qty": 5, "is_low_stock": False}


def _render(**extra):
    context = {"site_name": "S", "categories": [], "products": []}
    context.update(extra)
    return _env().get_template("index.html").render(**context)


def _home_func():
    tree = ast.parse(STORE.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "home"]
    assert len(found) == 1
    return found[0]


def test_api_failure_shows_shop_error_text():
    html = _render(load_error=True, error_text=ERROR_TEXT)
    assert ERROR_TEXT in html
    assert OLD_PRODUCTS not in html
    assert OLD_CATEGORIES not in html
    assert "<h2>Categories</h2>" not in html
    assert "Featured Products" in html


def test_empty_shop_keeps_old_texts():
    html = _render(load_error=False, error_text=ERROR_TEXT)
    assert OLD_PRODUCTS in html
    assert OLD_CATEGORIES in html
    assert ERROR_TEXT not in html


def test_page_without_the_flag_still_renders():
    html = _render()
    assert OLD_PRODUCTS in html
    assert OLD_CATEGORIES in html
    assert ERROR_TEXT not in html


def test_working_page_is_unchanged():
    html = _render(load_error=False, error_text=ERROR_TEXT,
                   categories=[{"id": "c1", "name": "Face"}],
                   products=[_product("Soap", None, 1500.0)])
    assert "<h2>Categories</h2>" in html
    assert "Face" in html
    assert "<h3>Soap</h3>" in html
    assert ERROR_TEXT not in html
    assert OLD_PRODUCTS not in html


def test_home_route_passes_flag_and_text():
    home = _home_func()
    values = {}
    for node in ast.walk(home):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant):
                    values[key.value] = value
    assert "load_error" in values
    assert "error_text" in values
    assert isinstance(values["error_text"], ast.Name)
    assert values["error_text"].id == "_SHOP_ERROR"


def test_home_route_sets_flag_and_logs_on_failure():
    home = _home_func()
    tries = [n for n in home.body if isinstance(n, ast.Try)]
    assert len(tries) == 1
    before = []
    for stmt in home.body:
        if stmt is tries[0]:
            break
        before.append(stmt)
    assert any(
        isinstance(s, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "load_error" for t in s.targets)
        and isinstance(s.value, ast.Constant) and s.value.value is False
        for s in before
    )
    handlers = [h for h in tries[0].handlers
                if isinstance(h.type, ast.Attribute) and h.type.attr == "HTTPError"]
    assert len(handlers) == 1
    body_nodes = [n for stmt in handlers[0].body for n in ast.walk(stmt)]
    assert any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "load_error" for t in n.targets)
        and isinstance(n.value, ast.Constant) and n.value.value is True
        for n in body_nodes
    )
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "exception"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "logger"
        for n in body_nodes
    )


def test_both_files_are_lf_without_bom():
    for path in (STORE, INDEX):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r" not in raw, path.name
        raw.decode("utf-8")
