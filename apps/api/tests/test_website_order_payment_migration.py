# Tests for patch 19a: the payment columns migration for storefront_orders.
# Plain sync tests. They read the migration file with ast and never use a database.
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
VERS = os.path.join(HERE, "..", "alembic", "versions")
SIX = {"payment_status", "payment_method", "amount_received_kobo", "paid_at", "paid_recorded_by", "payment_note"}


def _ours():
    return sorted(f for f in os.listdir(VERS) if f.startswith("wopay01paid") and f.endswith(".py"))


def _path():
    files = _ours()
    assert len(files) == 1, files
    return os.path.join(VERS, files[0])


def _raw():
    with open(_path(), "rb") as fh:
        return fh.read()


def _tree():
    return ast.parse(_raw().decode("utf-8"))


def _assigned(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Constant):
                    return node.value.value
    return None


def _func(tree, name):
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, name
    return found[0]


def _op_calls(func, attr):
    out = []
    for node in ast.walk(func):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "op"):
            out.append(node)
    return out


def _added_columns(func):
    cols = {}
    for call in _op_calls(func, "add_column"):
        assert isinstance(call.args[0], ast.Constant) and call.args[0].value == "storefront_orders"
        col = call.args[1]
        cols[col.args[0].value] = col
    return cols


def test_migration_file_exists_once():
    assert os.path.isfile(_path())


def test_revision_chain():
    tree = _tree()
    assert _assigned(tree, "revision") == "wopay01paid"
    assert _assigned(tree, "down_revision") == "cust4packs01"


def test_upgrade_adds_the_six_columns():
    assert set(_added_columns(_func(_tree(), "upgrade"))) == SIX


def test_payment_status_is_not_null_with_default_unpaid():
    col = _added_columns(_func(_tree(), "upgrade"))["payment_status"]
    kw = {k.arg: k.value for k in col.keywords}
    assert isinstance(kw["nullable"], ast.Constant) and kw["nullable"].value is False
    assert isinstance(kw["server_default"], ast.Constant) and kw["server_default"].value == "unpaid"


def test_downgrade_drops_the_six_columns():
    func = _func(_tree(), "downgrade")
    dropped = set()
    for call in _op_calls(func, "drop_column"):
        assert call.args[0].value == "storefront_orders"
        dropped.add(call.args[1].value)
    assert dropped == SIX


def test_single_head_and_crlf_without_bom():
    pointing = []
    for name in sorted(os.listdir(VERS)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(VERS, name), "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
        if re.search(r"^down_revision\s*=\s*\"cust4packs01\"", text, re.M):
            pointing.append(name)
    assert pointing == _ours(), pointing
    raw = _raw()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0
