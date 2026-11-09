"""P2e: the hidden_from_staff filter also applies to store_assistant.

TEXT CHECKS ONLY. These tests read the source files. They do not call the API.
"""
from pathlib import Path

ROUTERS = Path(__file__).resolve().parent.parent / "api" / "v1" / "routers"
NEW_IF = 'if current_user.role in ("cashier", "store_assistant"):  # PATCHP2E-HIDDEN-ROLES'


def _src(name):
    return (ROUTERS / name).read_text(encoding="utf-8")


def test_a_low_stock_report_filter_includes_store_assistant():
    src = _src("reports.py")
    assert NEW_IF + "\n        product_conditions.append(Product.hidden_from_staff.is_(False))" in src


def test_b_low_stock_report_old_staff_only_filter_is_gone():
    src = _src("reports.py")
    assert 'if current_user.role == "cashier":\n        product_conditions.append(Product.hidden_from_staff' not in src


def test_c_restock_list_filter_includes_store_assistant():
    src = _src("restock_list.py")
    assert NEW_IF + "\n        low_stock_conditions.append(Product.hidden_from_staff.is_(False))" in src


def test_d_restock_list_old_staff_only_filter_is_gone():
    src = _src("restock_list.py")
    assert 'if current_user.role == "cashier":\n        low_stock_conditions.append(Product.hidden_from_staff' not in src


def test_e_catalog_already_treats_store_assistant_like_staff():
    src = _src("catalog.py")
    assert 'current_user.role in ("cashier", "store_assistant")' in src
