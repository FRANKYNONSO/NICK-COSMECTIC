# P3a: Warehouse page and link need use_warehouse or manage_inventory.
# TEXT CHECKS ONLY. These tests read the JS source files. They do not run a browser.
from pathlib import Path

JS = Path(__file__).resolve().parents[3] / "apps" / "web_admin" / "js"
MARK = "PATCHP3A-WAREHOUSE-ACCESS"


def _src(name):
    return (JS / name).read_bytes().decode("utf-8")


def test_a_warehouse_page_guard_uses_new_permissions():
    assert "Perms.guardPageAny(['use_warehouse', 'manage_inventory']);" in _src("warehouse.js")


def test_b_old_view_stock_page_guard_is_gone():
    assert "Perms.guardPageAny(['view_stock', 'manage_inventory']);" not in _src("warehouse.js")


def test_c_nav_any_requirements_lists_warehouse():
    src = _src("permissions.js")
    assert "const NAV_ANY_REQUIREMENTS = {" in src
    assert "warehouse: ['use_warehouse', 'manage_inventory']," in src


def test_d_apply_nav_removes_link_without_any_permission():
    src = _src("permissions.js")
    assert "anyRequired.some((p) => this.can(p))" in src
    assert "el.remove();" in src


def test_e_both_files_carry_the_marker():
    assert MARK in _src("warehouse.js")
    assert MARK in _src("permissions.js")
