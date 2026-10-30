"""Default price for a branch-to-branch transfer (XFER1)."""


def default_transfer_price(selling_kobo, wholesale_kobo, wholesale_min_quantity, quantity_pieces):
    """Returns (unit_price_kobo, basis). Same rule as POS: the wholesale price
    applies once the quantity reaches wholesale_min_quantity; both the
    wholesale price and the minimum must be set."""
    if (
        wholesale_kobo is not None
        and wholesale_min_quantity is not None
        and quantity_pieces >= wholesale_min_quantity
    ):
        return int(wholesale_kobo), "wholesale"
    return int(selling_kobo or 0), "selling"


async def branch_selling_price_kobo(session, branch_id, product):
    # The selling price at this branch: its own override when one is set
    # (branch_product_prices), otherwise the product's global price.
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT selling_price_kobo FROM branch_product_prices WHERE branch_id = :b AND product_id = :p"),
            {"b": branch_id, "p": product.id},
        )
    ).first()
    return int(row[0]) if row is not None else int(product.selling_price_kobo or 0)


# XFER2-SHELF-OR-WAREHOUSE: where a transfer takes its stock from and where it lands.
async def shelf_pieces(session, branch_id, product_id):
    # Pieces on the shelf (sellable stock) of this branch for the product.
    from sqlalchemy import text

    value = await session.scalar(
        text(
            "SELECT COALESCE(SUM(quantity_remaining), 0) FROM stock_batches "
            "WHERE branch_id = :b AND product_id = :p AND quantity_remaining > 0"
        ),
        {"b": branch_id, "p": product_id},
    )
    return int(value or 0)


async def warehouse_pieces(session, branch_id, product_id):
    # Pieces in this branch's own warehouse for the product.
    from sqlalchemy import text

    value = await session.scalar(
        text("SELECT quantity_pieces FROM warehouse_stock WHERE branch_id = :b AND product_id = :p"),
        {"b": branch_id, "p": product_id},
    )
    return int(value or 0)


async def branch_shelf_limit(session, branch_id, product_id):
    # The SAVED shelf default of this branch for the product, or None when none is saved.
    from sqlalchemy import text

    value = await session.scalar(
        text("SELECT max_shelf_quantity FROM product_shelf_settings WHERE branch_id = :b AND product_id = :p"),
        {"b": branch_id, "p": product_id},
    )
    return int(value) if value is not None else None


def plan_transfer_split(quantity, take_from, shelf_available, warehouse_available, dest_shelf_now, dest_shelf_limit):
    # Returns the split, or None when the quantity is not available (or not above 0).
    # Source: the chosen place first, the other one covers any shortfall.
    # Landing: the receiving shelf up to its saved default, the rest to its warehouse.
    if quantity <= 0 or quantity > shelf_available + warehouse_available:
        return None
    if take_from in ("warehouse", "both"):  # XFER3-AUTO-SOURCE: both = warehouse first, the shelf covers the rest
        from_warehouse = min(quantity, warehouse_available)
        from_shelf = quantity - from_warehouse
    else:
        from_shelf = min(quantity, shelf_available)
        from_warehouse = quantity - from_shelf
    if dest_shelf_limit is None:
        to_shelf = quantity
    else:
        to_shelf = min(quantity, max(dest_shelf_limit - dest_shelf_now, 0))
    return {
        "from_shelf": from_shelf,
        "from_warehouse": from_warehouse,
        "to_shelf": to_shelf,
        "to_warehouse": quantity - to_shelf,
    }


def _split_part(shelf, warehouse):
    parts = []
    if shelf:
        parts.append("shelf %d" % shelf)
    if warehouse:
        parts.append("warehouse %d" % warehouse)
    return " + ".join(parts) or "nothing"


def transfer_split_text(plan):
    return "Taken from %s. Arrives as %s." % (
        _split_part(plan["from_shelf"], plan["from_warehouse"]),
        _split_part(plan["to_shelf"], plan["to_warehouse"]),
    )
