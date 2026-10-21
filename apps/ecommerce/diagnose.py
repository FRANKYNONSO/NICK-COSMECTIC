"""
Read-only storefront diagnostic. Run from the ecommerce folder:
    python diagnose.py
Changes nothing. Never prints passwords or tokens.
"""
import importlib
import sys

print("Python", sys.version.split()[0])
print("\n[1] Packages")
missing = []
for mod, pip_name in [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("httpx", "httpx"),
                      ("jinja2", "jinja2"), ("itsdangerous", "itsdangerous"),
                      ("multipart", "python-multipart")]:
    try:
        importlib.import_module(mod)
        print("  ok      ", pip_name)
    except Exception as e:
        missing.append(pip_name)
        print("  MISSING ", pip_name, "-", type(e).__name__)
if missing:
    print("  -> pip install", " ".join(missing))

import httpx
from config import settings

print("\n[2] Config")
print("  API_BASE_URL            :", settings.API_BASE_URL)
print("  STOREFRONT_BRANCH_ID    :", settings.STOREFRONT_BRANCH_ID)
print("  service email           :", settings.STOREFRONT_SERVICE_EMAIL)
if settings.STOREFRONT_SERVICE_PASSWORD == "ChangeThisSecurePassword123!":
    print("  WARNING: service password is still the placeholder default")
if settings.SESSION_SECRET == "dev-only-change-me":
    print("  note: session secret is the dev default (fine locally, not in production)")

base = settings.API_BASE_URL.rstrip("/")


def shape(data):
    if isinstance(data, list):
        first = data[0] if data else None
        keys = sorted(first.keys()) if isinstance(first, dict) else type(first).__name__
        return f"list of {len(data)}; first item keys: {keys}"
    if isinstance(data, dict):
        return f"dict with keys: {sorted(data.keys())}"
    return type(data).__name__


def get(path, token=None, params=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.get(base + path, headers=headers, params=params, timeout=10)
    return r


print("\n[3] Public catalog endpoints")
products = []
try:
    r = get("/api/v1/catalog/products", params={"page": 1})
    print("  products   :", r.status_code)
    if r.status_code == 200:
        data = r.json()
        print("   ", shape(data))
        if isinstance(data, dict):
            print("  !! api_client.list_products_with_stock expects a LIST. A dict here")
            print("     (e.g. {items,total}) makes it iterate keys and crash with TypeError.")
            products = data.get("items", [])
        else:
            products = data
    r = get("/api/v1/catalog/categories")
    print("  categories :", r.status_code, shape(r.json()) if r.status_code == 200 else r.text[:150])
except Exception as e:
    print("  FAILED to reach API:", type(e).__name__, e)
    print("  -> is apps/api running at", base, "?")
    sys.exit(1)

variant_ids = []
if products:
    pid = products[0]["id"]
    r = get(f"/api/v1/catalog/products/{pid}/variants")
    print("  variants   :", r.status_code, shape(r.json()) if r.status_code == 200 else r.text[:150])
    if r.status_code == 200:
        variant_ids = [v.get("id") for v in r.json()]

print("\n[4] Service login + stock levels")
try:
    r = httpx.post(base + "/api/v1/auth/login",
                   json={"email": settings.STOREFRONT_SERVICE_EMAIL,
                         "password": settings.STOREFRONT_SERVICE_PASSWORD}, timeout=10)
    print("  login      :", r.status_code)
    if r.status_code != 200:
        print("  !! Storefront service login fails. Every page using stock data then raises,")
        print("     home shows 'No products yet' and /shop shows the 502 error page.")
        print("     Response:", r.text[:200])
        sys.exit(1)
    token = r.json().get("access_token")
    r = get("/api/v1/inventory/stock-levels", token=token,
            params={"branch_id": settings.STOREFRONT_BRANCH_ID})
    print("  stock-levels:", r.status_code)
    if r.status_code == 200:
        rows = r.json()
        print("   ", shape(rows))
        needed = {"variant_id", "quantity_available", "is_low_stock"}
        if isinstance(rows, list) and rows:
            have = set(rows[0].keys())
            print("    missing expected keys:", sorted(needed - have) or "none")
            ids = {row.get("variant_id") for row in rows}
            overlap = [v for v in variant_ids if v in ids]
            print(f"    first product's variants found in stock rows: {len(overlap)}/{len(variant_ids)}")
        elif isinstance(rows, list):
            print("  note: zero stock rows for this branch id -> everything shows Out of stock.")
    else:
        print("   ", r.text[:200])
except Exception as e:
    print("  FAILED:", type(e).__name__, e)

print("\nDone.")
