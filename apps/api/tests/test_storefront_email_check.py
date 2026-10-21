# Tests for patch 15a: the storefront checks the shape of the email address.
# Plain sync tests. They read the storefront files as text and use ast.
# They never import the storefront (it needs secrets).
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SHOP = os.path.normpath(os.path.join(HERE, "..", "..", "ecommerce"))
CHECKOUT_PY = os.path.join(SHOP, "routers", "checkout.py")
CHECKOUT_HTML = os.path.join(SHOP, "templates", "checkout.html")
MARKER = "PATCH15A-EMAIL-CHECK"
MESSAGE = "Please enter a valid email address, for example name@example.com."


def _read(path):
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8")


def _rule():
    # Pull the rule out of checkout.py without importing the storefront.
    tree = ast.parse(_read(CHECKOUT_PY))
    wanted = {"_EMAIL_RE", "_BAD_EMAIL_TEXT", "_email_looks_valid"}
    keep = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted:
                keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            keep.append(node)
    module = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"re": re}
    exec(compile(module, "checkout_rule", "exec"), ns)
    return ns


def _email_tag():
    html = _read(CHECKOUT_HTML)
    m = re.search(r'<input[^>]*id="email"[^>]*>', html)
    assert m, "no email input found in checkout.html"
    return m.group(0)


def test_01_rule_exists_in_checkout_py():
    ns = _rule()
    assert "_EMAIL_RE" in ns, "checkout.py has no _EMAIL_RE"
    assert "_BAD_EMAIL_TEXT" in ns, "checkout.py has no _BAD_EMAIL_TEXT"
    assert "_email_looks_valid" in ns, "checkout.py has no _email_looks_valid"


def test_02_good_emails_are_accepted():
    ns = _rule()
    assert "_email_looks_valid" in ns, "checkout.py has no _email_looks_valid"
    check = ns["_email_looks_valid"]
    for good in ["name@gmail.com", "a.b+c@mail.example.co", "  name@gmail.com  ", "x@y.ng"]:
        assert check(good) is True, good


def test_03_bad_emails_are_refused():
    ns = _rule()
    assert "_email_looks_valid" in ns, "checkout.py has no _email_looks_valid"
    check = ns["_email_looks_valid"]
    bad_list = ["name@gmail", "name", "name@", "@gmail.com", "name@gmail.c",
                "na me@gmail.com", "a@b@c.com", "", "   ", None]
    for bad in bad_list:
        assert check(bad) is False, repr(bad)


def test_04_message_text_is_the_approved_one():
    ns = _rule()
    assert "_BAD_EMAIL_TEXT" in ns, "checkout.py has no _BAD_EMAIL_TEXT"
    assert ns["_BAD_EMAIL_TEXT"] == MESSAGE


def test_05_route_refuses_bad_email_before_stock_check():
    src = _read(CHECKOUT_PY)
    func = None
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "submit_checkout":
            func = node
    assert func is not None, "submit_checkout not found"
    body = ast.get_source_segment(src, func)
    assert "_email_looks_valid(email)" in body, "the route does not check the email"
    assert "_render_error(_BAD_EMAIL_TEXT)" in body, "the route does not show the message"
    i_cart = body.index('_render_error("Your cart is empty.")')
    i_mail = body.index("_email_looks_valid(email)")
    i_stock = body.index("_revalidate_cart(")
    assert i_cart < i_mail < i_stock, "the email check is in the wrong place"


def test_06_html_email_field_has_rule_and_message():
    tag = _email_tag()
    assert 'type="email"' in tag
    assert " required" in tag
    assert "pattern=" in tag, "the email field has no pattern"
    assert 'title="' + MESSAGE + '"' in tag, "the email field has no message"


def test_07_html_rule_equals_python_rule():
    ns = _rule()
    assert "_EMAIL_RE" in ns, "checkout.py has no _EMAIL_RE"
    m = re.search(r'pattern="([^"]*)"', _email_tag())
    assert m, "the email field has no pattern"
    assert m.group(1) == ns["_EMAIL_RE"].pattern


def test_08_marker_is_in_both_files_once():
    assert _read(CHECKOUT_PY).count(MARKER) == 1
    assert _read(CHECKOUT_HTML).count(MARKER) == 1
