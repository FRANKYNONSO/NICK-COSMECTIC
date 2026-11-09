"""N1 tests: role display names (Cashier, Super Admin).

No database needed: the migration is run against a recorder and
staff.html is read as text. PATCHROLE-NAMES
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve()
MIGRATION = HERE.parents[1] / "alembic" / "versions" / "rolenames01_cashier_super_admin_names.py"
STAFF_HTML = HERE.parents[2] / "web_admin" / "staff.html"


class _Bind:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))


class _FakeOp:
    def __init__(self):
        self.bind = _Bind()

    def get_bind(self):
        return self.bind


def _load():
    spec = importlib.util.spec_from_file_location("rolenames01", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chain_follows_permseed01():
    mod = _load()
    assert mod.revision == "rolenames01"
    assert mod.down_revision == "permseed01"


def test_upgrade_renames_only_matching_rows():
    mod = _load()
    fake = _FakeOp()
    mod.op = fake
    mod.upgrade()
    calls = fake.bind.calls
    assert [p for _, p in calls] == [
        {"name": "staff", "old": "Cashiers", "new": "Cashier"},
        {"name": "super_admin", "old": "BOSS", "new": "Super Admin"},
    ]
    for sql, _ in calls:
        assert "SET display_name = :new" in sql
        assert "WHERE name = :name AND display_name = :old" in sql


def test_downgrade_restores_old_names():
    mod = _load()
    fake = _FakeOp()
    mod.op = fake
    mod.downgrade()
    assert [p for _, p in fake.bind.calls] == [
        {"name": "staff", "old": "Cashier", "new": "Cashiers"},
        {"name": "super_admin", "old": "Super Admin", "new": "BOSS"},
    ]


def test_only_the_two_display_names_are_touched():
    mod = _load()
    assert [k for k, _, _ in mod._CHANGES] == ["staff", "super_admin"]
    assert "SET name" not in mod._SQL


def test_staff_html_pickers_singular_filter_plural():
    html = STAFF_HTML.read_text(encoding="utf-8")
    assert html.count("<option value=\"cashier\">Cashier</option>") == 2
    assert html.count("<option value=\"cashier\">Cashiers</option>") == 1
    assert "PATCHROLE-NAMES" in html
