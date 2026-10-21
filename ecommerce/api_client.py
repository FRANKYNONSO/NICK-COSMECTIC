"""
Thin async HTTP client over your existing cloud API (apps/api).

⚠️ ASSUMPTIONS TO VERIFY AGAINST YOUR REAL apps/api/api/v1/routers/ :
   - Catalog list:    GET  /api/v1/catalog/products
   - Catalog detail:  GET  /api/v1/catalog/products/{product_id}
   - Stock levels:    GET  /api/v1/inventory/stock-levels?product_id=...
   These paths are my best guess from your project map's router names
   (catalog, inventory). If your real routes differ, this is the ONLY
   file that needs path changes — everything else (routers/templates)
   calls through these functions, not raw URLs.
"""
import httpx
from config import settings


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def list_products(
        self, category: str | None = None, search: str | None = None, page: int = 1
    ) -> dict:
        """Returns paginated products with variants + aggregated stock."""
        params = {"page": page}
        if category:
            params["category"] = category
        if search:
            params["search"] = search
        return await self._get("/api/v1/catalog/products", params=params)

    async def get_product(self, product_id: int) -> dict:
        """Returns a single product with its variants and stock."""
        return await self._get(f"/api/v1/catalog/products/{product_id}")

    async def list_categories(self) -> list:
        return await self._get("/api/v1/catalog/categories")

    async def get_stock_level(self, product_variant_id: int, branch_id: int | None = None) -> dict:
        """
        Live stock for a variant. If branch_id is None, this should return
        stock aggregated across all branches (used for "online = all stock
        combined" mode). If ONLINE_BRANCH_ID is set in config, pass it here
        to scope to one branch's stock only.
        """
        params = {"product_variant_id": product_variant_id}
        if branch_id:
            params["branch_id"] = branch_id
        return await self._get("/api/v1/inventory/stock-levels", params=params)


api_client = ApiClient(settings.API_BASE_URL)
