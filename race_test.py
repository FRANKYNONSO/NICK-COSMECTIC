"""Overselling race test for Nick Cosmetics.

What it does:
  CHECK A (overselling): fires several checkouts for the SAME product at
  the SAME moment, when that product has exactly 1 unit in stock. A
  correct system sells that 1 unit once and refuses the rest. The old
  code could sell it to more than one buyer.
  CHECK B (stock intake): then tries to add 5 units of that product
  straight through the API, the way someone bypassing the screens would.
  A correct system refuses, because the product already has stock on
  record and more must come in through the Warehouse.

Uses only the Python standard library -- nothing to install.

HOW TO USE (PowerShell, from C:\\Dev\\nick-cosmetics-python-platform):
  1. In the web admin, add a throwaway product (e.g. "RACE TEST", price 1)
     with starting stock of exactly 1.
  2. Run:  .\\.venv\\Scripts\\python.exe race_test.py
  3. Enter your login, then the product NAME (e.g. RACE TEST). The
     script finds the product and branch ids itself. Your password is
     hidden as you type.

Note: a successful run leaves ONE real sale of the test product in
your Transactions list (website channel, NGN 1). That is expected.
"""
import getpass
import json
import sys
import threading
import urllib.error
import urllib.request
import uuid

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"


def call(method, url, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw


def stock_available(base, token, branch_id, product_id):
    status, data = call("GET", f"{base}/inventory/stock-levels?branch_id={branch_id}", token)
    if status != 200 or not isinstance(data, list):
        return None
    for row in data:
        if row.get("product_id") == product_id:
            return row.get("quantity_available")
    return 0  # product not listed = no sellable stock


def as_list(data):
    """The API may return a bare list or {"items": [...]} -- accept both."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def pick_branch(base, token):
    status, data = call("GET", f"{base}/branches", token)
    branches = [b for b in as_list(data) if isinstance(b, dict) and b.get("id")]
    if len(branches) == 1:
        b = branches[0]
        print(f"Using branch: {b.get('name', b['id'])}")
        return b["id"]
    if branches:
        for i, b in enumerate(branches, 1):
            print(f"  {i}. {b.get('name', b['id'])}")
        return branches[int(input("Pick a branch number: ")) - 1]["id"]
    return input("Couldn't list branches -- paste the branch id: ").strip()


def pick_product(base, token, branch_id):
    name = input("Test product NAME (e.g. RACE TEST): ").strip()
    from urllib.parse import quote
    status, data = call("GET", f"{base}/catalog/products?search={quote(name)}&branch_id={branch_id}", token)
    products = [p for p in as_list(data) if isinstance(p, dict) and p.get("id")]
    if len(products) == 1:
        print(f"Using product: {products[0].get('name', products[0]['id'])}")
        return products[0]["id"]
    if products:
        for i, p in enumerate(products, 1):
            print(f"  {i}. {p.get('name', p['id'])}")
        return products[int(input("Pick a product number: ")) - 1]["id"]
    return input("Couldn't find it -- paste the product id: ").strip()


def main():
    base = input(f"API base URL [{DEFAULT_BASE}]: ").strip() or DEFAULT_BASE
    email = input("Your login email: ").strip()
    password = getpass.getpass("Your password: ")
    n = int(input("How many simultaneous buyers [8]: ").strip() or "8")

    status, data = call("POST", f"{base}/auth/login", body={"email": email, "password": password})
    if status != 200 or not isinstance(data, dict) or "access_token" not in data:
        sys.exit(f"Login failed ({status}): {data}")
    token = data["access_token"]

    branch_id = pick_branch(base, token)
    product_id = pick_product(base, token, branch_id)

    before = stock_available(base, token, branch_id, product_id)
    print(f"\nStock before: {before}")
    if before != 1:
        sys.exit(
            "This test needs the product to have EXACTLY 1 unit in stock "
            f"(it has {before}). Fix that and run again."
        )

    run_id = uuid.uuid4().hex[:8]
    barrier = threading.Barrier(n)  # everyone fires at the same instant
    results = [None] * n

    def buyer(i):
        body = {
            "branch_id": branch_id,
            "channel": "website",  # no shift needed for this channel
            "local_ref_id": f"race-{run_id}-{i}",
            "payment_method": "cash",
            "items": [{"product_id": product_id, "quantity": 1, "unit_price_naira": 1}],
        }
        barrier.wait()
        results[i] = call("POST", f"{base}/sales/checkout", token, body)

    threads = [threading.Thread(target=buyer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sold = [r for r in results if r and r[0] == 201]
    refused = [r for r in results if r and r[0] in (400, 409)]
    other = [r for r in results if r and r[0] not in (201, 400, 409)]

    after = stock_available(base, token, branch_id, product_id)
    print(f"Buyers: {n}")
    print(f"  sale succeeded : {len(sold)}")
    print(f"  refused (no stock): {len(refused)}")
    if other:
        print(f"  OTHER responses : {len(other)}  -> first one: {other[0]}")
    print(f"Stock after: {after}")

    print()
    if len(sold) == 1 and not other:
        print("PASS: the single unit was sold exactly once.")
    elif len(sold) > 1:
        print(f"FAIL: the last unit was sold {len(sold)} times (overselling).")
    else:
        print("Inconclusive -- check the 'OTHER responses' line above and send it to me.")

    print("\nCHECK B: trying to add 5 units of this product directly (should be refused)...")
    status, data = call(
        "POST",
        f"{base}/inventory/stock-batches",
        token,
        {
            "product_id": product_id,
            "branch_id": branch_id,
            "quantity_received": 5,
            "unit_cost_naira": 0,
            "batch_number": "race-test-should-be-refused",
        },
    )
    if status in (403, 409):
        print(f"PASS: refused with {status}: {data.get('detail') if isinstance(data, dict) else data}")
    elif status == 201:
        print("FAIL: the API accepted it -- 5 units were added directly. (Only the test product is affected.)")
    else:
        print(f"Inconclusive: got {status}: {data}")


if __name__ == "__main__":
    main()
