"""
Thin async HTTP client over your existing cloud API (apps/api).
"""
import asyncio
import time

import httpx
from config import settings


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._service_token: str | None = None
        self._service_token_expires_at: float = 0

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def _get_service_token(self) -> str:
        """Logs in as the storefront's own staff account and caches the
        token in memory. Shoppers never see this token -- it's used
        server-to-server so the storefront can call authenticated
        endpoints (create customer, checkout, stock levels) on a guest's
        behalf. Refreshes a minute before real expiry to avoid
        edge-of-window 401s on slow requests.
        """
        if self._service_token and time.time() < self._service_token_expires_at:
            return self._service_token

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/auth/login",
                json={
                    "email": settings.STOREFRONT_SERVICE_EMAIL,
                    "password": settings.STOREFRONT_SERVICE_PASSWORD,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._service_token = data["access_token"]
        self._service_token_expires_at = time.time() + (14 * 60)
        return self._service_token

    async def _authed_post(self, path: str, json_body: dict, timeout: float = 10.0) -> dict:
        token = await self._get_service_token()
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}{path}",
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def _authed_get(self, path: str, params: dict | None = None) -> dict | list:
        token = await self._get_service_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()

    # ---- Catalog + stock -------------------------------------------------
    # The catalog API returns flat products (one row per sellable size), and
    # stock rows are keyed by product_id. The storefront never invents quantities.
    # PATCH18B-UNUSED-REMOVED

    @staticmethod
    def is_listed(product: dict) -> bool:
        """Only active products with a real price appear on the storefront
        (hides drafts and zero-price placeholders)."""
        try:
            price = float(product.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        return product.get("status") == "active" and price > 0

    @staticmethod
    def attach_stock(product: dict, stock_by_product: dict) -> dict:
        row = stock_by_product.get(str(product.get("id")), {})
        qty = int(row.get("quantity_available", 0) or 0)
        product["stock_qty"] = max(qty, 0)
        product["is_low_stock"] = qty > 0 and bool(row.get("is_low_stock", False))
        return product

    async def list_products(
        self, category: str | None = None, search: str | None = None, page: int = 1
    ) -> list:
        """Raw catalog products (no stock merged in)."""
        # The catalog API returns the full filtered list (it has no paging),
        # and filters by `category_id` and `search`. Paging is done in the
        # storefront router.
        params = {}
        if category:
            params["category_id"] = category
        if search:
            params["search"] = search
        data = await self._get("/api/v1/catalog/products", params=params)
        if isinstance(data, dict):  # tolerate a paginated {"items": [...]} shape
            data = data.get("items", [])
        return data

    async def get_combined_stock(self) -> list:
        """Website stock: every branch's sellable stock added together
        (minus open-booking reservations). Computed by the API, so the
        storefront never invents or caches quantities."""
        return await self._authed_get("/api/v1/storefront/stock")

    async def _stock_by_product(self) -> dict:
        rows = await self.get_combined_stock()
        return {str(r["product_id"]): r for r in rows}

    @staticmethod
    def unit_price_for(price, wholesale_price, wholesale_min, quantity) -> float:
        """Same rule as POS and the API: once the quantity reaches the
        product's wholesale minimum, the whole line is priced at the
        wholesale price. (The API recomputes this itself at order time.)"""
        price = float(price or 0)
        if wholesale_price is not None and wholesale_min is not None and quantity >= wholesale_min:
            return float(wholesale_price)
        return price

    async def create_storefront_order(self, payload: dict) -> dict:
        """One call places the whole order: the API finds/creates the
        customer, takes stock from whichever branches have it, applies the
        coupon and records the order -- all in one transaction."""
        return await self._authed_post("/api/v1/storefront/orders", payload, timeout=30.0)

    async def check_coupon(self, code: str) -> dict:
        """Asks the API whether a website coupon is usable right now.
        Does NOT redeem it (redemption happens inside create_storefront_order).
        Returns {"valid": bool, "discount_amount_naira": float, "message": str}."""
        return await self._authed_get("/api/v1/storefront/coupons/check", params={"code": code})

    async def list_products_with_stock(
        self, category: str | None = None, search: str | None = None, page: int = 1
    ) -> list:
        """Listed products with live stock merged in (stock_qty, is_low_stock)."""
        products = [
            p for p in await self.list_products(category=category, search=search, page=page)
            if self.is_listed(p)
        ]
        stock = await self._stock_by_product()
        return [self.attach_stock(p, stock) for p in products]

    async def get_product(self, product_id: str) -> dict:
        """One product with live stock merged in."""
        product = await self._get(f"/api/v1/catalog/products/{product_id}")
        stock = await self._stock_by_product()
        return self.attach_stock(product, stock)

    async def list_categories(self) -> list:
        return await self._get("/api/v1/catalog/categories")


api_client = ApiClient(settings.API_BASE_URL)