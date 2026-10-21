import asyncio
from sqlalchemy import select
from infrastructure.db.session import AsyncSessionLocal
from infrastructure.db.models import Role

WANTED = ["view_restock_list", "manage_restock_list", "view_purchase_invoices", "manage_products", "manage_inventory", "process_sale"]

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Role.name, Role.display_name, Role.permissions))
        for name, display_name, perms in result.all():
            perms = perms or []
            print(f"{name} ({display_name}): {len(perms)} permissions")
            for p in WANTED:
                print(f"    {p} = {p in perms}")

asyncio.run(main())
