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
from local_db.cache_sync import get_cached_branches, pull_branches, pull_catalog
from local_db.models import CachedVariant
from local_db.repositories import LocalSaleRepository, LocalStockBatchRepository
from local_db.session import get_session, init_local_db
from local_db.session_cache import clear_session, load_session, save_last_branch, save_session
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
        session = get_session()
        try:
            try:
                self._branches = pull_branches(self._api, session)
            except Exception:
                self._branches = get_cached_branches(session)
                if not self._branches:
                    QMessageBox.critical(
                        self,
                        "Cannot load branches",
                        "Could not reach the server, and no branch list is cached yet.\n\n"
                        "This device needs to be online at least once to select a branch.",
                    )
                    return

            self.branch_combo.clear()
            for branch in self._branches:
                self.branch_combo.addItem(branch["name"], userData=branch["id"])

            cached_session = load_session(session)
            if cached_session and cached_session.last_branch_id:
                index = self.branch_combo.findData(cached_session.last_branch_id)
                if index >= 0:
                    self.branch_combo.setCurrentIndex(index)
        finally:
            session.close()

    def _current_branch_id(self) -> str | None:
        return self.branch_combo.currentData()

    def _on_branch_changed(self, _index: int) -> None:
        self._cart.clear()
        self._refresh_cart_table()

        branch_id = self._current_branch_id()
        if branch_id:
            session = get_session()
            try:
                save_last_branch(session, branch_id)
            finally:
                session.close()

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

        self._attempt_sync(show_result=False)

        QMessageBox.information(
            self,
            "Sale complete",
            f"Sale recorded.\nTotal: \u20a6{sale.total.naira:,.2f}\n"
            f"Payment: {self.payment_combo.currentText()}",
        )
        self._cart.clear()
        self._refresh_cart_table()
        self._search_local_cache()
        self._update_sync_status_label()

    # ----------------------------------------------------------- sync

    def _attempt_sync(self, show_result: bool) -> None:
        session = get_session()
        try:
            synced, failed = push_outbox(self._api, session)
            save_session(session, self._api)
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
