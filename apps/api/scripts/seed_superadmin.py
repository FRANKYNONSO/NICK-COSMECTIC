"""One-off script to create the first Super Admin account.

Run inside the API container:
    docker compose -f infra/docker/docker-compose.dev.yml exec api python scripts/seed_superadmin.py

There's no public self-registration endpoint by design (per the roles
spec in docs/architecture Section 3) — the Super Admin account is the
seed from which every other user gets created via authenticated
"manage_users" calls, not a signup form.
"""

import asyncio
import getpass
import sys

sys.path.insert(0, ".")

from infrastructure.db.models import User  # noqa: E402
from infrastructure.db.repositories import UserRepository  # noqa: E402
from infrastructure.db.session import AsyncSessionLocal  # noqa: E402
from infrastructure.security.passwords import hash_password  # noqa: E402


async def main() -> None:
    print("Create the first Super Admin account.\n")
    full_name = input("Full name: ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match. Aborting.")
        return
    if len(password) < 8:
        print("Password should be at least 8 characters. Aborting.")
        return

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_email(email)
        if existing is not None:
            print(f"A user with email '{email}' already exists. Aborting.")
            return

        user = User(
            full_name=full_name,
            email=email,
            password_hash=hash_password(password),
            role="super_admin",
            is_active=True,
        )
        created = await repo.create(user)
        print(f"\nCreated Super Admin '{created.full_name}' <{created.email}> (id={created.id})")


if __name__ == "__main__":
    asyncio.run(main())
