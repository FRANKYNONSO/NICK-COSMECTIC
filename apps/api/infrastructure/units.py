"""Packaging units (carton / bag / roll / dozen / ...): the one place that
turns a piece total into "4 cartons + 4 pieces" and back.

PATCH9A-UNITS. Stock is always stored in PIECES. Nothing here changes any
stock figure -- it only describes a piece total in the units a product
actually comes in.

How big is one unit of a product?
  * carton / bag / roll: per product -- Product.default_pieces_per_carton /
    _bag / _roll (set on the product form).
  * every other unit (dozen = 12, plus anything added later): one fixed
    number on the unit itself (PackagingUnit.fixed_pieces).
A unit with no known size for a product is simply skipped for that product.
"""
from __future__ import annotations

from dataclasses import dataclass

# unit code -> Product column that holds its size for that product
PER_PRODUCT_COLUMNS = {
    "carton": "default_pieces_per_carton",
    "bag": "default_pieces_per_bag",
    "roll": "default_pieces_per_roll",
}


@dataclass(frozen=True)
class PackUnit:
    code: str
    label: str
    plural_label: str
    pieces: int


def pack_units_for_product(units, product, overrides=None) -> list[PackUnit]:
    """Units usable for this product, biggest first.

    `units` is any iterable of objects with code, label, plural_label,
    fixed_pieces, include_in_breakdown, is_active, sort_order (the
    PackagingUnit rows). `product` may be None (then only fixed units).
    """
    out: list[tuple[int, int, PackUnit]] = []
    for u in units or []:
        if not getattr(u, "is_active", True) or not getattr(u, "include_in_breakdown", True):
            continue
        column = PER_PRODUCT_COLUMNS.get(u.code)
        if column:
            size = getattr(product, column, None) if product is not None else None
        else:
            # PATCH10A: this product's own size for the unit (Manage Units page)
            # wins; otherwise the unit's default number, if it has one.
            size = overrides.get(getattr(u, "id", None)) if overrides else None
            if not size:
                size = u.fixed_pieces
        if not size or size <= 1:
            continue
        label = u.label
        out.append(
            (
                -int(size),
                int(getattr(u, "sort_order", 0) or 0),
                PackUnit(u.code, label, u.plural_label or label + "s", int(size)),
            )
        )
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out]


def split_pieces(total_pieces: int, packs: list[PackUnit]) -> tuple[list[tuple[PackUnit, int]], int]:
    """Greedy split, biggest unit first: (100, carton=24) -> ([(carton, 4)], 4)."""
    left = max(int(total_pieces or 0), 0)
    parts: list[tuple[PackUnit, int]] = []
    for p in sorted(packs, key=lambda x: -x.pieces):
        n, left = divmod(left, p.pieces)
        if n:
            parts.append((p, n))
    return parts, left


def format_split(total_pieces: int, packs: list[PackUnit]) -> str:
    """"4 cartons + 4 pieces"; "100 pieces" when no unit applies."""
    parts, rest = split_pieces(total_pieces, packs)
    bits = [f"{n} {p.label.lower() if n == 1 else p.plural_label.lower()}" for p, n in parts]
    if rest or not bits:
        bits.append(f"{rest} piece" + ("" if rest == 1 else "s"))
    return " + ".join(bits)


def has_unit_split(total_pieces: int, packs: list[PackUnit]) -> bool:
    """True when at least one whole unit fits (so the text is more than 'N pieces')."""
    return bool(split_pieces(total_pieces, packs)[0])


def pieces_from_parts(parts: list[tuple[int, int]]) -> int:
    """[(count, pieces_per_unit), ...] -> total pieces. 2 cartons of 24 + 5 loose = [(2, 24), (5, 1)]."""
    return sum(int(c) * int(s) for c, s in parts)


async def load_units(session):
    """All packaging-unit rows, in display order (callers filter on is_active)."""
    from sqlalchemy import select

    from infrastructure.db.models import PackagingUnit

    result = await session.execute(
        select(PackagingUnit).order_by(PackagingUnit.sort_order, PackagingUnit.label)
    )
    return list(result.scalars().all())


async def load_unit_sizes(session, product_ids):
    """PATCH10A: per-product unit sizes, {product_id: {unit_id: pieces}}.

    Only the numbers typed on the Manage Units page. A product with no row for
    a unit falls back to the unit's own default number (if it has one).
    """
    from sqlalchemy import select

    from infrastructure.db.models import ProductUnitSize

    ids = [i for i in (product_ids or []) if i is not None]
    if not ids:
        return {}
    result = await session.execute(
        select(ProductUnitSize).where(ProductUnitSize.product_id.in_(ids))
    )
    out: dict = {}
    for row in result.scalars().all():
        out.setdefault(row.product_id, {})[row.unit_id] = int(row.pieces)
    return out
