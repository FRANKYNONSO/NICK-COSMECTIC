"""Creates the storefront service role and account (see brief v20).
Modes: default = apply, --dry-run = report only, --rollback = remove.
--rotate = replace the password of the existing account (new one from NICK_STOREFRONT_NEW_PASSWORD).
The password is read from NICK_STOREFRONT_PASSWORD and is never printed.
The email comes from NICK_STOREFRONT_EMAIL (default storefront@nickcosmetics.com).
DATABASE_URL must be set and must point at nickcosmetics or shelf_test.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "packages" / "core"))

ROLE_NAME = "storefront_service"
ROLE_DISPLAY = "Website Service"
ROLE_PERMS = ["process_sale"]
FULL_NAME = "Website Storefront"
DEFAULT_EMAIL = "storefront@nickcosmetics.com"
ALLOWED_DBS = ("nickcosmetics", "shelf_test")


def fail(msg):
    print(msg)
    sys.exit(1)


async def run(mode, email, password):
    from sqlalchemy import func, select
    from infrastructure.db.audit import log_audit_event
    from infrastructure.db.models import Role, User
    from infrastructure.db.session import AsyncSessionLocal, engine
    from infrastructure.db.user_deletion import deletion_blockers
    from infrastructure.security.passwords import hash_password, verify_password

    code = 0
    try:
        async with AsyncSessionLocal() as session:
            role = (await session.execute(select(Role).where(Role.name == ROLE_NAME))).scalar_one_or_none()
            user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            print("State: role exists = %s, account exists = %s" % (role is not None, user is not None))

            if mode == "rollback":
                if user is None or user.role != ROLE_NAME:
                    print("Nothing to roll back for the account.")
                else:
                    blockers = await deletion_blockers(session, user.id)
                    if blockers:
                        print("STOP: the account has history and cannot be deleted: " + ", ".join(blockers[:5]))
                        print("You can deactivate it instead. Nothing was changed.")
                        return 1
                    uid = user.id
                    await session.delete(user)
                    await session.flush()
                    log_audit_event(session, user_id=None, action="storefront_service_account_removed",
                                    entity_type="user", entity_id=uid, metadata={"email": email})
                if role is not None:
                    left = (await session.execute(
                        select(func.count()).select_from(User).where(User.role == ROLE_NAME))).scalar() or 0
                    if left == 0 and not role.is_system:
                        await session.delete(role)
                    else:
                        print("Role kept (still used or a system role).")
                await session.commit()
                print("Rollback done.")
                return 0

            if mode in ("rotate", "rotate_dry"):
                if user is None:
                    print("STOP: the account does not exist. Nothing changed.")
                    return 1
                if user.role != ROLE_NAME:
                    print("MISMATCH: this email belongs to an account with another role. Nothing changed.")
                    return 1
                if verify_password(password, user.password_hash):
                    print("STOP: the new password is the same as the stored one. Nothing changed.")
                    return 1
                if mode == "rotate_dry":
                    print("DRY RUN: would replace the password of %s." % email)
                    print("DRY RUN: nothing was written.")
                    return 0
                user.password_hash = hash_password(password)
                from datetime import datetime, timezone  # PATCH25B-PASSWORD-CHANGED-AT
                user.password_changed_at = datetime.now(timezone.utc)
                log_audit_event(session, user_id=None, action="storefront_service_account_password_rotated",
                                entity_type="user", entity_id=user.id, metadata={"email": email})
                await session.commit()
                print("Password replaced.")
                async with AsyncSessionLocal() as check:
                    u2 = (await check.execute(select(User).where(User.email == email))).scalar_one_or_none()
                    ok_new = u2 is not None and verify_password(password, u2.password_hash)
                    print("CHECK stored password matches the new variable: %s" % ok_new)
                return 0 if ok_new else 1

            if role is not None and (sorted(role.permissions or []) != sorted(ROLE_PERMS) or role.is_system):
                print("MISMATCH: role %s exists with different permissions. Nothing changed." % ROLE_NAME)
                return 1
            if user is not None and user.role != ROLE_NAME:
                print("MISMATCH: this email belongs to an account with another role. Nothing changed.")
                return 1
            if user is not None:
                print("Already applied. Stored password matches your variable: %s"
                      % verify_password(password, user.password_hash))
                return 0

            if mode == "dry":
                print("DRY RUN: would create role = %s, account %s (role %s, no branch)."
                      % (role is None, email, ROLE_NAME))
                print("DRY RUN: nothing was written.")
                return 0

            if role is None:
                role = Role(name=ROLE_NAME, display_name=ROLE_DISPLAY, permissions=list(ROLE_PERMS),
                            is_system=False, created_by=None)
                session.add(role)
                await session.flush()
            user = User(full_name=FULL_NAME, role=ROLE_NAME, email=email, phone=None, branch_id=None,
                        password_hash=hash_password(password), is_active=True)
            session.add(user)
            await session.flush()
            log_audit_event(session, user_id=None, action="storefront_service_account_created",
                            entity_type="user", entity_id=user.id,
                            metadata={"email": email, "role": ROLE_NAME})
            await session.commit()
            print("Created role and account.")

        async with AsyncSessionLocal() as session:
            r = (await session.execute(select(Role).where(Role.name == ROLE_NAME))).scalar_one_or_none()
            u = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            ok_role = r is not None and list(r.permissions) == ROLE_PERMS
            ok_user = u is not None and u.is_active and u.role == ROLE_NAME and u.branch_id is None
            ok_pw = u is not None and verify_password(password, u.password_hash)
            print("CHECK role has only process_sale: %s" % ok_role)
            print("CHECK account active, right role, no branch: %s" % ok_user)
            print("CHECK stored password matches your variable: %s" % ok_pw)
            code = 0 if (ok_role and ok_user and ok_pw) else 1
        return code
    finally:
        await engine.dispose()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--rotate", action="store_true")
    a = ap.parse_args()
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        fail("STOP: DATABASE_URL is not set.")
    dbname = url.split("/")[-1].split("?")[0]
    print("Target database: " + dbname)
    if dbname not in ALLOWED_DBS:
        fail("STOP: database must be one of %s." % (ALLOWED_DBS,))
    email = (os.environ.get("NICK_STOREFRONT_EMAIL") or DEFAULT_EMAIL).strip()
    password = os.environ.get("NICK_STOREFRONT_PASSWORD") or ""
    if a.rotate and a.rollback:
        fail("STOP: use --rotate or --rollback, not both.")
    if a.rotate:
        mode = "rotate_dry" if a.dry_run else "rotate"
        password = os.environ.get("NICK_STOREFRONT_NEW_PASSWORD") or ""
        if len(password) < 20:
            fail("STOP: NICK_STOREFRONT_NEW_PASSWORD is missing or shorter than 20 characters.")
        print("New password variable length: %d (value hidden)" % len(password))
    else:
        mode = "rollback" if a.rollback else ("dry" if a.dry_run else "apply")
        if mode != "rollback":
            if len(password) < 8:
                fail("STOP: NICK_STOREFRONT_PASSWORD is missing or shorter than 8 characters.")
            print("Password variable length: %d (value hidden)" % len(password))
    print("Account email: %s | mode: %s" % (email, mode))
    sys.exit(asyncio.run(run(mode, email, password)))


main()
