"""PATCH10A: a unit can have its own size per product."""
from types import SimpleNamespace as N

from infrastructure.units import format_split, pack_units_for_product


def _unit(code, label, plural, fixed, uid, sort):
    return N(id=uid, code=code, label=label, plural_label=plural, fixed_pieces=fixed,
             include_in_breakdown=True, is_active=True, sort_order=sort)


CARTON = _unit("carton", "Carton", "Cartons", None, "u-carton", 10)
DOZEN = _unit("dozen", "Dozen", "Dozens", 12, "u-dozen", 40)
PACK_NO_DEFAULT = _unit("pack", "Pack", "Packs", None, "u-pack", 60)
PACK_DEFAULT_10 = _unit("pack", "Pack", "Packs", 10, "u-pack", 60)
BARE = N(default_pieces_per_carton=None, default_pieces_per_bag=None, default_pieces_per_roll=None)


def test_unit_without_default_or_override_is_skipped():
    packs = pack_units_for_product([DOZEN, PACK_NO_DEFAULT], BARE)
    assert [p.code for p in packs] == ["dozen"]


def test_product_size_gives_a_unit_without_default_a_size():
    packs = pack_units_for_product([DOZEN, PACK_NO_DEFAULT], BARE, {"u-pack": 6})
    assert format_split(20, packs) == "1 dozen + 1 pack + 2 pieces"


def test_product_size_beats_unit_default():
    packs = pack_units_for_product([DOZEN], BARE, {"u-dozen": 10})
    assert format_split(25, packs) == "2 dozens + 5 pieces"


def test_unit_default_used_when_product_has_no_size():
    packs = pack_units_for_product([PACK_DEFAULT_10], BARE, {})
    assert format_split(25, packs) == "2 packs + 5 pieces"
    packs = pack_units_for_product([PACK_DEFAULT_10], BARE, {"u-other": 3})
    assert format_split(25, packs) == "2 packs + 5 pieces"


def test_old_two_argument_call_still_works():
    packs = pack_units_for_product([DOZEN], BARE)
    assert format_split(30, packs) == "2 dozens + 6 pieces"


def test_carton_size_still_comes_from_the_product_not_the_override():
    fanta = N(default_pieces_per_carton=24, default_pieces_per_bag=None, default_pieces_per_roll=None)
    packs = pack_units_for_product([CARTON], fanta, {"u-carton": 5})
    assert format_split(100, packs) == "4 cartons + 4 pieces"
