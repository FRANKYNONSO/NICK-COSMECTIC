Write-Host "Creating desktop POS app files..." -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "apps\desktop_pos\ui" | Out-Null
New-Item -ItemType Directory -Force -Path "apps\desktop_pos\local_db" | Out-Null
New-Item -ItemType Directory -Force -Path "apps\desktop_pos\sync" | Out-Null

# --- apps\desktop_pos\requirements.txt ---
@'
# Desktop POS -- runs directly on Windows/Mac/Linux (not in Docker;
# GUI applications need a real display, which containers don't have).

PySide6>=6.7
httpx>=0.27
python-dotenv>=1.0

# Local offline database (added in the next increment alongside sync)
sqlalchemy>=2.0

# Shared domain package (editable local install, same as the API uses)
-e ../../packages/core
'@ | Set-Content -Path "apps\desktop_pos\requirements.txt" -Encoding utf8
Write-Host "  Created apps\desktop_pos\requirements.txt" -ForegroundColor Green

# --- apps\desktop_pos\.env.example ---
@'
# Copy to .env in this same folder if you need to override defaults.
# Points at the API running in your local Docker Compose setup by default.

API_BASE_URL=http://localhost:8000/api/v1
'@ | Set-Content -Path "apps\desktop_pos\.env.example" -Encoding utf8
Write-Host "  Created apps\desktop_pos\.env.example" -ForegroundColor Green

# --- apps\desktop_pos\config.py ---
@'
import os

from dotenv import load_dotenv

load_dotenv()

# Points at the cloud API by default. Once the API is deployed (not
# just running in local Docker), change this via a .env file next to
# main.py -- e.g. API_BASE_URL=https://api.nickcosmetics.com/api/v1
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000/api/v1")

# Where the local SQLite offline database lives (used starting in the
# offline-first increment, not yet wired into checkout in this version).
LOCAL_DB_PATH = os.environ.get("LOCAL_DB_PATH", "nick_pos_local.db")
'@ | Set-Content -Path "apps\desktop_pos\config.py" -Encoding utf8
Write-Host "  Created apps\desktop_pos\config.py" -ForegroundColor Green

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

    # ----------------------------------------------------------- sales

    def checkout(
        self,
        branch_id: str,
        items: list[dict],
        payment_method: str = "cash",
        discount_naira: float = 0,
    ) -> dict:
        """items: [{"variant_id": ..., "quantity": ..., "unit_price_naira": ...}, ...]

        `local_ref_id` is generated fresh per checkout call -- this is
        the idempotency key that will matter once offline queuing is
        added; generating it here (not server-side) means the same
        pattern already works when this call later gets deferred to a
        local outbox instead of going straight to the API.
        """
        body = {
            "branch_id": branch_id,
            "channel": "pos",
            "local_ref_id": str(uuid.uuid4()),
            "items": items,
            "payment_method": payment_method,
            "discount_naira": discount_naira,
        }
        return self.post("/sales/checkout", json=body)
'@ | Set-Content -Path "apps\desktop_pos\api_client.py" -Encoding utf8
Write-Host "  Created apps\desktop_pos\api_client.py" -ForegroundColor Green

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

        self._on_success(self._api)
'@ | Set-Content -Path "apps\desktop_pos\ui\login_window.py" -Encoding utf8
Write-Host "  Created apps\desktop_pos\ui\login_window.py" -ForegroundColor Green

# --- apps\desktop_pos\ui\pos_window.py ---
@'
from PySide6.QtCore import Qt
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

from api_client import ApiClient, ApiError


class CartLine:
    def __init__(self, variant: dict, quantity: int):
        self.variant = variant
        self.quantity = quantity

    @property
    def line_total(self) -> float:
        return self.variant["selling_price_naira"] * self.quantity


class POSWindow(QWidget):
    def __init__(self, api: ApiClient, on_logout):
        super().__init__()
        self._api = api
        self._on_logout = on_logout
        self._branches: list[dict] = []
        self._products: list[dict] = []
        self._variants: list[dict] = []
        self._cart: list[CartLine] = []

        self.setWindowTitle("Nick Cosmetics POS")
        self.resize(900, 600)

        self._build_ui()
        self._load_branches()

    # ------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout()

        # -- top bar: user info, branch selector, logout --
        top_bar = QHBoxLayout()
        user_label = QLabel(self._user_display_text())
        top_bar.addWidget(user_label)
        top_bar.addStretch()

        top_bar.addWidget(QLabel("Branch:"))
        self.branch_combo = QComboBox()
        self.branch_combo.currentIndexChanged.connect(self._on_branch_changed)
        top_bar.addWidget(self.branch_combo)

        logout_button = QPushButton("Log Out")
        logout_button.clicked.connect(self._logout)
        top_bar.addWidget(logout_button)
        root.addLayout(top_bar)

        # -- main split: product search+list on left, cart on right --
        main_row = QHBoxLayout()

        left_col = QVBoxLayout()
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search products by name...")
        self.search_input.returnPressed.connect(self._search_products)
        search_row.addWidget(self.search_input)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self._search_products)
        search_row.addWidget(search_button)
        left_col.addLayout(search_row)

        left_col.addWidget(QLabel("Products"))
        self.product_list = QListWidget()
        self.product_list.currentItemChanged.connect(self._on_product_selected)
        left_col.addWidget(self.product_list)

        left_col.addWidget(QLabel("Variants"))
        self.variant_list = QListWidget()
        left_col.addWidget(self.variant_list)

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
        except ApiError as exc:
            QMessageBox.critical(self, "Error", f"Could not load branches: {exc.detail}")
            return

        self.branch_combo.clear()
        for branch in self._branches:
            self.branch_combo.addItem(branch["name"], userData=branch["id"])

    def _current_branch_id(self) -> str | None:
        return self.branch_combo.currentData()

    def _on_branch_changed(self, _index: int) -> None:
        self._cart.clear()
        self._refresh_cart_table()

    def _search_products(self) -> None:
        query = self.search_input.text().strip()
        try:
            self._products = self._api.search_products(query)
        except ApiError as exc:
            QMessageBox.critical(self, "Error", f"Search failed: {exc.detail}")
            return

        self.product_list.clear()
        self.variant_list.clear()
        for product in self._products:
            item = QListWidgetItem(f"{product['name']} ({product['sku']})")
            item.setData(Qt.UserRole, product["id"])
            self.product_list.addItem(item)

    def _on_product_selected(self, current: QListWidgetItem, _previous) -> None:
        self.variant_list.clear()
        self._variants = []
        if current is None:
            return

        product_id = current.data(Qt.UserRole)
        try:
            self._variants = self._api.get_variants(product_id)
        except ApiError as exc:
            QMessageBox.critical(self, "Error", f"Could not load variants: {exc.detail}")
            return

        for variant in self._variants:
            label = (
                f"{variant['variant_name']} -- "
                f"\u20a6{variant['selling_price_naira']:,.2f}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, variant)
            self.variant_list.addItem(item)

    # ------------------------------------------------------------ cart

    def _add_to_cart(self) -> None:
        current = self.variant_list.currentItem()
        if current is None:
            QMessageBox.information(self, "No variant selected", "Select a variant to add.")
            return

        variant = current.data(Qt.UserRole)
        quantity = self.qty_spin.value()

        for line in self._cart:
            if line.variant["id"] == variant["id"]:
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
            self.cart_table.setItem(row, 0, QTableWidgetItem(line.variant["variant_name"]))
            self.cart_table.setItem(row, 1, QTableWidgetItem(str(line.quantity)))
            self.cart_table.setItem(
                row, 2, QTableWidgetItem(f"\u20a6{line.variant['selling_price_naira']:,.2f}")
            )
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

        items = [
            {
                "variant_id": line.variant["id"],
                "quantity": line.quantity,
                "unit_price_naira": line.variant["selling_price_naira"],
            }
            for line in self._cart
        ]

        self.checkout_button.setEnabled(False)
        try:
            sale = self._api.checkout(
                branch_id=branch_id,
                items=items,
                payment_method=self.payment_combo.currentText(),
            )
        except ApiError as exc:
            QMessageBox.critical(self, "Checkout failed", exc.detail)
            return
        except Exception as exc:
            QMessageBox.critical(self, "Checkout failed", f"Could not reach the server: {exc}")
            return
        finally:
            self.checkout_button.setEnabled(True)

        QMessageBox.information(
            self,
            "Sale complete",
            f"Sale recorded.\nTotal: \u20a6{sale['total_naira']:,.2f}\n"
            f"Payment: {sale['payment_method']}",
        )
        self._cart.clear()
        self._refresh_cart_table()

    # -------------------------------------------------------- session

    def _logout(self) -> None:
        self._api.logout()
        self.close()
        self._on_logout()
'@ | Set-Content -Path "apps\desktop_pos\ui\pos_window.py" -Encoding utf8
Write-Host "  Created apps\desktop_pos\ui\pos_window.py" -ForegroundColor Green

# --- apps\desktop_pos\main.py ---
@'
import sys
from pathlib import Path

# Allow `from ui.login_window import ...` etc. to resolve when run
# directly (python main.py) regardless of the working directory.
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication

from api_client import ApiClient
from ui.login_window import LoginWindow
from ui.pos_window import POSWindow


class AppController:
    """Owns the currently-shown window and swaps between login and POS
    screens on login/logout, so main.py itself stays trivial.
    """

    def __init__(self):
        self.window = None
        self.show_login()

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
Write-Host "  Created apps\desktop_pos\main.py" -ForegroundColor Green

New-Item -ItemType File -Force -Path "apps\desktop_pos\ui\__init__.py" | Out-Null
New-Item -ItemType File -Force -Path "apps\desktop_pos\local_db\__init__.py" | Out-Null
New-Item -ItemType File -Force -Path "apps\desktop_pos\sync\__init__.py" | Out-Null
Write-Host "  Created __init__.py files" -ForegroundColor Green

Write-Host ""
Write-Host "Done. See setup instructions in the next message to install and run." -ForegroundColor Cyan
