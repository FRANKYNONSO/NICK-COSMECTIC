import asyncio, uuid
from sqlalchemy import select, func
from infrastructure.db.session import AsyncSessionLocal
from infrastructure.db.models import CustomerRequest, StockBatch
from api.v1.routers.sales import _get_open_booking_holds

BRANCH = uuid.UUID("aac794fa-026c-463b-81e4-f7eff6cfb04c")

async def main():
    async with AsyncSessionLocal() as s:
        for r in (await s.execute(select(CustomerRequest))).scalars().all():
            if r.status in ("pending", "arrived"):
                print(r.customer_name, "|", r.item_name, "|", r.status, "| ordered", r.quantity, "| hold_pieces", r.hold_pieces)
                if r.product_id:
                    oh = await s.scalar(select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0))
                                        .where(StockBatch.product_id == r.product_id, StockBatch.branch_id == BRANCH))
                    h = (await _get_open_booking_holds(s, [r.product_id], BRANCH)).get(r.product_id, {}).get("reserved", 0)
                    print("    on hand", oh, "| held by server", h, "| sellable", int(oh) - h)
        await s.rollback()

asyncio.run(main())
