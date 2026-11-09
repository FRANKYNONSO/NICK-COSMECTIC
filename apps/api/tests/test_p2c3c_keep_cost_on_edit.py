# P2c-3c text checks: a draft edit must not erase the cost (marker PATCHP2C3C-KEEP-COST).
# These tests read the source file only. They do not edit a receipt.
from pathlib import Path

MARK = "PATCHP2C3C-KEEP-COST"
GR = Path(__file__).resolve().parents[1] / "api" / "v1" / "routers" / "goods_receiving.py"


def _text():
    return GR.read_bytes().decode("utf-8")


def _apply_input_source():
    t = _text()
    a = t.index("async def _apply_input(")
    b = t.index("async def _late_line_check(", a)
    return t[a:b]


def test_a_marker_present():
    assert MARK in _text()


def test_b_old_costs_saved_before_lines_are_cleared():
    s = _apply_input_source()
    assert "old_costs = {}" in s
    assert "can_view_costs(current_user, session)" in s
    assert s.index("old_costs = {}") < s.index("can_view_costs(current_user, session)")
    assert s.index("can_view_costs(current_user, session)") < s.index("receipt.lines.clear()")


def test_c_old_cost_is_used_only_when_new_cost_is_missing():
    s = _apply_input_source()
    assert "l.unit_cost_kobo if l.unit_cost_kobo is not None else old_costs.get(l.product_id)" in s
    assert s.index("receipt.lines.clear()") < s.index("old_costs.get(l.product_id)")


def test_d_edit_and_create_keep_their_guard():
    t = _text()
    a = t.index('@router.put("/receipts/{receipt_id}")')
    b = t.index('@router.post("/receipts/{receipt_id}/post")', a)
    assert 'require_permission("manage_inventory")' in t[a:b]
    a2 = t.index('@router.post("/receipts", status_code=status.HTTP_201_CREATED)')
    b2 = t.index('@router.get("/receipts")', a2)
    assert 'require_permission("manage_inventory")' in t[a2:b2]
