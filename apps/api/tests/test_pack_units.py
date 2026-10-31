"""PATCH9A-UNITS: pure tests for infrastructure/units.py (no database)."""
from types import SimpleNamespace as N

from infrastructure.units import (
    format_split,
    has_unit_split,
    pack_units_for_product,
    pieces_from_parts,
    split_pieces,
)


def _unit(code, label, plural, fixed=None, breakdown=True, active=True, order=0):
    return N(
        code=code, label=label, plural_label=plural, fixed_pieces=fixed,
        include_in_breakdown=breakdown, is_active=active, sort_order=order,
    )


UNITS = [
    _unit("carton", "Carton", "Cartons", None, order=10),
    _unit("bag", "Bag", "Bags", None, order=20),
    _unit("roll", "Roll", "Rolls", None, order=30),
    _unit("dozen", "Dozen", "Dozens", 12, order=40),
    _unit("piece", "Piece", "Pieces", 1, breakdown=False, order=50),
]


def _fanta():
    return N(default_pieces_per_carton=24, default_pieces_per_bag=None, default_pieces_per_roll=None)


def test_a_fanta_100_is_4_cartons_4_pieces():
    packs = pack_units_for_product(UNITS, _fanta())
    assert format_split(100, packs) == "4 cartons + 4 pieces"


def test_b_sale_of_30_is_1_carton_6_pieces():
    packs = pack_units_for_product(UNITS, _fanta())
    assert format_split(30, packs) == "1 carton + 6 pieces"


def test_c_two_cartons_is_48_pieces():
    assert pieces_from_parts([(2, 24)]) == 48
    assert pieces_from_parts([(2, 24), (5, 1)]) == 53


def test_d_dozen_is_always_12_and_sorts_after_carton():
    packs = pack_units_for_product(UNITS, _fanta())
    assert [(p.code, p.pieces) for p in packs] == [("carton", 24), ("dozen", 12)]
    assert format_split(40, packs) == "1 carton + 1 dozen + 4 pieces"


def test_e_product_without_pack_sizes_only_gets_dozens():
    bare = N(default_pieces_per_carton=None, default_pieces_per_bag=None, default_pieces_per_roll=None)
    packs = pack_units_for_product(UNITS, bare)
    assert [p.code for p in packs] == ["dozen"]
    assert format_split(30, packs) == "2 dozens + 6 pieces"
    assert format_split(5, packs) == "5 pieces"


def test_f_no_product_means_fixed_units_only():
    assert [p.code for p in pack_units_for_product(UNITS, None)] == ["dozen"]


def test_g_switched_off_or_hidden_units_are_skipped():
    units = [
        _unit("carton", "Carton", "Cartons", None, active=False),
        _unit("dozen", "Dozen", "Dozens", 12, breakdown=False),
    ]
    assert pack_units_for_product(units, _fanta()) == []
    assert format_split(100, []) == "100 pieces"


def test_h_a_unit_added_later_works_with_no_code_change():
    units = UNITS + [_unit("pack", "Pack", "Packs", 6, order=60)]
    bare = N(default_pieces_per_carton=None, default_pieces_per_bag=None, default_pieces_per_roll=None)
    packs = pack_units_for_product(units, bare)
    assert format_split(20, packs) == "1 dozen + 1 pack + 2 pieces"


def test_i_edge_cases():
    packs = pack_units_for_product(UNITS, _fanta())
    assert format_split(0, packs) == "0 pieces"
    assert format_split(1, packs) == "1 piece"
    assert format_split(24, packs) == "1 carton"
    assert format_split(-5, packs) == "0 pieces"
    assert split_pieces(100, packs) == ([(packs[0], 4)], 4)
    assert has_unit_split(30, packs) and not has_unit_split(11, packs)


def test_j_breakdown_always_adds_back_to_the_piece_total():
    packs = pack_units_for_product(UNITS, N(default_pieces_per_carton=24, default_pieces_per_bag=10, default_pieces_per_roll=7))
    for total in range(0, 500):
        parts, rest = split_pieces(total, packs)
        assert sum(n * p.pieces for p, n in parts) + rest == total
