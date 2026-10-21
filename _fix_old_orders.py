# -*- coding: ascii -*-
# One-time data fix for two old customer orders (ERIC / BED SHEET and promise / laptop 40).
# Database: nickcosmetics only.
# Usage (project root): .\.venv\Scripts\python.exe _fix_old_orders.py [--dry-run | --rollback]
import sys
import os
import json
import glob
import asyncio
import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = "postgresql+asyncpg://nickcosmetics:nickcosmetics@localhost:5432/nickcosmetics"
ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "oldorders"

ERIC = "0a4cf074-cb8c-4aa2-93bb-46c3b34d3ae2"
LAP = "f093f799-87e7-4f1b-bd35-10dae3011da3"
P_ERIC = "95ebfc33-138d-4a73-80be-0c69465195d0"
P_LAP = "dd1b5887-c7f7-44bd-b92d-3697c20d0805"
ERIC_PRICE_KOBO = 250000
LAP_PRICE_KOBO = 500000

U = "CAST(CAST(:id AS text) AS uuid)"


async def read_state(c):
    st = {}
    for key, oid in (("eric", ERIC), ("lap", LAP)):
        r = await c.execute(
            text(
                "select status, arrived_at, hold_pieces, order_pieces, customer_pieces, "
                "deposit_method, deposit_kobo, quantity, unit_label, booking_id, product_id "
                "from customer_requests where id = " + U
            ),
            {"id": oid},
        )
        row = r.mappings().first()
        st[key] = dict(row) if row else None
    for key, pid in (("p_eric", P_ERIC), ("p_lap", P_LAP)):
        r = await c.execute(
            text("select selling_price_kobo from products where id = " + U), {"id": pid}
        )
        row = r.mappings().first()
        st[key] = dict(row) if row else None
    return st


async def branch_price_problem(c):
    r = await c.execute(
        text(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' and table_name ilike '%branch%price%'"
        )
    )
    names = [x[0] for x in r.fetchall()]
    if len(names) != 1:
        return "cannot find exactly one branch price table, found: " + str(names)
    r = await c.execute(
        text(
            "select count(*) from " + names[0]
            + " where product_id in (CAST(CAST(:a AS text) AS uuid), CAST(CAST(:b AS text) AS uuid))"
        ),
        {"a": P_ERIC, "b": P_LAP},
    )
    n = r.scalar()
    if n:
        return "a branch price override exists for these products in " + names[0] + " (" + str(n) + " rows)"
    return None


def problems(st):
    out = []
    for key, expect_dep, expect_qty, pid in (
        ("eric", 200000, 5, P_ERIC),
        ("lap", 400000, 4, P_LAP),
    ):
        o = st[key]
        if o is None:
            out.append(key + ": order not found")
            continue
        if o["status"] != "arrived":
            out.append(key + ": status is " + str(o["status"]) + ", expected arrived")
        if o["booking_id"] is not None:
            out.append(key + ": already has a booking")
        if o["customer_pieces"] is not None:
            out.append(key + ": customer_pieces already set")
        if int(o["deposit_kobo"] or 0) != expect_dep:
            out.append(key + ": deposit is " + str(o["deposit_kobo"]) + ", expected " + str(expect_dep))
        if int(o["quantity"] or 0) != expect_qty:
            out.append(key + ": quantity is " + str(o["quantity"]) + ", expected " + str(expect_qty))
        if str(o["product_id"]) != pid:
            out.append(key + ": product is not the expected one")
    for key in ("p_eric", "p_lap"):
        p = st[key]
        if p is None:
            out.append(key + ": product not found")
        elif int(p["selling_price_kobo"] or 0) != 0:
            out.append(key + ": selling price is " + str(p["selling_price_kobo"]) + ", expected 0")
    return out


async def do_apply(dry):
    e = create_async_engine(DSN)
    try:
        async with e.connect() as c:
            st = await read_state(c)
            await c.rollback()
            if st["eric"] and st["eric"]["customer_pieces"] == 5 and st["eric"]["deposit_method"] == "cash":
                print("Already applied")
                return 0
            probs = problems(st)
            bp = None
            async with e.connect() as c2:
                bp = await branch_price_problem(c2)
            if bp:
                probs.append(bp)
        if probs:
            print("STOP: nothing was written. Reasons:")
            for p in probs:
                print("  - " + p)
            return 1
        print("Checks OK. Current state:")
        print(json.dumps(st, indent=2, default=str))
        if dry:
            print("DRY RUN OK. Nothing was written.")
            return 0

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bdir = os.path.join(ROOT, "_backup_before_" + NAME + "_" + stamp)
        os.makedirs(bdir)
        with open(os.path.join(bdir, "snapshot.json"), "w") as fh:
            json.dump(st, fh, indent=2, default=str)
        print("Snapshot saved: " + bdir)

        async with e.begin() as c:
            r1 = await c.execute(
                text(
                    "update customer_requests set customer_pieces = 5, order_pieces = 5, "
                    "deposit_method = 'cash', hold_pieces = 0, updated_at = now() "
                    "where id = " + U + " and status = 'arrived' and booking_id is null"
                ),
                {"id": ERIC},
            )
            r2 = await c.execute(
                text(
                    "update customer_requests set status = 'pending', arrived_at = NULL, "
                    "hold_pieces = 0, order_pieces = 4, customer_pieces = 4, "
                    "deposit_method = 'online', updated_at = now() "
                    "where id = " + U + " and status = 'arrived' and booking_id is null"
                ),
                {"id": LAP},
            )
            r3 = await c.execute(
                text(
                    "update products set selling_price_kobo = " + str(ERIC_PRICE_KOBO)
                    + " where id = " + U + " and selling_price_kobo = 0"
                ),
                {"id": P_ERIC},
            )
            r4 = await c.execute(
                text(
                    "update products set selling_price_kobo = " + str(LAP_PRICE_KOBO)
                    + " where id = " + U + " and selling_price_kobo = 0"
                ),
                {"id": P_LAP},
            )
            counts = [r1.rowcount, r2.rowcount, r3.rowcount, r4.rowcount]
            print("Rows changed: " + str(counts))
            if counts != [1, 1, 1, 1]:
                raise RuntimeError("unexpected row counts, rolling back")
            after = await read_state(c)
            print("State after change:")
            print(json.dumps(after, indent=2, default=str))
            ok = (
                after["eric"]["customer_pieces"] == 5
                and after["eric"]["deposit_method"] == "cash"
                and after["lap"]["status"] == "pending"
                and after["lap"]["customer_pieces"] == 4
                and int(after["p_eric"]["selling_price_kobo"]) == ERIC_PRICE_KOBO
                and int(after["p_lap"]["selling_price_kobo"]) == LAP_PRICE_KOBO
            )
            if not ok:
                raise RuntimeError("verification failed, rolling back")
        print("FIX APPLIED")
        return 0
    except Exception as exc:
        print("FAILED: " + str(exc) + " (nothing was saved by this step)")
        return 1
    finally:
        await e.dispose()


async def do_rollback():
    dirs = sorted(glob.glob(os.path.join(ROOT, "_backup_before_" + NAME + "_*")))
    if not dirs:
        print("No snapshot folder found. Nothing to roll back.")
        return 1
    newest = dirs[-1]
    with open(os.path.join(newest, "snapshot.json")) as fh:
        st = json.load(fh)
    print("Rolling back from: " + newest)
    e = create_async_engine(DSN)
    try:
        async with e.begin() as c:
            for key, oid in (("eric", ERIC), ("lap", LAP)):
                o = st[key]
                await c.execute(
                    text(
                        "update customer_requests set "
                        "status = CAST(:status AS text), "
                        "arrived_at = CAST(CAST(:arrived_at AS text) AS timestamptz), "
                        "hold_pieces = CAST(:hold AS integer), "
                        "order_pieces = CAST(:op AS integer), "
                        "customer_pieces = CAST(:cp AS integer), "
                        "deposit_method = CAST(:dm AS text), "
                        "updated_at = now() where id = " + U
                    ),
                    {
                        "id": oid,
                        "status": o["status"],
                        "arrived_at": o["arrived_at"],
                        "hold": o["hold_pieces"],
                        "op": o["order_pieces"],
                        "cp": o["customer_pieces"],
                        "dm": o["deposit_method"],
                    },
                )
            for key, pid in (("p_eric", P_ERIC), ("p_lap", P_LAP)):
                await c.execute(
                    text("update products set selling_price_kobo = CAST(:p AS integer) where id = " + U),
                    {"id": pid, "p": st[key]["selling_price_kobo"]},
                )
        print("ROLLBACK DONE (note: a booking already made from ERIC's order is NOT undone)")
        return 0
    except Exception as exc:
        print("ROLLBACK FAILED: " + str(exc))
        return 1
    finally:
        await e.dispose()


def main():
    args = sys.argv[1:]
    if "--rollback" in args:
        return asyncio.run(do_rollback())
    return asyncio.run(do_apply("--dry-run" in args))


if __name__ == "__main__":
    sys.exit(main())
