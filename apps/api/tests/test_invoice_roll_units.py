"""Script test for Patch 5b: rolls count like cartons and bags on an invoice line.
Pure function tests, no database writes.
"""
from api.v1.routers.purchase_invoices import _compute_line_piece_calc, _unit_pieces


def test_a_rolls_count_times_pack():
    assert _unit_pieces("rolls", 3, 50) == 150
    print("PASS a: 3 rolls at 50 pieces = 150")


def test_b_rolls_without_pack_gives_nothing():
    assert _unit_pieces("rolls", 3, None) is None
    assert _unit_pieces("rolls", 3, 0) is None
    print("PASS b: rolls without a pack number give no total")


def test_c_line_calc_with_rolls():
    assert _compute_line_piece_calc(2, "rolls", 30, None, None) == 60
    print("PASS c: line calculation, 2 rolls at 30 = 60")


def test_d_other_units_unchanged():
    assert _unit_pieces("cartons", 2, 48) == 96
    assert _unit_pieces("bags", 4, 24) == 96
    assert _unit_pieces("dozens", 2, None) == 24
    assert _unit_pieces("pieces", 5, None) == 5
    print("PASS d: cartons, bags, dozens and pieces count as before")


def test_e_half_carton_with_rolls_adds_half_a_pack():
    assert _compute_line_piece_calc(2, "rolls", 30, None, "half_carton") == 75
    print("PASS e: half carton on top of rolls uses the same pack number")
