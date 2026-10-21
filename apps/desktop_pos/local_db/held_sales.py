"""Hold/resume a cart mid-sale -- e.g. to serve another customer
without losing what's currently in the cart. Held sales are entirely
local and never synced; they represent in-progress carts, not
completed transactions.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from local_db.models import HeldSale, HeldSaleItem


def hold_sale(
    session: Session,
    branch_id: str,
    held_by: str,
    held_by_name: str,
    cart_lines: list,  # list[CartLine] from pos_window.py -- kept loosely typed to avoid a UI->local_db import
    sale_discount_naira: float,
    coupon_code: str | None,
    note: str | None = None,
) -> HeldSale:
    held = HeldSale(
        branch_id=branch_id,
        held_by=held_by,
        held_by_name=held_by_name,
        note=note,
        sale_discount_kobo=round(sale_discount_naira * 100),
        coupon_code=coupon_code,
    )
    session.add(held)
    session.flush()  # assigns held.id before the child rows reference it

    for line in cart_lines:
        session.add(
            HeldSaleItem(
                held_sale_id=held.id,
                variant_id=line.variant.id,
                quantity=line.quantity,
                discount_kobo=round(line.discount_naira * 100),
            )
        )

    session.commit()
    return held


def list_held_sales(session: Session, branch_id: str) -> list[HeldSale]:
    return session.execute(
        select(HeldSale)
        .where(HeldSale.branch_id == branch_id)
        .order_by(HeldSale.created_at.desc())
    ).scalars().all()


def get_held_sale_items(session: Session, held_sale_id: str) -> list[HeldSaleItem]:
    return session.execute(
        select(HeldSaleItem).where(HeldSaleItem.held_sale_id == held_sale_id)
    ).scalars().all()


def delete_held_sale(session: Session, held_sale_id: str) -> None:
    session.execute(
        HeldSaleItem.__table__.delete().where(HeldSaleItem.held_sale_id == held_sale_id)
    )
    session.execute(HeldSale.__table__.delete().where(HeldSale.id == held_sale_id))
    session.commit()
