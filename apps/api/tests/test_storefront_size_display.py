# PATCH16A-SIZE-DISPLAY
# Script tests: the shop shows the product size next to the name.
# Plain sync tests. They never import the storefront (it needs secrets).
import ast
from pathlib import Path

import jinja2

ECO = Path(__file__).resolve().parents[3] / "apps" / "ecommerce"


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


def _line(name, size, price, qty=1, with_size_key=True):
    line = {"product_id": "p-" + name, "name": name, "price": price, "quantity": qty, "image_url": None}
    if with_size_key:
        line["size"] = size
    return line


def _list_page(products):
    return _env().get_template("product_list.html").render(
        site_name="S", categories=[], category=None, search=None,
        total=len(products), products=products, page=1, has_next=False)


def _home_page(products):
    return _env().get_template("index.html").render(site_name="S", categories=[], products=products)


def _cart_page(items):
    subtotal = sum(i["price"] * i["quantity"] for i in items)
    return _env().get_template("cart.html").render(site_name="S", items=items, subtotal=subtotal)


def _checkout_summary(items):
    text = (ECO / "templates" / "checkout.html").read_text(encoding="utf-8-sig")
    start = text.index("{% for item in items %}")
    end = text.index("{% endfor %}", start) + len("{% endfor %}")
    return _env().from_string(text[start:end]).render(items=items)


def _dict_keys(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            yield {k.value for k in sub.keys if isinstance(k, ast.Constant)}


def test_cart_stores_size():
    tree = ast.parse((ECO / "routers" / "cart.py").read_text(encoding="utf-8"))
    assert any({"name", "price", "size"} <= keys for keys in _dict_keys(tree))


def test_revalidated_lines_keep_size():
    tree = ast.parse((ECO / "routers" / "checkout.py").read_text(encoding="utf-8"))
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef) and n.name == "_revalidate_cart"]
    assert len(funcs) == 1
    assert any({"product_id", "name", "size"} <= keys for keys in _dict_keys(funcs[0]))


def test_shop_list_shows_size():
    html = _list_page([_product("Dettol Liquid", "BIG", 15000.0), _product("Soap", None, 1500.0)])
    assert "Dettol Liquid (BIG)" in html
    assert "<h3>Soap</h3>" in html


def test_home_cards_show_size():
    html = _home_page([_product("Dettol Liquid", "BIG", 15000.0), _product("Soap", None, 1500.0)])
    assert "Dettol Liquid (BIG)" in html
    assert "<h3>Soap</h3>" in html


def test_cart_shows_size_and_unit_price():
    items = [_line("Dettol Liquid", "MEDIUM", 3500.0, 2), _line("Dettol Liquid ", "MEDIUM", 6000.0, 1)]
    html = _cart_page(items)
    assert html.count("(MEDIUM)") == 2
    assert "3,500 each" in html
    assert "6,000 each" in html


def test_checkout_summary_shows_size():
    html = _checkout_summary([_line("Dettol Liquid", "BIG", 15000.0, 2), _line("Soap", None, 1500.0, 1)])
    assert "Dettol Liquid (BIG)" in html
    assert "Soap" in html


def test_no_size_shows_plain_name():
    plain = _line("Soap", None, 1500.0)
    old_cart_line = _line("Soap", None, 1500.0, with_size_key=False)
    pages = [
        _list_page([_product("Soap", None, 1500.0)]),
        _home_page([_product("Soap", None, 1500.0)]),
        _cart_page([plain, old_cart_line]),
        _checkout_summary([plain, old_cart_line]),
    ]
    for html in pages:
        assert "Soap" in html
        assert "()" not in html
        assert "None" not in html
