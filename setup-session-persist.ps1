Write-Host "Adding persistent login session (offline app restart fix)..." -ForegroundColor Cyan

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


# ------------------------------------------------------- SESSION CACHE

class SessionCache(Base):
    """Persists the last successful login so the app can resume
    without the internet -- e.g. reopened while offline. Always a
    single row (id=1). See local_db/session_cache.py for how this is
    used: checked fresh against the API when reachable, otherwise
    trusted as-is so a cashier isn't locked out of a working offline
    device just because the login screen itself needs connectivity.
    """

    __tablename__ = "session_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
'@ | Set-Content -Path "apps\desktop_pos\local_db\models.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\local_db\models.py" -ForegroundColor Green

# --- apps\desktop_pos\local_db\session_cache.py ---
@'
"""Persists and restores the login session so the app can resume
without a fresh login -- see local_db/models.py SessionCache for why.
"""

from sqlalchemy.orm import Session

from api_client import ApiClient
from local_db.models import SessionCache

_SESSION_ROW_ID = 1  # always a single row


def save_session(session: Session, api: ApiClient) -> None:
    user = api.current_user or {}
    existing = session.get(SessionCache, _SESSION_ROW_ID)
    if existing is None:
        existing = SessionCache(id=_SESSION_ROW_ID)
        session.add(existing)

    existing.user_id = str(user.get("id", ""))
    existing.full_name = user.get("full_name", "")
    existing.email = user.get("email", "") or ""
    existing.role = user.get("role", "")
    existing.branch_id = str(user["branch_id"]) if user.get("branch_id") else None
    existing.access_token = api.access_token or ""
    existing.refresh_token = api.refresh_token or ""

    session.commit()


def load_session(session: Session) -> SessionCache | None:
    return session.get(SessionCache, _SESSION_ROW_ID)


def clear_session(session: Session) -> None:
    existing = session.get(SessionCache, _SESSION_ROW_ID)
    if existing is not None:
        session.delete(existing)
        session.commit()
'@ | Set-Content -Path "apps\desktop_pos\local_db\session_cache.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\local_db\session_cache.py" -ForegroundColor Green

# --- apps\desktop_pos\ui\login_window.py ---
@'
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from api_client import ApiClient, ApiError
from local_db.session import get_session, init_local_db
from local_db.session_cache import save_session


class LoginWindow(QWidget):
    """First screen shown on launch. On successful login, calls
    `on_success(api_client)` so main.py can hand off to the POS window
    without this module needing to know what comes next.
    """

    def __init__(self, on_success):
        super().__init__()
        self._on_success = on_success
        self._api = ApiClient()

        self.setWindowTitle("Nick Cosmetics POS -- Login")
        self.setFixedSize(360, 220)

        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Nick Cosmetics")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Email")
        layout.addWidget(self.email_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.returnPressed.connect(self._attempt_login)
        layout.addWidget(self.password_input)

        self.login_button = QPushButton("Log In")
        self.login_button.clicked.connect(self._attempt_login)
        layout.addWidget(self.login_button)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.setLayout(layout)

    def _attempt_login(self) -> None:
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self.status_label.setText("Enter both email and password.")
            return

        self.login_button.setEnabled(False)
        self.status_label.setText("Logging in...")

        try:
            self._api.login(email, password)
        except ApiError as exc:
            self.status_label.setText(exc.detail)
            self.login_button.setEnabled(True)
            return
        except Exception as exc:
            self.status_label.setText(f"Could not reach the server: {exc}")
            self.login_button.setEnabled(True)
            return

        init_local_db()
        db_session = get_session()
        try:
            save_session(db_session, self._api)
        finally:
            db_session.close()

        self._on_success(self._api)
'@ | Set-Content -Path "apps\desktop_pos\ui\login_window.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\ui\login_window.py" -ForegroundColor Green

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
from local_db.session_cache import clear_session
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
        session = get_session()
        try:
            clear_session(session)
        finally:
            session.close()
        self._api.logout()
        self.close()
        self._on_logout()
'@ | Set-Content -Path "apps\desktop_pos\ui\pos_window.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\ui\pos_window.py" -ForegroundColor Green

# --- apps\desktop_pos\main.py ---
@'
import sys
from pathlib import Path

# Allow `from ui.login_window import ...` etc. to resolve when run
# directly (python main.py) regardless of the working directory.
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication

from api_client import ApiClient, ApiError
from local_db.session import get_session, init_local_db
from local_db.session_cache import clear_session, load_session, save_session
from ui.login_window import LoginWindow
from ui.pos_window import POSWindow


class AppController:
    """Owns the currently-shown window and swaps between login and POS
    screens on login/logout, so main.py itself stays trivial.

    On startup, tries to resume a previously cached session (see
    local_db/session_cache.py) before falling back to the login
    screen -- this is what lets the app reopen and keep working for a
    device that's offline at that exact moment, rather than being
    locked out of an otherwise-functional offline POS just because
    login itself would normally need connectivity.
    """

    def __init__(self):
        init_local_db()
        self.window = None
        if not self._try_resume_session():
            self.show_login()

    def _try_resume_session(self) -> bool:
        db_session = get_session()
        try:
            cached = load_session(db_session)
            if cached is None:
                return False

            api = ApiClient()
            api.access_token = cached.access_token
            api.refresh_token = cached.refresh_token
            api.current_user = {
                "id": cached.user_id,
                "full_name": cached.full_name,
                "email": cached.email,
                "role": cached.role,
                "branch_id": cached.branch_id,
                "is_active": True,
            }

            try:
                fresh_user = api.get("/auth/me")
                api.current_user = fresh_user
                save_session(db_session, api)
            except ApiError:
                # A real rejection while online -- e.g. account
                # disabled or token revoked. Cached credentials are no
                # longer valid; require a fresh login.
                clear_session(db_session)
                return False
            except Exception:
                # Server unreachable (offline). Proceed with the
                # cached identity -- checkout and search both work
                # entirely from local data regardless of token
                # validity; only sync/catalog-pull need a live token,
                # and those already fail gracefully on their own.
                pass

            self.show_pos(api)
            return True
        finally:
            db_session.close()

    def show_login(self) -> None:
        self.window = LoginWindow(on_success=self.show_pos)
        self.window.show()

    def show_pos(self, api: ApiClient) -> None:
        self.window = POSWindow(api, on_logout=self.show_login)
        self.window.show()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Nick Cosmetics POS")

    controller = AppController()  # noqa: F841 -- keeps windows alive

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
'@ | Set-Content -Path "apps\desktop_pos\main.py" -Encoding utf8
Write-Host "  Updated apps\desktop_pos\main.py" -ForegroundColor Green

Write-Host ""
Write-Host "Done. No new dependencies -- just rerun: python main.py" -ForegroundColor Cyan
