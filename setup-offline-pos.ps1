Write-Host "Adding offline-first support to the desktop POS..." -ForegroundColor Cyan

# --- apps\desktop_pos\local_db\models.py ---
@'
"""Local SQLite schema for offline-first operation.

This is deliberately a much smaller schema than the cloud database --
just enough to let the POS keep selling with zero internet:
- A read-only cache of variants/stock, refreshed from the API whenever
  online (see local_db/cache_sync.py).
- An outbox of sales made while offline (or even while online -- every
  sale always goes through the outbox first), pushed to the cloud API
  when connectivity is available (see sync/outbox_sync.py).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_str() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------- CATALOG CACHE

class CachedVariant(Base):
    """Mirrors just the fields the POS needs to sell an item offline.
    Refreshed wholesale from the API on each successful sync -- this
    table is never the source of truth, the cloud is.
    """

    __tablename__ = "cached_variants"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # cloud variant UUID as string
    product_id: Mapped[str] = mapped_column(String, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    variant_name: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String, nullable=True)
    selling_price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    reorder_level: Mapped[int] = mapped_column(Integer, default=5)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CachedStockBatch(Base):
    """Local mirror of cloud stock_batches for one branch -- what
    FifoStockAllocator (from nickcosmetics_core) actually reads/writes
    when a sale happens offline. Deltas made offline are reconciled
    against the cloud during sync; see sync/outbox_sync.py for the
    conflict-handling notes.
    """

    __tablename__ = "cached_stock_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # cloud batch UUID as string
    variant_id: Mapped[str] = mapped_column(String, ForeignKey("cached_variants.id"), nullable=False)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# --------------------------------------------------------------- OUTBOX

class OutboxSale(Base):
    """Every sale made on this device, online or offline, is written
    here first (and to CachedStockBatch, deducted immediately) before
    anything touches the network. `synced` flips to True once the
    cloud API has confirmed it -- see sync/outbox_sync.py.

    `local_ref_id` is the idempotency key the cloud API's /sales/checkout
    endpoint already understands (see checkout_adapter.py in the API),
    so a sync retry after a dropped connection can never double-sell.
    """

    __tablename__ = "outbox_sales"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    local_ref_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    served_by: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, default="pos")
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
    discount_kobo: Mapped[int] = mapped_column(Integer, default=0)
    subtotal_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    total_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    synced: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)


class OutboxSaleItem(Base):
    __tablename__ = "outbox_sale_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid_str)
    outbox_sale_id: Mapped[str] = mapped_column(String, ForeignKey("outbox_sales.id"), nullable=False)
    variant_id: Mapped[str] = mapped_column(String, nullable=False)
    stock_batch_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
'@ | Set-Content -Path "apps\desktop_pos\local_db\models.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\local_db\models.py" -ForegroundColor Green

# --- apps\desktop_pos\local_db\session.py ---
@'
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import LOCAL_DB_PATH
from local_db.models import Base

# Sync engine (not async) -- deliberately simple for a single-user
# desktop app talking to a local file; no connection pooling concerns
# like the cloud API has.
engine = create_engine(f"sqlite:///{LOCAL_DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_local_db() -> None:
    """Creates the local SQLite file/tables if they don't exist yet.
    Safe to call every app startup -- create_all() no-ops on tables
    that already exist.
    """
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
'@ | Set-Content -Path "apps\desktop_pos\local_db\session.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\local_db\session.py" -ForegroundColor Green

# --- apps\desktop_pos\local_db\repositories.py ---
@'
"""SQLite-backed implementations of the repository interfaces from
nickcosmetics_core.interfaces.repositories.

This is the offline counterpart to apps/api/infrastructure/db/
checkout_adapter.py -- same SalesService, same FifoStockAllocator,
same idempotency guarantee, just backed by a local SQLite Session
instead of async Postgres. Nothing in nickcosmetics_core had to change
to support this; that's the entire point of keeping the domain layer
framework-agnostic (see docs/architecture Section 1.3).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nickcosmetics_core.domain.inventory.stock import StockBatch as DomainStockBatch
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import PaymentMethod
from nickcosmetics_core.domain.sales.sale import Sale as DomainSale
from nickcosmetics_core.domain.sales.sale import SaleChannel, SaleItem, SaleStatus
from nickcosmetics_core.interfaces.repositories import SaleRepository, StockBatchRepository

from local_db.models import CachedStockBatch, OutboxSale, OutboxSaleItem


class LocalStockBatchRepository(StockBatchRepository):
    def __init__(self, session: Session):
        self._session = session

    def get_batches_for_variant(self, variant_id: str, branch_id: str) -> list[DomainStockBatch]:
        rows = self._session.execute(
            select(CachedStockBatch).where(
                CachedStockBatch.variant_id == variant_id,
                CachedStockBatch.branch_id == branch_id,
                CachedStockBatch.quantity_remaining > 0,
            )
        ).scalars().all()

        return [
            DomainStockBatch(
                id=row.id,
                variant_id=row.variant_id,
                branch_id=row.branch_id,
                quantity_received=row.quantity_remaining,  # local cache doesn't track original qty
                quantity_remaining=row.quantity_remaining,
                unit_cost=Money(row.unit_cost_kobo),
                expiry_date=row.expiry_date,
                received_at=row.received_at,
            )
            for row in rows
        ]

    def save_batch(self, batch: DomainStockBatch) -> None:
        row = self._session.get(CachedStockBatch, batch.id)
        if row is not None:
            row.quantity_remaining = batch.quantity_remaining


class LocalSaleRepository(SaleRepository):
    def __init__(self, session: Session):
        self._session = session

    def get_by_local_ref_id(self, local_ref_id: str) -> DomainSale | None:
        row = self._session.execute(
            select(OutboxSale).where(OutboxSale.local_ref_id == local_ref_id)
        ).scalar_one_or_none()
        if row is None:
            return None

        items = self._session.execute(
            select(OutboxSaleItem).where(OutboxSaleItem.outbox_sale_id == row.id)
        ).scalars().all()

        sale = DomainSale(
            id=row.id,
            branch_id=row.branch_id,
            served_by=row.served_by,
            channel=SaleChannel(row.channel),
            local_ref_id=row.local_ref_id,
            discount=Money(row.discount_kobo),
            payment_method=PaymentMethod(row.payment_method) if row.payment_method else None,
            status=SaleStatus.COMPLETED,
            created_at=row.created_at,
        )
        for item in items:
            sale.add_item(
                SaleItem(
                    variant_id=item.variant_id,
                    stock_batch_id=item.stock_batch_id,
                    quantity=item.quantity,
                    unit_price=Money(item.unit_price_kobo),
                )
            )
        return sale

    def save(self, sale: DomainSale) -> None:
        outbox_sale = OutboxSale(
            id=sale.id,
            local_ref_id=sale.local_ref_id,
            branch_id=sale.branch_id,
            served_by=sale.served_by,
            channel=sale.channel.value,
            payment_method=sale.payment_method.value if sale.payment_method else None,
            discount_kobo=sale.discount.kobo,
            subtotal_kobo=sale.subtotal.kobo,
            total_kobo=sale.total.kobo,
        )
        self._session.add(outbox_sale)

        for item in sale.items:
            self._session.add(
                OutboxSaleItem(
                    outbox_sale_id=sale.id,
                    variant_id=item.variant_id,
                    stock_batch_id=item.stock_batch_id,
                    quantity=item.quantity,
                    unit_price_kobo=item.unit_price.kobo,
                )
            )
'@ | Set-Content -Path "apps\desktop_pos\local_db\repositories.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\local_db\repositories.py" -ForegroundColor Green

# --- apps\desktop_pos\local_db\cache_sync.py ---
@'
"""Refreshes the local read-only catalog/stock cache from the cloud
API. Call this whenever online -- after login, after switching branch,
or via the "Sync Now" button. Wholesale replace-and-reinsert, since
this table is never the source of truth, just a mirror.
"""

from datetime import date, datetime

from sqlalchemy.orm import Session

from api_client import ApiClient
from local_db.models import CachedStockBatch, CachedVariant


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def pull_catalog(api: ApiClient, session: Session, branch_id: str) -> None:
    products = api.search_products("")

    session.query(CachedStockBatch).delete()
    session.query(CachedVariant).delete()

    for product in products:
        variants = api.get_variants(product["id"])
        for variant in variants:
            session.add(
                CachedVariant(
                    id=variant["id"],
                    product_id=product["id"],
                    product_name=product["name"],
                    variant_name=variant["variant_name"],
                    sku=variant["sku"],
                    barcode=variant.get("barcode"),
                    selling_price_kobo=round(variant["selling_price_naira"] * 100),
                    reorder_level=variant["reorder_level"],
                )
            )

    batches = api.get_stock_batches(branch_id)
    for batch in batches:
        if batch["quantity_remaining"] <= 0:
            continue
        expiry = date.fromisoformat(batch["expiry_date"]) if batch.get("expiry_date") else None
        received_at = _parse_iso(batch.get("received_at")) or datetime.utcnow()
        session.add(
            CachedStockBatch(
                id=batch["id"],
                variant_id=batch["variant_id"],
                branch_id=batch["branch_id"],
                quantity_remaining=batch["quantity_remaining"],
                unit_cost_kobo=round(batch["unit_cost_naira"] * 100),
                expiry_date=expiry,
                received_at=received_at,
            )
        )

    session.commit()
'@ | Set-Content -Path "apps\desktop_pos\local_db\cache_sync.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\local_db\cache_sync.py" -ForegroundColor Green

# --- apps\desktop_pos\sync\outbox_sync.py ---
@'
"""Pushes every unsynced sale in the local outbox to the cloud API.

Called automatically right after a sale (best-effort -- if it's
offline, the sale just stays queued), and manually via the "Sync Now"
button for a device that's been offline a while.

Reuses the exact `local_ref_id` generated at sale time, so the cloud
API's idempotency check (see apps/api/infrastructure/db/
checkout_adapter.py) guarantees a retried sync -- even one that
partially succeeded before a dropped connection -- can never create a
duplicate sale or double-deduct stock.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from api_client import ApiClient, ApiError
from local_db.models import OutboxSale, OutboxSaleItem
from nickcosmetics_core.domain.money import Money


def push_outbox(api: ApiClient, session: Session) -> tuple[int, int]:
    """Returns (synced_count, failed_count)."""
    pending = session.execute(
        select(OutboxSale).where(OutboxSale.synced.is_(False))
    ).scalars().all()

    synced_count = 0
    failed_count = 0

    for sale in pending:
        items = session.execute(
            select(OutboxSaleItem).where(OutboxSaleItem.outbox_sale_id == sale.id)
        ).scalars().all()

        api_items = [
            {
                "variant_id": item.variant_id,
                "quantity": item.quantity,
                "unit_price_naira": Money(item.unit_price_kobo).naira,
            }
            for item in items
        ]

        try:
            api.checkout(
                branch_id=sale.branch_id,
                items=api_items,
                payment_method=sale.payment_method or "cash",
                discount_naira=Money(sale.discount_kobo).naira,
                local_ref_id=sale.local_ref_id,
            )
        except ApiError as exc:
            sale.sync_error = exc.detail
            failed_count += 1
            continue
        except Exception as exc:
            # Network-level failure (offline, timeout, DNS, etc.) --
            # not a rejection, just "try again later."
            sale.sync_error = f"Connection error: {exc}"
            failed_count += 1
            continue

        sale.synced = True
        sale.synced_at = datetime.utcnow()
        sale.sync_error = None
        synced_count += 1

    session.commit()
    return synced_count, failed_count


def pending_count(session: Session) -> int:
    return len(
        session.execute(select(OutboxSale.id).where(OutboxSale.synced.is_(False))).all()
    )
'@ | Set-Content -Path "apps\desktop_pos\sync\outbox_sync.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\sync\outbox_sync.py" -ForegroundColor Green

# --- apps\desktop_pos\api_client.py ---
@'
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

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        url = f"{self.base_url}{path}"
        response = self._client.request(method, url, headers=self._headers(), **kwargs)
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

    # ----------------------------------------------------------- sales

    def checkout(
        self,
        branch_id: str,
        items: list[dict],
        payment_method: str = "cash",
        discount_naira: float = 0,
        local_ref_id: str | None = None,
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
        return self.post("/sales/checkout", json=body)
'@ | Set-Content -Path "apps\desktop_pos\api_client.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\api_client.py" -ForegroundColor Green

# --- apps\desktop_pos\ui\pos_window.py ---
@'
import uuid

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from api_client import ApiClient, ApiError
from local_db.cache_sync import pull_catalog
from local_db.models import CachedVariant
from local_db.repositories import LocalSaleRepository, LocalStockBatchRepository
from local_db.session import get_session, init_local_db
from nickcosmetics_core.application.sales_service import CheckoutLine, SalesService
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import SaleChannel
from nickcosmetics_core.exceptions import InsufficientStockError
from sync.outbox_sync import pending_count, push_outbox


class CartLine:
    def __init__(self, variant: CachedVariant, quantity: int):
        self.variant = variant
        self.quantity = quantity

    @property
    def unit_price_naira(self) -> float:
        return Money(self.variant.selling_price_kobo).naira

    @property
    def line_total(self) -> float:
        return self.unit_price_naira * self.quantity


class POSWindow(QWidget):
    """Offline-first: product search always reads the local SQLite
    cache (fast, works with zero internet); checkout always writes to
    the local outbox first via the same SalesService/FifoStockAllocator
    the cloud API uses, then makes a best-effort attempt to sync
    immediately. See docs/architecture Section 7 for the sync design
    this implements.
    """

    def __init__(self, api: ApiClient, on_logout):
        super().__init__()
        self._api = api
        self._on_logout = on_logout
        self._branches: list[dict] = []
        self._cart: list[CartLine] = []

        init_local_db()

        self.setWindowTitle("Nick Cosmetics POS")
        self.resize(900, 640)

        self._build_ui()
        self._load_branches()

        # Best-effort background sync every 60s -- catches "internet
        # came back" without the cashier needing to notice or act.
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._background_sync)
        self._sync_timer.start(60_000)

    # ------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout()

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(self._user_display_text()))
        top_bar.addStretch()

        top_bar.addWidget(QLabel("Branch:"))
        self.branch_combo = QComboBox()
        self.branch_combo.currentIndexChanged.connect(self._on_branch_changed)
        top_bar.addWidget(self.branch_combo)

        logout_button = QPushButton("Log Out")
        logout_button.clicked.connect(self._logout)
        top_bar.addWidget(logout_button)
        root.addLayout(top_bar)

        # -- sync status bar --
        sync_bar = QHBoxLayout()
        self.sync_status_label = QLabel("Not synced yet")
        sync_bar.addWidget(self.sync_status_label)
        sync_bar.addStretch()
        sync_now_button = QPushButton("Sync Now")
        sync_now_button.clicked.connect(self._manual_sync)
        sync_bar.addWidget(sync_now_button)
        root.addLayout(sync_bar)

        main_row = QHBoxLayout()

        left_col = QVBoxLayout()
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by name, SKU, or barcode...")
        self.search_input.textChanged.connect(self._search_local_cache)
        search_row.addWidget(self.search_input)
        left_col.addLayout(search_row)

        left_col.addWidget(QLabel("Items (from local cache)"))
        self.item_list = QListWidget()
        left_col.addWidget(self.item_list)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Qty:"))
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999)
        add_row.addWidget(self.qty_spin)
        add_to_cart_button = QPushButton("Add to Cart")
        add_to_cart_button.clicked.connect(self._add_to_cart)
        add_row.addWidget(add_to_cart_button)
        left_col.addLayout(add_row)

        main_row.addLayout(left_col, stretch=3)

        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("Cart"))
        self.cart_table = QTableWidget(0, 4)
        self.cart_table.setHorizontalHeaderLabels(["Item", "Qty", "Unit Price", "Line Total"])
        self.cart_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.cart_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        right_col.addWidget(self.cart_table)

        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self._remove_selected_cart_line)
        right_col.addWidget(remove_button)

        self.total_label = QLabel("Total: \u20a60.00")
        self.total_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.total_label.setAlignment(Qt.AlignRight)
        right_col.addWidget(self.total_label)

        payment_row = QHBoxLayout()
        payment_row.addWidget(QLabel("Payment:"))
        self.payment_combo = QComboBox()
        self.payment_combo.addItems(["cash", "card", "transfer", "online"])
        payment_row.addWidget(self.payment_combo)
        right_col.addLayout(payment_row)

        self.checkout_button = QPushButton("Checkout")
        self.checkout_button.setStyleSheet(
            "font-size: 16px; padding: 10px; background-color: #2e7d32; color: white;"
        )
        self.checkout_button.clicked.connect(self._checkout)
        right_col.addWidget(self.checkout_button)

        main_row.addLayout(right_col, stretch=2)
        root.addLayout(main_row)

        self.setLayout(root)

    def _user_display_text(self) -> str:
        user = self._api.current_user or {}
        return f"{user.get('full_name', '')} ({user.get('role', '')})"

    # -------------------------------------------------------- loading

    def _load_branches(self) -> None:
        try:
            self._branches = self._api.list_branches()
        except (ApiError, Exception) as exc:
            # Branch list itself isn't cached locally in this version
            # -- a device that's never been online before this moment
            # can't pick a branch yet. Flagged as a known limitation;
            # caching the branch list is a small follow-up.
            QMessageBox.critical(
                self,
                "Cannot load branches",
                f"Could not reach the server to load branches: {exc}\n\n"
                "This device needs to be online at least once to select a branch.",
            )
            return

        self.branch_combo.clear()
        for branch in self._branches:
            self.branch_combo.addItem(branch["name"], userData=branch["id"])

    def _current_branch_id(self) -> str | None:
        return self.branch_combo.currentData()

    def _on_branch_changed(self, _index: int) -> None:
        self._cart.clear()
        self._refresh_cart_table()
        self._sync_catalog_for_current_branch()

    def _sync_catalog_for_current_branch(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return

        session = get_session()
        try:
            pull_catalog(self._api, session, branch_id)
            self.sync_status_label.setText("Online -- catalog synced")
        except Exception as exc:
            self.sync_status_label.setText(f"Offline -- using cached data ({exc})")
        finally:
            session.close()

        self._search_local_cache()

    # ------------------------------------------------------------ search

    def _search_local_cache(self) -> None:
        query = self.search_input.text().strip().lower()
        session = get_session()
        try:
            stmt = select(CachedVariant)
            rows = session.execute(stmt).scalars().all()
        finally:
            session.close()

        self.item_list.clear()
        for variant in rows:
            haystack = f"{variant.product_name} {variant.variant_name} {variant.sku} {variant.barcode or ''}".lower()
            if query and query not in haystack:
                continue
            label = (
                f"{variant.product_name} -- {variant.variant_name} "
                f"({variant.sku}) \u20a6{Money(variant.selling_price_kobo).naira:,.2f}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, variant)
            self.item_list.addItem(item)

    # ------------------------------------------------------------ cart

    def _add_to_cart(self) -> None:
        current = self.item_list.currentItem()
        if current is None:
            QMessageBox.information(self, "No item selected", "Select an item to add.")
            return

        variant: CachedVariant = current.data(Qt.UserRole)
        quantity = self.qty_spin.value()

        for line in self._cart:
            if line.variant.id == variant.id:
                line.quantity += quantity
                break
        else:
            self._cart.append(CartLine(variant, quantity))

        self._refresh_cart_table()

    def _remove_selected_cart_line(self) -> None:
        row = self.cart_table.currentRow()
        if row < 0:
            return
        del self._cart[row]
        self._refresh_cart_table()

    def _refresh_cart_table(self) -> None:
        self.cart_table.setRowCount(len(self._cart))
        total = 0.0
        for row, line in enumerate(self._cart):
            self.cart_table.setItem(row, 0, QTableWidgetItem(line.variant.variant_name))
            self.cart_table.setItem(row, 1, QTableWidgetItem(str(line.quantity)))
            self.cart_table.setItem(row, 2, QTableWidgetItem(f"\u20a6{line.unit_price_naira:,.2f}"))
            self.cart_table.setItem(row, 3, QTableWidgetItem(f"\u20a6{line.line_total:,.2f}"))
            total += line.line_total

        self.total_label.setText(f"Total: \u20a6{total:,.2f}")

    # -------------------------------------------------------- checkout

    def _checkout(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Empty cart", "Add at least one item before checkout.")
            return

        branch_id = self._current_branch_id()
        if not branch_id:
            QMessageBox.warning(self, "No branch selected", "Select a branch first.")
            return

        served_by = (self._api.current_user or {}).get("id", "")

        session = get_session()
        try:
            stock_repo = LocalStockBatchRepository(session)
            sale_repo = LocalSaleRepository(session)
            service = SalesService(stock_repo, sale_repo)

            lines = [
                CheckoutLine(
                    variant_id=line.variant.id,
                    quantity=line.quantity,
                    unit_price=Money(line.variant.selling_price_kobo),
                )
                for line in self._cart
            ]

            try:
                sale = service.checkout(
                    sale_id=str(uuid.uuid4()),
                    branch_id=branch_id,
                    served_by=served_by,
                    channel=SaleChannel.POS,
                    local_ref_id=str(uuid.uuid4()),
                    lines=lines,
                    discount=Money(0),
                )
            except InsufficientStockError as exc:
                QMessageBox.warning(self, "Not enough stock", str(exc))
                return

            session.commit()
        finally:
            session.close()

        # Sale is safely recorded locally at this point regardless of
        # connectivity. Now try to sync it immediately -- if this
        # fails (offline), the sale simply stays queued; nothing about
        # the completed sale is undone or at risk.
        self._attempt_sync(show_result=False)

        QMessageBox.information(
            self,
            "Sale complete",
            f"Sale recorded.\nTotal: \u20a6{sale.total.naira:,.2f}\n"
            f"Payment: {self.payment_combo.currentText()}",
        )
        self._cart.clear()
        self._refresh_cart_table()
        self._search_local_cache()  # reflect updated stock in the list, if shown
        self._update_sync_status_label()

    # ----------------------------------------------------------- sync

    def _attempt_sync(self, show_result: bool) -> None:
        session = get_session()
        try:
            synced, failed = push_outbox(self._api, session)
        except Exception as exc:
            if show_result:
                QMessageBox.warning(self, "Sync failed", f"Could not sync: {exc}")
            session.close()
            return
        session.close()

        self._update_sync_status_label()
        if show_result:
            QMessageBox.information(
                self, "Sync complete", f"Synced: {synced}\nFailed (will retry): {failed}"
            )

    def _manual_sync(self) -> None:
        self._sync_catalog_for_current_branch()
        self._attempt_sync(show_result=True)

    def _background_sync(self) -> None:
        self._attempt_sync(show_result=False)

    def _update_sync_status_label(self) -> None:
        session = get_session()
        try:
            count = pending_count(session)
        finally:
            session.close()

        if count == 0:
            self.sync_status_label.setText("All sales synced")
        else:
            self.sync_status_label.setText(f"{count} sale(s) pending sync")

    # -------------------------------------------------------- session

    def _logout(self) -> None:
        self._sync_timer.stop()
        self._api.logout()
        self.close()
        self._on_logout()
'@ | Set-Content -Path "apps\desktop_pos\ui\pos_window.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\ui\pos_window.py" -ForegroundColor Green

Write-Host ""
Write-Host "Done. Reinstall requirements (sqlalchemy was added) then run main.py" -ForegroundColor Cyan
Write-Host "  cd apps\desktop_pos" -ForegroundColor Yellow
Write-Host "  .venv\Scripts\activate" -ForegroundColor Yellow
Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
Write-Host "  python main.py" -ForegroundColor Yellow
