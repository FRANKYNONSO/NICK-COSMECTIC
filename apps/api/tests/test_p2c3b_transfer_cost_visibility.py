# P2c-3b text checks for the warehouse transfer cost (marker PATCHP2C3B-TRANSFER-COST).
# These tests read the source file only. They do not create a transfer.
from pathlib import Path

MARK = "PATCHP2C3B-TRANSFER-COST"
WH = Path(__file__).resolve().parents[1] / "api" / "v1" / "routers" / "warehouse.py"


def _text():
    return WH.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _slice(text, start_marker, end_marker):
    a = text.index(start_marker)
    b = text.index(end_marker, a + 1)
    return text[a:b]


def test_a_import_and_marker():
    t = _text()
    assert MARK in t
    assert "    can_view_costs,\n" in t.split("router = APIRouter")[0]


def test_b_create_route_hides_cost():
    t = _text()
    s = _slice(
        t,
        '"/transfers", response_model=StockTransferOut, status_code=status.HTTP_201_CREATED',
        '@router.get("/transfer-split-preview")',
    )
    assert 'Depends(require_permission("manage_inventory"))' in s
    assert "show_cost = await can_view_costs(current_user, session)" in s
    assert "if (show_cost and transfer.total_cost_kobo is not None) else None" in s


def test_c_preview_route_hides_cost():
    t = _text()
    s = _slice(t, '@router.get("/transfer-cost-preview")', '@router.get("/pushable-invoice-items"')
    assert 'current_user: User = Depends(require_permission("manage_inventory"))' in s
    assert '    _: User = Depends(require_permission("manage_inventory")),\n) -> dict:' not in s
    assert "show_cost = await can_view_costs(current_user, session)" in s
    assert s.count("if show_cost else None") == 2


def test_d_list_route_hides_cost():
    t = _text()
    s = _slice(t, '@router.get("/transfers", response_model=list[StockTransferOut])', '@router.post("/transfer-returns"')
    assert 'current_user: User = Depends(require_any_permission("manage_inventory", "use_warehouse"))' in s
    assert "    _: User = Depends(get_current_user)" not in s
    assert "show_cost = await can_view_costs(current_user, session)" in s
    assert "if (show_cost and t.total_cost_kobo is not None) else None" in s


def test_e_write_routes_keep_their_guards():
    t = _text()
    s = _slice(t, '@router.post("/pull-to-store"', '@router.post("/transfer-to-branch"')
    assert 'require_any_permission("manage_inventory", "restock_shelf")' in s
    assert "can_view_costs" not in s


def test_f_file_is_still_crlf():
    raw = WH.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert raw.count(b"\n") > 2000
