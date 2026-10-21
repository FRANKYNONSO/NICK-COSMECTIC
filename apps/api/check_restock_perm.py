import asyncio
from sqlalchemy import select
from infrastructure.db.session import AsyncSessionLocal
from infrastructure.db.models import Role

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Role.name, Role.display_name, Role.permissions))
        for name, display_name, perms in result.all():
            has_it = "view_restock_list" in (perms or [])
            print(f"{name} ({display_name}): has view_restock_list = {has_it}")

asyncio.run(main())
