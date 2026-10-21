"""Pure helpers for storefront orders (no DB, no framework) so they can be
unit-tested on their own. Used by api/v1/routers/storefront_orders.py.
"""
import uuid


def allocate_across_branches(
    needed: int,
    available: dict[uuid.UUID, int],
    tie_break: dict[uuid.UUID, str],
) -> dict[uuid.UUID, int] | None:
    """Split `needed` units across branches, taking from the branch with the
    most stock first (so an order is split as few ways as possible, and a
    single branch fills it whenever any single branch can).

    Example: A=50, B=10, needed=55 -> {A: 50, B: 5}.
    Returns None if the branches together can't cover `needed`.
    """
    if needed <= 0:
        return {}
    ordered = sorted(
        ((bid, qty) for bid, qty in available.items() if qty > 0),
        key=lambda item: (-item[1], tie_break.get(item[0], ""), str(item[0])),
    )
    plan: dict[uuid.UUID, int] = {}
    remaining = needed
    for bid, qty in ordered:
        take = min(qty, remaining)
        plan[bid] = take
        remaining -= take
        if remaining == 0:
            return plan
    return None


def split_discount(total_kobo: int, weights_kobo: list[int]) -> list[int]:
    """Share a discount across sales in proportion to each sale's subtotal.
    Shares always add up to exactly `total_kobo` and never exceed a sale's
    own subtotal (as long as total_kobo <= sum(weights))."""
    weight_sum = sum(weights_kobo)
    if total_kobo <= 0 or weight_sum <= 0:
        return [0] * len(weights_kobo)
    total_kobo = min(total_kobo, weight_sum)
    shares = [total_kobo * w // weight_sum for w in weights_kobo]
    leftover = total_kobo - sum(shares)
    i = 0
    guard = 0
    while leftover > 0 and guard < len(shares) * (leftover + 1):
        if shares[i] < weights_kobo[i]:
            shares[i] += 1
            leftover -= 1
        i = (i + 1) % len(shares)
        guard += 1
    return shares
