import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select, update

from api_client import ApiClient, ApiError
from local_db.cache_sync import get_cached_branches, pull_branches, pull_catalog, pull_coupons
from local_db.held_sales import (
    delete_held_sale,
    get_held_sale_items,
    hold_sale,
    list_held_sales,
)
from local_db.models import CachedStockBatch, CachedVariant, OutboxSale
from local_db.repositories import LocalCouponRepository, LocalSaleRepository, LocalStockBatchRepository
from local_db.reports import list_recent_sales, todays_sales_summary, void_sale as void_local_sale
from local_db.session import get_session, init_local_db
from local_db.session_cache import clear_session, load_session, save_last_branch, save_session
from local_db.shifts import close_shift, get_open_shift, open_shift, shift_cash_summary
from nickcosmetics_core.application.coupon_service import CouponService
from nickcosmetics_core.application.sales_service import CheckoutLine, SalesService
from nickcosmetics_core.domain.coupons.coupon import CouponStatus
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import PaymentMethod, PaymentTender, SaleChannel
from nickcosmetics_core.exceptions import (
    CouponAlreadyUsedError,
    CouponExpiredError,
    CouponNotFoundError,
    CouponVoidedError,
    InsufficientStockError,
    PaymentMismatchError,
    SaleAlreadySyncedError,
    SaleAlreadyVoidedError,
)
from sync.outbox_sync import pending_count, push_outbox
from ui.receive_stock_dialog import ReceiveStockDialog
from ui.refund_dialog import RefundDialog
from ui.low_stock_dialog import LowStockDialog
from ui.receive_stock_dialog import ReceiveStockDialog
from ui.refund_dialog import RefundDialog
from ui.low_stock_dialog import LowStockDialog
from ui.receive_stock_dialog import ReceiveStockDialog
from ui.refund_dialog import RefundDialog
from ui.low_stock_dialog import LowStockDialog

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_LOGO_PATH = _ASSETS_DIR / "logo.jpg"

# Cart table column indices
_COL_ITEM = 0
_COL_QTY = 1
_COL_UNIT_PRICE = 2
_COL_DISCOUNT = 3
_COL_LINE_TOTAL = 4

# Tender table column indices
_TCOL_METHOD = 0
_TCOL_AMOUNT = 1

_PAYMENT_METHODS = ["cash", "card", "transfer", "online"]


class CartLine:
    def __init__(self, variant: CachedVariant, quantity: int, discount_naira: float = 0.0):
        self.variant = variant
        self.quantity = quantity
        self.discount_naira = discount_naira

    @property
    def unit_price_naira(self) -> float:
        return Money(self.variant.selling_price_kobo).naira

    @property
    def gross_total(self) -> float:
        return self.unit_price_naira * self.quantity

    @property
    def line_total(self) -> float:
        return max(0.0, self.gross_total - self.discount_naira)


class SalesHistoryDialog(QDialog):
    """Read-only list of the logged-in staff member's own past sales.

    Always hits the cloud API directly (no offline cache for this --
    it's a "nice to check" view, not something checkout depends on),
    and relies entirely on the server-side restriction in GET /sales
    that limits staff to their own records, never a client-side filter.
    """

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("My Sales History")
        self.resize(600, 420)

        layout = QVBoxLayout()

        self.status_label = QLabel("Loading...")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Date", "Items", "Total"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.setLayout(layout)
        self._load(api)

    def _load(self, api: ApiClient) -> None:
        try:
            sales = api.list_sales()
        except Exception as exc:
            self.status_label.setText(
                "Could not load sales history -- this needs an internet connection.\n"
                f"({exc})"
            )
            return

        sales = sorted(sales, key=lambda s: s.get("created_at") or "", reverse=True)

        if not sales:
            self.status_label.setText("No sales recorded yet.")
        else:
            self.status_label.setText(f"{len(sales)} sale(s)")

        self.table.setRowCount(len(sales))
        for row, sale in enumerate(sales):
            created_at = sale.get("created_at") or ""
            date_str = created_at[:16].replace("T", " ")
            items = sale.get("items") or []
            item_count = sum(item.get("quantity", 0) for item in items)
            total_naira = sale.get("total_naira", 0.0)

            self.table.setItem(row, 0, QTableWidgetItem(date_str))
            self.table.setItem(row, 1, QTableWidgetItem(f"{item_count} item(s)"))
            self.table.setItem(row, 2, QTableWidgetItem(f"\u20a6{total_naira:,.2f}"))


class OpenShiftDialog(QDialog):
    """Prompts for a counted opening float before a cashier can start
    ringing up sales. Shown automatically on login/branch-switch when
    no shift is already open, and via the "Open Shift" button.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open Shift")
        self.setFixedSize(320, 160)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Count the till and enter the opening cash float:"))

        self.float_input = QDoubleSpinBox()
        self.float_input.setRange(0, 10_000_000)
        self.float_input.setPrefix("\u20a6 ")
        self.float_input.setDecimals(2)
        layout.addWidget(self.float_input)

        buttons = QHBoxLayout()
        open_button = QPushButton("Open Shift")
        open_button.clicked.connect(self.accept)
        buttons.addWidget(open_button)
        cancel_button = QPushButton("Not now")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        self.setLayout(layout)

    def opening_float_naira(self) -> float:
        return self.float_input.value()


class CloseShiftDialog(QDialog):
    """Shows expected cash (opening float + cash sales this shift) and
    collects the counted amount, so a manager can immediately see any
    over/short against the till before confirming close.
    """

    def __init__(self, summary: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Close Shift")
        self.resize(340, 220)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"Opening float: \u20a6{summary['opening_float_naira']:,.2f}"))
        layout.addWidget(QLabel(f"Cash sales this shift: \u20a6{summary['cash_sales_naira']:,.2f}"))
        expected_label = QLabel(f"Expected in till: \u20a6{summary['expected_naira']:,.2f}")
        expected_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(expected_label)

        layout.addWidget(QLabel("Counted amount:"))
        self.counted_input = QDoubleSpinBox()
        self.counted_input.setRange(0, 10_000_000)
        self.counted_input.setPrefix("\u20a6 ")
        self.counted_input.setDecimals(2)
        self.counted_input.setValue(summary["expected_naira"])
        layout.addWidget(self.counted_input)

        self.diff_label = QLabel("")
        layout.addWidget(self.diff_label)
        self._expected_naira = summary["expected_naira"]
        self.counted_input.valueChanged.connect(self._update_diff_label)
        self._update_diff_label()

        buttons = QHBoxLayout()
        close_button = QPushButton("Close Shift")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        self.setLayout(layout)

    def _update_diff_label(self) -> None:
        diff = self.counted_input.value() - self._expected_naira
        if abs(diff) < 0.01:
            self.diff_label.setText("Matches expected.")
            self.diff_label.setStyleSheet("color: #4caf50;")
        elif diff > 0:
            self.diff_label.setText(f"Over by \u20a6{diff:,.2f}")
            self.diff_label.setStyleSheet("color: #ffb300;")
        else:
            self.diff_label.setText(f"Short by \u20a6{abs(diff):,.2f}")
            self.diff_label.setStyleSheet("color: #ff5252;")

    def counted_naira(self) -> float:
        return self.counted_input.value()


class HeldSalesDialog(QDialog):
    """Lists parked ("held") carts for this branch. Resuming loads the
    cart back into the POS window; deleting discards it permanently.
    """

    def __init__(self, branch_id: str, parent=None):
        super().__init__(parent)
        self._branch_id = branch_id
        self.resumed_held_sale_id: str | None = None

        self.setWindowTitle("Held Sales")
        self.resize(480, 320)

        layout = QVBoxLayout()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Held By", "Note", "Time"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        resume_button = QPushButton("Resume Selected")
        resume_button.clicked.connect(self._resume_selected)
        buttons.addWidget(resume_button)
        delete_button = QPushButton("Delete Selected")
        delete_button.clicked.connect(self._delete_selected)
        buttons.addWidget(delete_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.setLayout(layout)
        self._load()

    def _load(self) -> None:
        session = get_session()
        try:
            self._held = list_held_sales(session, self._branch_id)
        finally:
            session.close()

        self.table.setRowCount(len(self._held))
        for row, held in enumerate(self._held):
            self.table.setItem(row, 0, QTableWidgetItem(held.held_by_name))
            self.table.setItem(row, 1, QTableWidgetItem(held.note or ""))
            self.table.setItem(row, 2, QTableWidgetItem(held.created_at.strftime("%H:%M")))

    def _selected_held(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._held):
            return None
        return self._held[row]

    def _resume_selected(self) -> None:
        held = self._selected_held()
        if held is None:
            QMessageBox.information(self, "No sale selected", "Select a held sale to resume.")
            return
        self.resumed_held_sale_id = held.id
        self.accept()

    def _delete_selected(self) -> None:
        held = self._selected_held()
        if held is None:
            return
        session = get_session()
        try:
            delete_held_sale(session, held.id)
        finally:
            session.close()
        self._load()


class VoidSaleDialog(QDialog):
    """Lists this device's recent local sales so one can be voided
    (cancelled) before it syncs -- restoring the stock it deducted.
    Distinct from "Remove Selected" in the cart, which only removes a
    line that hasn't been checked out yet.
    """

    def __init__(self, branch_id: str, parent=None):
        super().__init__(parent)
        self._branch_id = branch_id

        self.setWindowTitle("Void a Recent Sale")
        self.resize(560, 360)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Only sales that haven't synced yet can be voided here."))

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Time", "Total", "Status", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        void_button = QPushButton("Void Selected")
        void_button.clicked.connect(self._void_selected)
        buttons.addWidget(void_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.setLayout(layout)
        self._load()

    def _load(self) -> None:
        session = get_session()
        try:
            self._sales = list_recent_sales(session, self._branch_id)
        finally:
            session.close()

        self.table.setRowCount(len(self._sales))
        for row, sale in enumerate(self._sales):
            self.table.setItem(row, 0, QTableWidgetItem(sale.created_at.strftime("%Y-%m-%d %H:%M")))
            self.table.setItem(row, 1, QTableWidgetItem(f"\u20a6{sale.total_kobo / 100:,.2f}"))
            if sale.voided:
                status = "Voided"
            elif sale.synced:
                status = "Synced (can't void here)"
            else:
                status = "Pending sync"
            self.table.setItem(row, 2, QTableWidgetItem(status))
            self.table.setItem(row, 3, QTableWidgetItem(""))

    def _void_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._sales):
            QMessageBox.information(self, "No sale selected", "Select a sale to void.")
            return

        sale = self._sales[row]
        confirm = QMessageBox.question(
            self,
            "Confirm void",
            f"Void this \u20a6{sale.total_kobo / 100:,.2f} sale and restore its stock?",
        )
        if confirm != QMessageBox.Yes:
            return

        session = get_session()
        try:
            void_local_sale(session, sale.id)
        except (SaleAlreadyVoidedError, SaleAlreadySyncedError) as exc:
            QMessageBox.warning(self, "Can't void this sale", str(exc))
            return
        finally:
            session.close()

        self._load()


class GenerateCouponDialog(QDialog):
    """Super-admin only: issues a new discount coupon via the API and
    refreshes the local coupon cache so it can be applied immediately.
    """

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate Discount Coupon")
        self.resize(360, 240)
        self._api = api
        self._generated_code = ""

        layout = QVBoxLayout()

        layout.addWidget(QLabel("Discount amount (\u20a6):"))
        self.amount_input = QLineEdit()
        self.amount_input.setPlaceholderText("e.g. 500")
        layout.addWidget(self.amount_input)

        generate_button = QPushButton("Generate")
        generate_button.clicked.connect(self._generate)
        layout.addWidget(generate_button)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        self.result_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.result_label)

        self.expiry_label = QLabel("")
        self.expiry_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.expiry_label)

        self.copy_button = QPushButton("Copy Code")
        self.copy_button.clicked.connect(self._copy_code)
        self.copy_button.setEnabled(False)
        layout.addWidget(self.copy_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.setLayout(layout)

    def _generate(self) -> None:
        text = self.amount_input.text().strip()
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Invalid amount", "Enter a discount amount greater than 0.")
            return

        try:
            result = self._api.issue_coupon(amount)
        except ApiError as exc:
            QMessageBox.warning(self, "Could not generate coupon", str(exc))
            return
        except Exception as exc:
            QMessageBox.warning(
                self, "Could not generate coupon", f"Check your internet connection.\n({exc})"
            )
            return

        self._generated_code = result["code"]
        self.result_label.setText(self._generated_code)
        expires_at = (result.get("expires_at") or "")[:16].replace("T", " ")
        self.expiry_label.setText(f"Expires: {expires_at}" if expires_at else "")
        self.copy_button.setEnabled(True)

        # Refresh the local coupon cache immediately so this code can
        # be applied at checkout right away, without a manual sync.
        session = get_session()
        try:
            pull_coupons(self._api, session)
        except Exception:
            pass  # non-fatal -- worst case, cashier still has "Sync Now" as a fallback
        finally:
            session.close()

    def _copy_code(self) -> None:
        if self._generated_code:
            QApplication.clipboard().setText(self._generated_code)


class POSWindow(QWidget):
    def __init__(self, api: ApiClient, on_logout):
        super().__init__()
        self._api = api
        self._on_logout = on_logout
        self._branches: list[dict] = []
        self._cart: list[CartLine] = []
        self._applied_coupon = None
        self._attached_customer: dict | None = None
        self._loyalty_redeemed_naira: float = 0.0
        self._tenders: list[PaymentTender] = []
        self._current_shift = None

        init_local_db()

        self.setWindowTitle("Nick Cosmetics POS")
        self.resize(1000, 680)

        self._build_ui()
        self._load_branches()

        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._background_sync)
        self._sync_timer.start(60_000)

    def _is_super_admin(self) -> bool:
        return (self._api.current_user or {}).get("role") == "super_admin"

    def _build_ui(self) -> None:
        root = QVBoxLayout()

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(self._user_display_text()))
        top_bar.addStretch()

        self.today_summary_label = QLabel("Today: --")
        top_bar.addWidget(self.today_summary_label)
        top_bar.addStretch()

        top_bar.addWidget(QLabel("Branch:"))
        self.branch_combo = QComboBox()
        self.branch_combo.currentIndexChanged.connect(self._on_branch_changed)
        top_bar.addWidget(self.branch_combo)

        my_sales_button = QPushButton("My Sales")
        my_sales_button.clicked.connect(self._open_sales_history)
        top_bar.addWidget(my_sales_button)

        void_button = QPushButton("Void Sale")
        void_button.clicked.connect(self._open_void_dialog)
        top_bar.addWidget(void_button)

        receive_stock_button = QPushButton("Receive Stock")
        receive_stock_button.clicked.connect(self._open_receive_stock)
        top_bar.addWidget(receive_stock_button)

        refund_button = QPushButton("Refund Sale")
        refund_button.clicked.connect(self._open_refund_dialog)
        top_bar.addWidget(refund_button)

        low_stock_button = QPushButton("Low Stock")
        low_stock_button.clicked.connect(self._open_low_stock)
        top_bar.addWidget(low_stock_button)

        if self._is_super_admin():
            generate_coupon_button = QPushButton("Generate Coupon")
            generate_coupon_button.clicked.connect(self._open_generate_coupon)
            top_bar.addWidget(generate_coupon_button)

        logout_button = QPushButton("Log Out")
        logout_button.clicked.connect(self._logout)
        top_bar.addWidget(logout_button)
        root.addLayout(top_bar)

        shift_bar = QHBoxLayout()
        self.shift_status_label = QLabel("Shift: not open")
        shift_bar.addWidget(self.shift_status_label)
        shift_bar.addStretch()
        self.shift_button = QPushButton("Open Shift")
        self.shift_button.clicked.connect(self._on_shift_button_clicked)
        shift_bar.addWidget(self.shift_button)
        root.addLayout(shift_bar)

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
        self.search_input.returnPressed.connect(self._on_search_enter)
        search_row.addWidget(self.search_input)
        left_col.addLayout(search_row)

        left_col.addWidget(QLabel("Items (from local cache) -- stock shown per item"))
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

        held_row = QHBoxLayout()
        hold_button = QPushButton("Hold Sale")
        hold_button.clicked.connect(self._hold_current_sale)
        held_row.addWidget(hold_button)
        held_sales_button = QPushButton("Held Sales")
        held_sales_button.clicked.connect(self._open_held_sales)
        held_row.addWidget(held_sales_button)
        left_col.addLayout(held_row)

        main_row.addLayout(left_col, stretch=3)

        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("Cart"))
        self.cart_table = QTableWidget(0, 5)
        self.cart_table.setHorizontalHeaderLabels(
            ["Item", "Qty", "Unit Price", "Discount (\u20a6)", "Line Total"]
        )
        self.cart_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.cart_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cart_table.cellChanged.connect(self._on_cart_cell_changed)
        right_col.addWidget(self.cart_table)

        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self._remove_selected_cart_line)
        right_col.addWidget(remove_button)

        customer_row = QHBoxLayout()
        customer_row.addWidget(QLabel("Customer:"))
        self.customer_search_input = QLineEdit()
        self.customer_search_input.setPlaceholderText("Search name or phone...")
        customer_row.addWidget(self.customer_search_input)
        find_customer_button = QPushButton("Find/Add")
        find_customer_button.clicked.connect(self._open_customer_picker)
        customer_row.addWidget(find_customer_button)
        clear_customer_button = QPushButton("Clear")
        clear_customer_button.clicked.connect(self._clear_customer)
        customer_row.addWidget(clear_customer_button)
        right_col.addLayout(customer_row)

        self.customer_status_label = QLabel("No customer attached")
        right_col.addWidget(self.customer_status_label)

        redeem_row = QHBoxLayout()
        self.redeem_points_input = QLineEdit()
        self.redeem_points_input.setPlaceholderText("Points to redeem")
        self.redeem_points_input.setFixedWidth(120)
        redeem_row.addWidget(self.redeem_points_input)
        redeem_button = QPushButton("Redeem Points")
        redeem_button.clicked.connect(self._redeem_customer_points)
        redeem_row.addWidget(redeem_button)
        redeem_row.addStretch()
        right_col.addLayout(redeem_row)

        coupon_row = QHBoxLayout()
        coupon_row.addWidget(QLabel("Discount Coupon Code:"))
        self.coupon_input = QLineEdit()
        self.coupon_input.setPlaceholderText("e.g. SAVE-XXXXXX")
        coupon_row.addWidget(self.coupon_input)
        apply_coupon_button = QPushButton("Apply")
        apply_coupon_button.clicked.connect(self._apply_coupon)
        coupon_row.addWidget(apply_coupon_button)
        remove_coupon_button = QPushButton("Remove")
        remove_coupon_button.clicked.connect(self._remove_coupon)
        coupon_row.addWidget(remove_coupon_button)
        right_col.addLayout(coupon_row)

        self.coupon_status_label = QLabel("No coupon applied")
        right_col.addWidget(self.coupon_status_label)

        totals_row = QHBoxLayout()
        totals_row.addWidget(QLabel("Discount Applied (\u20a6):"))
        self.sale_discount_input = QLineEdit("0")
        self.sale_discount_input.setFixedWidth(90)
        self.sale_discount_input.setReadOnly(True)
        totals_row.addWidget(self.sale_discount_input)
        totals_row.addStretch()
        right_col.addLayout(totals_row)

        self.total_label = QLabel("Total: \u20a60.00")
        self.total_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.total_label.setAlignment(Qt.AlignRight)
        right_col.addWidget(self.total_label)

        right_col.addWidget(QLabel("Payments"))
        self.tender_table = QTableWidget(0, 2)
        self.tender_table.setHorizontalHeaderLabels(["Method", "Amount"])
        self.tender_table.setMaximumHeight(90)
        self.tender_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tender_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        right_col.addWidget(self.tender_table)

        tender_row = QHBoxLayout()
        self.payment_combo = QComboBox()
        self.payment_combo.addItems(_PAYMENT_METHODS)
        tender_row.addWidget(self.payment_combo)
        self.tender_amount_input = QLineEdit()
        self.tender_amount_input.setPlaceholderText("Amount")
        tender_row.addWidget(self.tender_amount_input)
        add_tender_button = QPushButton("Add Tender")
        add_tender_button.clicked.connect(self._add_tender)
        tender_row.addWidget(add_tender_button)
        remove_tender_button = QPushButton("Remove")
        remove_tender_button.clicked.connect(self._remove_selected_tender)
        tender_row.addWidget(remove_tender_button)
        right_col.addLayout(tender_row)

        self.remaining_label = QLabel("Remaining: \u20a60.00")
        right_col.addWidget(self.remaining_label)

        action_row = QHBoxLayout()
        preview_button = QPushButton("Preview Receipt")
        preview_button.clicked.connect(self._preview_receipt)
        action_row.addWidget(preview_button)

        self.checkout_button = QPushButton("Checkout")
        self.checkout_button.setStyleSheet(
            "font-size: 16px; padding: 10px; background-color: #4caf50; color: white;"
        )
        self.checkout_button.clicked.connect(self._checkout)
        action_row.addWidget(self.checkout_button)
        right_col.addLayout(action_row)

        main_row.addLayout(right_col, stretch=2)
        root.addLayout(main_row)

        self.setLayout(root)

    def _user_display_text(self) -> str:
        user = self._api.current_user or {}
        return f"{user.get('full_name', '')} ({user.get('role', '')})"

    def _open_sales_history(self) -> None:
        dialog = SalesHistoryDialog(self._api, self)
        dialog.exec()

    def _open_generate_coupon(self) -> None:
        dialog = GenerateCouponDialog(self._api, self)
        dialog.exec()

    # ------------------------------------------------------- branches

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
        self._tenders.clear()
        self._refresh_cart_table()
        self._refresh_tender_table()

        branch_id = self._current_branch_id()
        if branch_id:
            session = get_session()
            try:
                save_last_branch(session, branch_id)
            finally:
                session.close()

        self._sync_catalog_for_current_branch()
        self._refresh_shift_status()
        self._update_today_summary_label()

    def _sync_catalog_for_current_branch(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return

        session = get_session()
        try:
            pull_catalog(self._api, session, branch_id)
            pull_coupons(self._api, session)
            self.sync_status_label.setText("Online -- catalog synced")
        except Exception as exc:
            self.sync_status_label.setText(f"Offline -- using cached data ({exc})")
        finally:
            session.close()

        self._search_local_cache()

    # ---------------------------------------------------------- search

    def _stock_levels_by_variant(self, session) -> dict[str, int]:
        rows = session.execute(select(CachedStockBatch)).scalars().all()
        levels: dict[str, int] = {}
        for row in rows:
            levels[row.variant_id] = levels.get(row.variant_id, 0) + row.quantity_remaining
        return levels

    def _search_local_cache(self) -> None:
        query = self.search_input.text().strip().lower()
        session = get_session()
        try:
            stmt = select(CachedVariant)
            rows = session.execute(stmt).scalars().all()
            stock_levels = self._stock_levels_by_variant(session)
        finally:
            session.close()

        self.item_list.clear()
        for variant in rows:
            haystack = f"{variant.product_name} {variant.variant_name} {variant.sku} {variant.barcode or ''}".lower()
            if query and query not in haystack:
                continue

            qty = stock_levels.get(variant.id, 0)
            label = (
                f"{variant.product_name} -- {variant.variant_name} "
                f"({variant.sku}) \u20a6{Money(variant.selling_price_kobo).naira:,.2f} "
                f"-- Stock: {qty}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, variant)
            if qty <= 0:
                item.setForeground(QColor("#ff5252"))
                item.setBackground(QColor("#3a1c22"))
                item.setText(label + "  [OUT OF STOCK]")
            elif qty <= variant.reorder_level:
                item.setForeground(QColor("#ffb300"))
                item.setBackground(QColor("#3a2f14"))
                item.setText(label + "  [LOW STOCK]")
            else:
                item.setForeground(QColor("#4caf50"))
            self.item_list.addItem(item)

    def _add_variant_to_cart(self, variant: CachedVariant, quantity: int) -> None:
        for line in self._cart:
            if line.variant.id == variant.id:
                line.quantity += quantity
                break
        else:
            self._cart.append(CartLine(variant, quantity))

        self._refresh_cart_table()

    def _add_to_cart(self) -> None:
        current = self.item_list.currentItem()
        if current is None:
            QMessageBox.information(self, "No item selected", "Select an item to add.")
            return

        variant: CachedVariant = current.data(Qt.UserRole)
        self._add_variant_to_cart(variant, self.qty_spin.value())

    def _flash_search_input(self, ok: bool) -> None:
        color = "#c8e6c9" if ok else "#ffcdd2"  # light green / light red
        self.search_input.setStyleSheet(f"background-color: {color};")
        QTimer.singleShot(700, lambda: self.search_input.setStyleSheet(""))

    def _on_search_enter(self) -> None:
        code = self.search_input.text().strip()
        if not code:
            return

        session = get_session()
        try:
            variant = session.execute(
                select(CachedVariant).where(CachedVariant.barcode == code)
            ).scalar_one_or_none()
        finally:
            session.close()

        if variant is None:
            self._flash_search_input(ok=False)
            return

        self._add_variant_to_cart(variant, self.qty_spin.value())
        self._flash_search_input(ok=True)
        self.search_input.clear()

    def _remove_selected_cart_line(self) -> None:
        row = self.cart_table.currentRow()
        if row < 0:
            return
        del self._cart[row]
        self._refresh_cart_table()

    def _on_cart_cell_changed(self, row: int, column: int) -> None:
        # _refresh_cart_table() blocks signals while it repopulates the
        # table, so this only ever fires from a genuine user edit --
        # never from our own setItem() calls below.
        if column != _COL_DISCOUNT:
            return
        if row >= len(self._cart):
            return

        line = self._cart[row]
        item = self.cart_table.item(row, _COL_DISCOUNT)
        text = item.text().strip() if item else ""

        try:
            entered = float(text) if text else 0.0
        except ValueError:
            entered = 0.0

        # A line discount can never exceed what that line is worth --
        # clamp rather than let it go negative or silently accept a
        # typo like an extra zero.
        clamped = max(0.0, min(entered, line.gross_total))
        line.discount_naira = clamped

        self._refresh_cart_table()

    def _refresh_cart_table(self) -> None:
        # Block signals during repopulation -- otherwise each setItem()
        # call below would re-trigger _on_cart_cell_changed() as if the
        # user had typed something, corrupting state and recursing.
        self.cart_table.blockSignals(True)
        try:
            self.cart_table.setRowCount(len(self._cart))
            for row, line in enumerate(self._cart):
                self.cart_table.setItem(row, _COL_ITEM, QTableWidgetItem(line.variant.variant_name))
                self.cart_table.setItem(row, _COL_QTY, QTableWidgetItem(str(line.quantity)))
                self.cart_table.setItem(
                    row, _COL_UNIT_PRICE, QTableWidgetItem(f"\u20a6{line.unit_price_naira:,.2f}")
                )
                self.cart_table.setItem(
                    row, _COL_DISCOUNT, QTableWidgetItem(f"{line.discount_naira:.2f}")
                )
                self.cart_table.setItem(
                    row, _COL_LINE_TOTAL, QTableWidgetItem(f"\u20a6{line.line_total:,.2f}")
                )

                for col in (_COL_ITEM, _COL_QTY, _COL_UNIT_PRICE, _COL_LINE_TOTAL):
                    cell = self.cart_table.item(row, col)
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
        finally:
            self.cart_table.blockSignals(False)

        self._update_total_label()

    # ----------------------------------------------------------- coupon

    def _apply_coupon(self) -> None:
        code = self.coupon_input.text().strip().upper()
        if not code:
            return

        session = get_session()
        try:
            coupon = LocalCouponRepository(session).get_by_code(code)
        finally:
            session.close()

        if coupon is None:
            QMessageBox.warning(
                self,
                "Coupon not found",
                "This code isn't in the local cache yet. Tap 'Sync Now' if it "
                "was just issued, then try again.",
            )
            return

        if coupon.status != CouponStatus.ACTIVE:
            QMessageBox.warning(self, "Coupon not usable", f"This coupon is {coupon.status.value}.")
            return

        if coupon.expires_at <= datetime.utcnow():
            QMessageBox.warning(self, "Coupon expired", "This coupon has expired.")
            return

        self._applied_coupon = coupon
        self.coupon_status_label.setText(
            f"Applied: {coupon.code} (\u2212\u20a6{coupon.discount_amount.naira:,.2f})"
        )
        self._refresh_discount_display()
        self._update_total_label()

    def _remove_coupon(self) -> None:
        self._applied_coupon = None
        self.coupon_input.clear()
        self.coupon_status_label.setText("No coupon applied")
        self._refresh_discount_display()
        self._update_total_label()

    # ----------------------------------------------------- customer/loyalty

    def _open_customer_picker(self) -> None:
        query = self.customer_search_input.text().strip()
        if not query:
            QMessageBox.information(self, "Search customer", "Type a name or phone number first.")
            return

        try:
            matches = self._api.search_customers(query)
        except Exception as exc:
            QMessageBox.warning(
                self, "Search failed", f"Couldn't reach the server to search customers.\n{exc}"
            )
            return

        if not matches:
            reply = QMessageBox.question(
                self,
                "No customer found",
                f"No customer matches '{query}'. Register them as a new customer?",
            )
            if reply == QMessageBox.Yes:
                try:
                    customer = self._api.quick_create_customer(full_name=query)
                except Exception as exc:
                    QMessageBox.warning(self, "Couldn't add customer", str(exc))
                    return
                self._attach_customer(customer)
            return

        if len(matches) == 1:
            self._attach_customer(matches[0])
            return

        # Multiple matches -- let the cashier pick the right one.
        names = [f"{m['full_name']} ({m.get('phone') or 'no phone'})" for m in matches]
        choice, ok = QInputDialog.getItem(
            self, "Select customer", "Multiple matches found:", names, editable=False
        )
        if ok and choice:
            self._attach_customer(matches[names.index(choice)])

    def _attach_customer(self, customer: dict) -> None:
        self._attached_customer = customer
        self.customer_status_label.setText(
            f"{customer['full_name']} \u2014 {customer.get('loyalty_points', 0)} points"
        )

    def _clear_customer(self) -> None:
        self._attached_customer = None
        self.customer_search_input.clear()
        self.customer_status_label.setText("No customer attached")

    def _redeem_customer_points(self) -> None:
        if self._attached_customer is None:
            QMessageBox.information(self, "No customer", "Find/attach a customer first.")
            return

        text = self.redeem_points_input.text().strip()
        if not text.isdigit() or int(text) <= 0:
            QMessageBox.information(self, "Invalid amount", "Enter a whole number of points.")
            return

        points = int(text)
        try:
            result = self._api.redeem_loyalty_points(self._attached_customer["id"], points)
        except Exception as exc:
            QMessageBox.warning(self, "Redemption failed", str(exc))
            return

        self._attached_customer["loyalty_points"] = result["remaining_points"]
        self.customer_status_label.setText(
            f"{self._attached_customer['full_name']} \u2014 {result['remaining_points']} points "
            f"(\u2212\u20a6{result['discount_naira']:,.2f} applied)"
        )
        self._loyalty_redeemed_naira += result["discount_naira"]
        self._refresh_discount_display()
        self._update_total_label()
        self.redeem_points_input.clear()

    def _get_coupon_discount_naira(self) -> float:
        if self._applied_coupon is None:
            return 0.0
        remaining = max(0.0, self._cart_subtotal() - self._get_sale_discount_naira())
        return max(0.0, min(self._applied_coupon.discount_amount.naira, remaining))

    def _cart_subtotal(self) -> float:
        return sum(line.line_total for line in self._cart)

    def _get_sale_discount_naira(self) -> float:
        return max(0.0, min(self._loyalty_redeemed_naira, self._cart_subtotal()))

    def _refresh_discount_display(self) -> None:
        total_discount = self._get_coupon_discount_naira() + self._loyalty_redeemed_naira
        self.sale_discount_input.setText(f"{total_discount:.2f}")

    def _current_total_naira(self) -> float:
        subtotal = self._cart_subtotal()
        sale_discount = self._get_sale_discount_naira()
        coupon_discount = self._get_coupon_discount_naira()
        return max(0.0, subtotal - sale_discount - coupon_discount)

    def _update_total_label(self) -> None:
        total = self._current_total_naira()
        self.total_label.setText(f"Total: \u20a6{total:,.2f}")
        self._update_remaining_label()

    # ---------------------------------------------------------- tenders

    def _refresh_tender_table(self) -> None:
        self.tender_table.setRowCount(len(self._tenders))
        for row, tender in enumerate(self._tenders):
            self.tender_table.setItem(row, _TCOL_METHOD, QTableWidgetItem(tender.method.value))
            self.tender_table.setItem(
                row, _TCOL_AMOUNT, QTableWidgetItem(f"\u20a6{tender.amount.naira:,.2f}")
            )
        self._update_remaining_label()

    def _update_remaining_label(self) -> None:
        total = self._current_total_naira()
        tendered = sum(t.amount.naira for t in self._tenders)
        remaining = total - tendered
        if remaining > 0.004:
            self.remaining_label.setText(f"Remaining: \u20a6{remaining:,.2f}")
            self.remaining_label.setStyleSheet("color: #ff5252;")
        elif remaining < -0.004:
            self.remaining_label.setText(f"Change due: \u20a6{abs(remaining):,.2f}")
            self.remaining_label.setStyleSheet("color: #ffb300;")
        else:
            self.remaining_label.setText("Fully paid")
            self.remaining_label.setStyleSheet("color: #4caf50;")
        # Convenience: keep the amount box prefilled with what's still
        # owed, so adding a single cash tender for the full total is
        # normally just "click Add Tender" with no typing.
        if not self.tender_amount_input.hasFocus():
            self.tender_amount_input.setText(f"{max(0.0, remaining):.2f}")

    def _add_tender(self) -> None:
        text = self.tender_amount_input.text().strip()
        try:
            amount_naira = float(text) if text else 0.0
        except ValueError:
            QMessageBox.warning(self, "Invalid amount", "Enter a valid tender amount.")
            return
        if amount_naira <= 0:
            QMessageBox.warning(self, "Invalid amount", "Tender amount must be greater than zero.")
            return

        method = PaymentMethod(self.payment_combo.currentText())
        self._tenders.append(PaymentTender(method=method, amount=Money.from_naira(amount_naira)))
        self._refresh_tender_table()

    def _remove_selected_tender(self) -> None:
        row = self.tender_table.currentRow()
        if row < 0 or row >= len(self._tenders):
            return
        del self._tenders[row]
        self._refresh_tender_table()

    # ------------------------------------------------------ held sales

    def _hold_current_sale(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Empty cart", "Nothing to hold.")
            return

        branch_id = self._current_branch_id()
        user = self._api.current_user or {}

        session = get_session()
        try:
            hold_sale(
                session,
                branch_id=branch_id,
                held_by=user.get("id", ""),
                held_by_name=user.get("full_name", ""),
                cart_lines=self._cart,
                sale_discount_naira=self._get_sale_discount_naira(),
                coupon_code=self._applied_coupon.code if self._applied_coupon else None,
            )
        finally:
            session.close()

        self._cart.clear()
        self._tenders.clear()
        self._loyalty_redeemed_naira = 0.0
        self._applied_coupon = None
        self.coupon_input.clear()
        self.coupon_status_label.setText("No coupon applied")
        self.sale_discount_input.setText("0")
        self._refresh_cart_table()
        self._refresh_tender_table()

    def _open_held_sales(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return

        dialog = HeldSalesDialog(branch_id, self)
        if dialog.exec() != QDialog.Accepted or not dialog.resumed_held_sale_id:
            return

        session = get_session()
        try:
            held_id = dialog.resumed_held_sale_id
            items = get_held_sale_items(session, held_id)
            held = next((h for h in dialog._held if h.id == held_id), None)
            variants_by_id = {
                v.id: v for v in session.execute(select(CachedVariant)).scalars().all()
            }

            self._cart.clear()
            for item in items:
                variant = variants_by_id.get(item.variant_id)
                if variant is None:
                    continue  # variant no longer in local catalog -- skip rather than crash
                self._cart.append(
                    CartLine(
                        variant,
                        item.quantity,
                        discount_naira=item.discount_kobo / 100,
                    )
                )

            if held is not None:
                self._loyalty_redeemed_naira = held.sale_discount_kobo / 100
                if held.coupon_code:
                    coupon = LocalCouponRepository(session).get_by_code(held.coupon_code)
                    if coupon is not None and coupon.status == CouponStatus.ACTIVE:
                        self._applied_coupon = coupon
                        self.coupon_input.setText(coupon.code)
                        self.coupon_status_label.setText(
                            f"Applied: {coupon.code} (\u2212\u20a6{coupon.discount_amount.naira:,.2f})"
                        )

            delete_held_sale(session, held_id)
        finally:
            session.close()

        self._refresh_discount_display()
        self._update_total_label()
        self._refresh_cart_table()

    # ------------------------------------------------------------ shift

    def _refresh_shift_status(self) -> None:
        branch_id = self._current_branch_id()
        user = self._api.current_user or {}
        if not branch_id or not user.get("id"):
            return

        session = get_session()
        try:
            self._current_shift = get_open_shift(session, branch_id, user["id"])
        finally:
            session.close()

        if self._current_shift is None:
            self.shift_status_label.setText("Shift: not open")
            self.shift_button.setText("Open Shift")
            self._prompt_open_shift()
        else:
            opened_at = self._current_shift.opened_at.strftime("%H:%M")
            self.shift_status_label.setText(
                f"Shift open since {opened_at} "
                f"(float \u20a6{self._current_shift.opening_float_kobo / 100:,.2f})"
            )
            self.shift_button.setText("Close Shift")

    def _prompt_open_shift(self) -> None:
        dialog = OpenShiftDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._do_open_shift(dialog.opening_float_naira())

    def _do_open_shift(self, opening_float_naira: float) -> None:
        branch_id = self._current_branch_id()
        user = self._api.current_user or {}
        session = get_session()
        try:
            self._current_shift = open_shift(
                session, branch_id, user.get("id", ""), user.get("full_name", ""), opening_float_naira
            )
        finally:
            session.close()
        self._refresh_shift_status()

    def _on_shift_button_clicked(self) -> None:
        if self._current_shift is None:
            self._prompt_open_shift()
            return

        session = get_session()
        try:
            summary = shift_cash_summary(session, self._current_shift)
        finally:
            session.close()

        dialog = CloseShiftDialog(summary, self)
        if dialog.exec() != QDialog.Accepted:
            return

        session = get_session()
        try:
            close_shift(session, self._current_shift.id, dialog.counted_naira())
        finally:
            session.close()

        self._current_shift = None
        self._refresh_shift_status()

    # ------------------------------------------------------ today summary

    def _update_today_summary_label(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            self.today_summary_label.setText("Today: --")
            return

        session = get_session()
        try:
            summary = todays_sales_summary(session, branch_id)
        finally:
            session.close()

        self.today_summary_label.setText(
            f"Today: {summary['count']} sale(s) \u00b7 \u20a6{summary['total_naira']:,.2f}"
        )

    # ----------------------------------------------------------- void

    def _open_receive_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            QMessageBox.warning(self, "No branch selected", "Select a branch first.")
            return
        dialog = ReceiveStockDialog(self._api, branch_id, self)
        if dialog.exec() == QDialog.Accepted:
            self._sync_catalog_for_current_branch()

    def _open_refund_dialog(self) -> None:
        dialog = RefundDialog(self._api, self)
        dialog.exec()

    def _open_low_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return
        dialog = LowStockDialog(self._api, branch_id, self)
        dialog.exec()

    def _open_receive_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            QMessageBox.warning(self, "No branch selected", "Select a branch first.")
            return
        dialog = ReceiveStockDialog(self._api, branch_id, self)
        if dialog.exec() == QDialog.Accepted:
            self._sync_catalog_for_current_branch()

    def _open_refund_dialog(self) -> None:
        dialog = RefundDialog(self._api, self)
        dialog.exec()

    def _open_low_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return
        dialog = LowStockDialog(self._api, branch_id, self)
        dialog.exec()

    def _open_receive_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            QMessageBox.warning(self, "No branch selected", "Select a branch first.")
            return
        dialog = ReceiveStockDialog(self._api, branch_id, self)
        if dialog.exec() == QDialog.Accepted:
            self._sync_catalog_for_current_branch()

    def _open_refund_dialog(self) -> None:
        dialog = RefundDialog(self._api, self)
        dialog.exec()

    def _open_low_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return
        dialog = LowStockDialog(self._api, branch_id, self)
        dialog.exec()

    def _open_void_dialog(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return
        dialog = VoidSaleDialog(branch_id, self)
        dialog.exec()
        self._update_today_summary_label()
        self._search_local_cache()  # stock may have been restored
        self._update_sync_status_label()

    # --------------------------------------------------------- checkout

    def _checkout(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Empty cart", "Add at least one item before checkout.")
            return

        branch_id = self._current_branch_id()
        if not branch_id:
            QMessageBox.warning(self, "No branch selected", "Select a branch first.")
            return

        if self._current_shift is None:
            QMessageBox.warning(
                self, "No shift open", "Open a shift before ringing up sales (see 'Open Shift')."
            )
            return

        if not self._tenders:
            QMessageBox.information(
                self, "No payment added", "Add at least one payment tender before checkout."
            )
            return

        served_by = (self._api.current_user or {}).get("id", "")
        sale_discount_naira = self._get_sale_discount_naira()
        coupon_discount_naira = self._get_coupon_discount_naira()
        sale_id = str(uuid.uuid4())

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
                    discount=Money.from_naira(line.discount_naira),
                )
                for line in self._cart
            ]

            try:
                sale = service.checkout(
                    sale_id=sale_id,
                    branch_id=branch_id,
                    served_by=served_by,
                    channel=SaleChannel.POS,
                    local_ref_id=str(uuid.uuid4()),
                    lines=lines,
                    discount=Money.from_naira(sale_discount_naira + coupon_discount_naira),
                    payments=list(self._tenders),
                )
            except InsufficientStockError as exc:
                QMessageBox.warning(self, "Not enough stock", str(exc))
                return
            except PaymentMismatchError as exc:
                QMessageBox.warning(self, "Payment doesn't cover total", str(exc))
                return

            if self._applied_coupon is not None:
                coupon_service = CouponService(LocalCouponRepository(session))
                try:
                    coupon_service.redeem(
                        code=self._applied_coupon.code,
                        cashier_id=served_by,
                        sale_id=sale_id,
                        sale_total=Money.from_naira(
                            max(0.0, self._cart_subtotal() - sale_discount_naira)
                        ),
                    )
                except (
                    CouponNotFoundError,
                    CouponAlreadyUsedError,
                    CouponVoidedError,
                    CouponExpiredError,
                ) as exc:
                    QMessageBox.warning(self, "Coupon could not be redeemed", str(exc))
                    session.rollback()
                    return

            # Tag this sale with the current shift for cash reconciliation
            # (see local_db/shifts.py:shift_cash_summary). Done as a
            # direct update rather than threading shift_id through
            # SalesService, since shifts are a local/POS-only concept
            # the shared core domain doesn't need to know about.
            session.execute(
                update(OutboxSale)
                .where(OutboxSale.id == sale_id)
                .values(shift_id=self._current_shift.id)
            )

            session.commit()
        finally:
            session.close()

        receipt_lines = list(self._cart)
        branch_name = self.branch_combo.currentText()

        self._attempt_sync(show_result=False)

        self._cart.clear()
        self._tenders.clear()
        self._loyalty_redeemed_naira = 0.0
        self._applied_coupon = None
        self.coupon_input.clear()
        self.coupon_status_label.setText("No coupon applied")
        self.sale_discount_input.setText("0")
        self._refresh_cart_table()
        self._refresh_tender_table()
        self._search_local_cache()
        self._update_sync_status_label()
        self._update_today_summary_label()

        self._offer_receipt(sale, receipt_lines, branch_name)

    def _offer_receipt(self, sale, cart_lines: list["CartLine"], branch_name: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Sale complete")
        change_line = ""
        if sale.change_due.kobo > 0:
            change_line = f"\nChange due: \u20a6{sale.change_due.naira:,.2f}"
        box.setText(
            f"Sale recorded.\nTotal: \u20a6{sale.total.naira:,.2f}{change_line}"
        )
        print_button = box.addButton("Print Receipt", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)
        box.exec()

        if box.clickedButton() == print_button:
            self._print_receipt(sale, cart_lines, branch_name)

    # --------------------------------------------------------- receipts

    def _receipt_rows_html(self, cart_lines: list["CartLine"]) -> str:
        return "".join(
            f"<tr>"
            f"<td>{line.variant.product_name} -- {line.variant.variant_name}</td>"
            f"<td align='right'>{line.quantity}</td>"
            f"<td align='right'>\u20a6{line.unit_price_naira:,.2f}</td>"
            f"<td align='right'>\u20a6{line.discount_naira:,.2f}</td>"
            f"<td align='right'>\u20a6{line.line_total:,.2f}</td>"
            f"</tr>"
            for line in cart_lines
        )

    def _receipt_header_html(self, branch_name: str) -> str:
        user = self._api.current_user or {}
        cashier = user.get("full_name", "")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        logo_html = ""
        if _LOGO_PATH.exists():
            logo_url = QUrl.fromLocalFile(str(_LOGO_PATH)).toString()
            logo_html = f'<img src="{logo_url}" width="100" /><br/>'

        return f"""
            <div style="text-align: center;">
                {logo_html}
                <h2 style="margin-bottom: 0;">Nick Cosmetics</h2>
                <div>{branch_name}</div>
                <div>{timestamp} -- Cashier: {cashier}</div>
                <hr/>
            </div>
        """

    def _build_receipt_html(self, sale, cart_lines: list["CartLine"], branch_name: str) -> str:
        rows_html = self._receipt_rows_html(cart_lines)
        payments_html = "".join(
            f"<tr><td align='right'>{p.method.value.title()}:</td>"
            f"<td align='right' width='120'>\u20a6{p.amount.naira:,.2f}</td></tr>"
            for p in sale.payments
        )
        change_html = ""
        if sale.change_due.kobo > 0:
            change_html = (
                f"<tr><td align='right'><b>Change:</b></td>"
                f"<td align='right'><b>\u20a6{sale.change_due.naira:,.2f}</b></td></tr>"
            )

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; font-size: 14px;">
        <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
        <table width="380" cellpadding="0" cellspacing="0"><tr><td>
            {self._receipt_header_html(branch_name)}
            <table width="100%" cellspacing="0" cellpadding="4">
                <tr>
                    <th align="left">Item</th>
                    <th align="right">Qty</th>
                    <th align="right">Price</th>
                    <th align="right">Disc.</th>
                    <th align="right">Total</th>
                </tr>
                {rows_html}
            </table>
            <hr/>
            <table width="100%">
                <tr><td align="right">Subtotal:</td><td align="right" width="120">\u20a6{sale.subtotal.naira:,.2f}</td></tr>
                <tr><td align="right">Discount:</td><td align="right">\u20a6{sale.discount.naira:,.2f}</td></tr>
                <tr><td align="right"><b>Total:</b></td><td align="right"><b>\u20a6{sale.total.naira:,.2f}</b></td></tr>
                {payments_html}
                {change_html}
            </table>
            <hr/>
            <div style="text-align: center;">Thank you for shopping with us!</div>
        </body>
        </html>
        """

    def _build_preview_receipt_html(self, cart_lines: list["CartLine"], branch_name: str) -> str:
        """Same layout as a completed-sale receipt, but computed live
        from the current cart/tenders -- for a cashier to sanity-check
        before actually charging the customer. No Sale object exists
        yet at this point, so totals are derived straight from the
        cart/UI state instead of from a persisted sale.
        """
        rows_html = self._receipt_rows_html(cart_lines)
        subtotal = self._cart_subtotal()
        sale_discount = self._get_sale_discount_naira()
        coupon_discount = self._get_coupon_discount_naira()
        total = self._current_total_naira()

        tenders_html = "".join(
            f"<tr><td align='right'>{t.method.value.title()}:</td>"
            f"<td align='right' width='120'>\u20a6{t.amount.naira:,.2f}</td></tr>"
            for t in self._tenders
        )

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; font-size: 14px;">
        <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
        <table width="380" cellpadding="0" cellspacing="0"><tr><td>
            {self._receipt_header_html(branch_name)}
            <div style="text-align:center; color:#ffb300;"><b>PREVIEW -- not a completed sale</b></div>
            <table width="100%" cellspacing="0" cellpadding="4">
                <tr>
                    <th align="left">Item</th>
                    <th align="right">Qty</th>
                    <th align="right">Price</th>
                    <th align="right">Disc.</th>
                    <th align="right">Total</th>
                </tr>
                {rows_html}
            </table>
            <hr/>
            <table width="100%">
                <tr><td align="right">Subtotal:</td><td align="right" width="120">\u20a6{subtotal:,.2f}</td></tr>
                <tr><td align="right">Sale Discount:</td><td align="right">\u20a6{sale_discount:,.2f}</td></tr>
                <tr><td align="right">Coupon Discount:</td><td align="right">\u20a6{coupon_discount:,.2f}</td></tr>
                <tr><td align="right"><b>Total:</b></td><td align="right"><b>\u20a6{total:,.2f}</b></td></tr>
                {tenders_html}
            </table>
        </body>
        </html>
        """

    def _print_receipt(self, sale, cart_lines: list["CartLine"], branch_name: str) -> None:
        document = QTextDocument()
        document.setHtml(self._build_receipt_html(sale, cart_lines, branch_name))

        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Print Receipt")
        if dialog.exec() == QPrintDialog.Accepted:
            document.print_(printer)

    def _preview_receipt(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Empty cart", "Add items to preview a receipt.")
            return

        branch_name = self.branch_combo.currentText()
        html = self._build_preview_receipt_html(self._cart, branch_name)

        dialog = QDialog(self)
        dialog.setWindowTitle("Receipt Preview")
        dialog.resize(420, 560)
        layout = QVBoxLayout()
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setHtml(html)
        layout.addWidget(viewer)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.setLayout(layout)
        dialog.exec()

    # ------------------------------------------------------------ sync

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
        self._update_today_summary_label()

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


