# P2d-1 text checks for the warehouse read guards (marker PATCHP2D-WAREHOUSE-GUARDS).
# These tests read the source file only. They do not call the routes.
from pathlib import Path

MARK = "PATCHP2D-WAREHOUSE-GUARDS"
GUARD = 'require_any_permission("manage_inventory", "use_warehouse")'
WH = Path(__file__).resolve().parents[1] / "api" / "v1" / "routers" / "warehouse.py"


def _text():
    return WH.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _sig(name, end):
    t = _text()
    a = t.index("async def " + name + "(")
    b = t.index(end, a)
    return t[a:b]


def test_a_marker_three_times():
    assert _text().count(MARK) == 3


def test_b_receipts_list_guard():
    s = _sig("list_waybill_complaints", ") -> list[IntakeReceiptOut]:")
    assert GUARD in s
    assert "get_current_user" not in s


def test_c_stock_list_guard():
    s = _sig("list_warehouse_stock", ") -> list[WarehouseStockOut]:")
    assert GUARD in s
    assert "get_current_user" not in s


def test_d_transfers_list_guard_and_cost_hiding():
    s = _sig("list_stock_transfers", ") -> list[StockTransferOut]:")
    assert GUARD in s
    assert "get_current_user" not in s
    t = _text()
    a = t.index("async def list_stock_transfers(")
    b = t.index('@router.post("/transfer-returns"', a)
    assert "show_cost = await can_view_costs(current_user, session)" in t[a:b]


def test_e_other_routes_keep_their_guards():
    t = _text()
    a = t.index('@router.post("/pull-to-store"')
    b = t.index('@router.post("/transfer-to-branch"', a)
    assert 'require_any_permission("manage_inventory", "restock_shelf")' in t[a:b]
    a = t.index('@router.post("/transfer-to-branch"')
    b = t.index('@router.get("/stock"', a)
    assert 'require_permission("manage_inventory")' in t[a:b]
    a = t.index('@router.get("/transfer-split-preview")')
    b = t.index('@router.get("/transfer-price-preview")', a)
    assert 'require_permission("manage_inventory")' in t[a:b]


def test_f_file_is_still_crlf():
    raw = WH.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert raw.count(b"\n") > 2000
