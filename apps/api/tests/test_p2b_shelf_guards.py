"""P2b static tests (marker PATCHP2B-SHELF-GUARDS).

Text checks only. They read the router source and the shared matrix.
They do not call the API, so they do NOT prove a live 403 or 200.
"""
from pathlib import Path

from nickcosmetics_core.domain.users.user import ROLE_PERMISSION_MATRIX

ROUTERS = Path(__file__).resolve().parents[1] / "api" / "v1" / "routers"


def _window(file_name, start_text, size):
    text = (ROUTERS / file_name).read_text(encoding="utf-8")
    pos = text.index(start_text)
    return text[pos:pos + size]


def test_reduce_stock_accepts_manage_inventory_or_reduce_shelf_stock():
    win = _window("goods_receiving.py", "async def reduce_shelf_stock(", 600)
    assert 'require_any_permission("manage_inventory", "reduce_shelf_stock")' in win
    assert 'require_permission("manage_inventory")' not in win


def test_pull_to_store_accepts_manage_inventory_or_restock_shelf():
    win = _window("warehouse.py", "async def pull_to_store(", 800)
    assert 'require_any_permission("manage_inventory", "restock_shelf")' in win
    assert 'require_permission("manage_inventory")' not in win


def test_shelf_limit_save_route_is_unchanged_until_p6():
    win = _window("goods_receiving.py", "async def set_shelf_setting(", 400)
    assert 'require_permission("manage_inventory")' in win


def test_matrix_gives_restock_and_reduce_to_store_assistant_not_staff():
    for name in ("restock_shelf", "reduce_shelf_stock"):
        assert name in ROLE_PERMISSION_MATRIX["store_assistant"]
        assert name in ROLE_PERMISSION_MATRIX["branch_manager"]
        assert name in ROLE_PERMISSION_MATRIX["super_admin"]
        assert name not in ROLE_PERMISSION_MATRIX["cashier"]
