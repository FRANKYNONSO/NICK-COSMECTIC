# PATCHP2A-RESTOCK-MANAGE
# Static guards (no database). They check the TEXT of the code, not live 403 answers.
from pathlib import Path

API = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parents[2] / "web_admin" / "js"
ROUTERS = API / "api" / "v1" / "routers"


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace")


def test_restock_list_writes_need_manage_inventory():
    text = _read(ROUTERS / "restock_list.py")
    assert '_MANAGE = require_permission("manage_inventory")' in text
    assert 'require_permission("manage_restock_list")' not in text
    assert '_VIEW = require_permission("view_restock_list")' in text


def test_customer_requests_writes_need_manage_inventory():
    text = _read(ROUTERS / "customer_requests.py")
    assert '_MANAGE = require_permission("manage_inventory")' in text
    assert 'require_permission("manage_restock_list")' not in text
    assert '_VIEW = require_permission("view_restock_list")' in text


def test_web_buttons_follow_manage_inventory():
    for name in ("restock-list.js", "customer-requests.js"):
        text = _read(WEB / name)
        assert "const canManage = Perms.can('manage_inventory');" in text
        assert "manage_restock_list" not in text
    text = _read(WEB / "inventory.js")
    assert "const canBulkAddRestock = Perms.can('manage_inventory');" in text
    assert "manage_restock_list" not in text
