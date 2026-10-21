import asyncio
from sqlalchemy import select
from infrastructure.db.session import AsyncSessionLocal
from infrastructure.db.models import Role

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Role))
        roles = result.scalars().all()
        for role in roles:
            perms = list(role.permissions or [])
            changed = False
            for p in ("view_restock_list", "manage_restock_list"):
                if p not in perms:
                    perms.append(p)
                    changed = True
            if changed:
                role.permissions = perms
                print(f"Updated {role.name}: {perms}")
        await session.commit()

asyncio.run(main())
