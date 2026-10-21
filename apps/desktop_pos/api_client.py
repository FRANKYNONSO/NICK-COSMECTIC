"""Thin synchronous HTTP client wrapping the Nick Cosmetics cloud API.

Kept deliberately simple for this first (online-mode) version of the
desktop app -- calls block the calling thread. That's fine for now
since PySide6's event loop still processes paint events between
calls on a local network, but it's the first thing to move onto a
QThread/worker once the app needs to feel snappier or once offline
mode means calls can time out.
"""

import uuid

import httpx

from config import API_BASE_URL


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")


class ApiClient:
    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.current_user: dict | None = None
        self._client = httpx.Client(timeout=10.0)

    # ------------------------------------------------------------ core

    def _headers(self) -> dict:
        if not self.access_token:
            return {}
        return {"Authorization": f"Bearer {self.access_token}"}

    def _try_refresh(self) -> bool:
        """Attempts to exchange the refresh token for a new access
        token. Returns True on success. Called automatically by
        _request() whenever a call comes back 401 -- access tokens are
        short-lived (15 min) by design, so this needs to just work
        silently rather than forcing a re-login every time one expires
        mid-session, which would otherwise be the common case for
        anyone who's been offline a while.
        """
        if not self.refresh_token:
            return False
        try:
            response = self._client.post(
                f"{self.base_url}/auth/refresh", json={"refresh_token": self.refresh_token}
            )
        except Exception:
            return False
        if response.status_code >= 400:
            return False
        data = response.json()
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        return True

    def _request(self, method: str, path: str, _retried: bool = False, **kwargs) -> dict | list:
        url = f"{self.base_url}{path}"
        response = self._client.request(method, url, headers=self._headers(), **kwargs)

        if response.status_code == 401 and path != "/auth/refresh" and not _retried:
            if self._try_refresh():
                return self._request(method, path, _retried=True, **kwargs)

        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise ApiError(response.status_code, str(detail))
        if response.status_code == 204:
            return {}
        return response.json()

    def get(self, path: str, params: dict | None = None) -> dict | list:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict | None = None) -> dict | list:
        return self._request("POST", path, json=json)

    # ------------------------------------------------------------ auth

    def login(self, email: str, password: str) -> dict:
        result = self.post("/auth/login", json={"email": email, "password": password})
        self.access_token = result["access_token"]
        self.refresh_token = result["refresh_token"]
        self.current_user = self.get("/auth/me")
        return self.current_user

    def logout(self) -> None:
        self.access_token = None
        self.refresh_token = None
        self.current_user = None

    # --------------------------------------------------------- catalog

    def search_products(self, query: str = "") -> list[dict]:
        params = {"q": query} if query else None
        return self.get("/catalog/products", params=params)

    def get_variants(self, product_id: str) -> list[dict]:
        return self.get(f"/catalog/products/{product_id}/variants")

    # -------------------------------------------------------- branches

    def list_branches(self) -> list[dict]:
        return self.get("/branches")

    # ------------------------------------------------------- inventory

    def get_stock_levels(self, branch_id: str) -> list[dict]:
        return self.get("/inventory/stock-levels", params={"branch_id": branch_id})

    def get_stock_batches(self, branch_id: str) -> list[dict]:
        """Raw per-batch data (id, cost, expiry, quantity_remaining) --
        what the offline cache needs for accurate FIFO allocation,
        as opposed to get_stock_levels()'s pre-aggregated totals.
        """
        return self.get("/inventory/stock-batches", params={"branch_id": branch_id})

    def receive_stock(
        self,
        variant_id: str,
        branch_id: str,
        quantity: int,
        cost_price_naira: float | None = None,
        expiry_date: str | None = None,
    ) -> dict:
        """Records a new incoming stock batch for a variant at a branch.

        Called from ui/receive_stock_dialog.py. cost_price_naira is
        converted to kobo (integer) for the API, matching how the rest
        of the platform stores currency amounts.
        """
        payload = {
            "variant_id": variant_id,
            "branch_id": branch_id,
            "quantity_received": quantity,
            "unit_cost_naira": cost_price_naira if cost_price_naira is not None else 0,
        }
        if expiry_date:
            payload["expiry_date"] = expiry_date
        return self.post("/inventory/stock-batches", json=payload)

    # ----------------------------------------------------------- sales

    def checkout(
        self,
        branch_id: str,
        items: list[dict],
        payment_method: str = "cash",
        discount_naira: float = 0,
        local_ref_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict:
        """items: [{"variant_id": ..., "quantity": ..., "unit_price_naira": ...}, ...]

        `local_ref_id` is the idempotency key. Generated fresh here if
        not supplied (the direct online-checkout path) -- but when a
        sale is being pushed from the offline outbox, the caller must
        pass the exact same local_ref_id that was generated at the
        moment of the offline sale, so a retried sync can never create
        a duplicate sale server-side.
        """
        body = {
            "branch_id": branch_id,
            "channel": "pos",
            "local_ref_id": local_ref_id or str(uuid.uuid4()),
            "items": items,
            "payment_method": payment_method,
            "discount_naira": discount_naira,
        }
        if customer_id:
            body["customer_id"] = customer_id
        return self.post("/sales/checkout", json=body)

    def list_sales(self, branch_id: str | None = None) -> list[dict]:
        """Staff only ever see their own sales -- enforced server-side
        in the /sales list endpoint regardless of this client's args.
        """
        params = {"branch_id": branch_id} if branch_id else None
        return self.get("/sales", params=params)

    # -------------------------------------------------------- customers

    def search_customers(self, query: str) -> list[dict]:
        return self.get("/customers/search", params={"q": query})

    def quick_create_customer(self, full_name: str, phone: str | None = None) -> dict:
        return self.post("/customers/quick", json={"full_name": full_name, "phone": phone})

    def redeem_loyalty_points(self, customer_id: str, points: int) -> dict:
        return self.post(f"/customers/{customer_id}/loyalty/redeem", json={"points": points})

    # ---------------------------------------------------------- coupons

    def issue_coupon(self, discount_naira: float) -> dict:
        """Super-admin only, enforced server-side via manage_coupons."""
        return self.post("/coupons/issue", json={"discount_amount_naira": discount_naira})

    def list_coupons(self) -> list[dict]:
        """Active coupons only -- what cache_sync.pull_coupons() needs
        to refresh the offline redemption cache. Any authenticated
        user can call this (unlike issue_coupon), since any cashier
        needs to be able to sync a newly-issued coupon down.
        """
        return self.get("/coupons")

    def redeem_coupon(self, code: str, sale_id: str, sale_total_naira: float) -> dict:
        """Reconciles a coupon redeemed locally (see pos_window.py's
        CouponService.redeem against LocalCouponRepository) up to the
        cloud, once the sale itself has synced. Called from
        sync/outbox_sync.py -- never directly from the UI -- with the
        cloud-assigned sale_id from that sync (not the local one, they
        differ). Raises ApiError (e.g. 409 if someone else redeemed the
        same code first) or a connection-level exception on failure;
        callers decide how to record that against the OutboxSale row.
        """
        return self.post(
            "/coupons/redeem",
            json={
                "code": code,
                "sale_id": sale_id,
                "sale_total_naira": sale_total_naira,
            },
        )