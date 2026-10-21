"""
Receive Stock dialog -- lets a staff member add inventory for a variant
directly from the POS, instead of going through the API docs manually.

Calls POST /api/v1/inventory/stock-batches via the existing ApiClient.
Assumes ApiClient has (or you'll add) a method like:

    def receive_stock(self, variant_id: str, branch_id: str, quantity: int,
                       cost_price_naira: float | None = None,
                       expiry_date: str | None = None) -> dict:
        payload = {
            "variant_id": variant_id,
            "branch_id": branch_id,
            "quantity": quantity,
        }
        if cost_price_naira is not None:
            payload["cost_price_kobo"] = int(round(cost_price_naira * 100))
        if expiry_date:
            payload["expiry_date"] = expiry_date
        return self._request("POST", "/inventory/stock-batches", json=payload)

If your ApiClient's method is named differently, adjust the one call
to self._api.receive_stock(...) below -- everything else is generic.

Integration (in ui/pos_window.py):

    from ui.receive_stock_dialog import ReceiveStockDialog

    # in _build_ui(), near the other admin/staff buttons in top_bar:
    receive_stock_button = QPushButton("Receive Stock")
    receive_stock_button.clicked.connect(self._open_receive_stock)
    top_bar.addWidget(receive_stock_button)

    # new method on POSWindow:
    def _open_receive_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            QMessageBox.warning(self, "No branch selected", "Select a branch first.")
            return
        dialog = ReceiveStockDialog(self._api, branch_id, self)
        if dialog.exec() == QDialog.Accepted:
            self._sync_catalog_for_current_branch()  # refresh stock numbers/list
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from sqlalchemy import select

from api_client import ApiClient, ApiError
from local_db.models import CachedVariant
from local_db.session import get_session


class ReceiveStockDialog(QDialog):
    """Pick a variant from the local cache, enter a quantity (and
    optionally cost price / expiry), and post it to the server as a
    new stock batch. Refreshing the catalog afterward pulls the
    updated stock level back down for display.
    """

    def __init__(self, api: ApiClient, branch_id: str, parent=None):
        super().__init__(parent)
        self._api = api
        self._branch_id = branch_id
        self._variants: list[CachedVariant] = []

        self.setWindowTitle("Receive Stock")
        self.resize(420, 320)

        layout = QVBoxLayout()

        layout.addWidget(QLabel("Category:"))
        self.category_combo = QComboBox()
        self.category_combo.currentIndexChanged.connect(self._filter_by_category)
        layout.addWidget(self.category_combo)

        layout.addWidget(QLabel("Product:"))
        self.variant_combo = QComboBox()
        layout.addWidget(self.variant_combo)
        self._load_variants()

        layout.addWidget(QLabel("Quantity received:"))
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 1_000_000)
        self.qty_spin.setValue(1)
        layout.addWidget(self.qty_spin)

        layout.addWidget(QLabel("Cost price per unit (\u20a6) -- optional:"))
        self.cost_input = QDoubleSpinBox()
        self.cost_input.setRange(0, 10_000_000)
        self.cost_input.setDecimals(2)
        layout.addWidget(self.cost_input)

        layout.addWidget(QLabel("Expiry date -- optional:"))
        expiry_row = QHBoxLayout()
        self.has_expiry_checkbox = QPushButton("No expiry")
        self.has_expiry_checkbox.setCheckable(True)
        self.has_expiry_checkbox.clicked.connect(self._toggle_expiry)
        expiry_row.addWidget(self.has_expiry_checkbox)
        self.expiry_date = QDateEdit()
        self.expiry_date.setCalendarPopup(True)
        self.expiry_date.setEnabled(False)
        expiry_row.addWidget(self.expiry_date)
        layout.addLayout(expiry_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        submit_button = QPushButton("Receive Stock")
        submit_button.clicked.connect(self._submit)
        button_row.addWidget(submit_button)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        self.setLayout(layout)

    def _toggle_expiry(self) -> None:
        has_expiry = self.has_expiry_checkbox.isChecked()
        self.has_expiry_checkbox.setText("Has expiry" if has_expiry else "No expiry")
        self.expiry_date.setEnabled(has_expiry)

    def _load_variants(self) -> None:
        session = get_session()
        try:
            self._variants = list(session.execute(select(CachedVariant)).scalars().all())
        finally:
            session.close()

        categories = sorted({v.category or "Uncategorized" for v in self._variants})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("All categories", userData=None)
        for category in categories:
            self.category_combo.addItem(category, userData=category)
        self.category_combo.blockSignals(False)

        self._populate_variant_combo()

    def _populate_variant_combo(self, category: str | None = None) -> None:
        self.variant_combo.clear()
        for variant in self._variants:
            if category and (variant.category or "Uncategorized") != category:
                continue
            label = f"{variant.product_name} -- {variant.variant_name} ({variant.sku})"
            self.variant_combo.addItem(label, userData=variant.id)

    def _filter_by_category(self) -> None:
        category = self.category_combo.currentData()
        self._populate_variant_combo(category)

    def _submit(self) -> None:
        variant_id = self.variant_combo.currentData()
        if not variant_id:
            self.status_label.setText("No product selected.")
            return

        quantity = self.qty_spin.value()
        cost_naira = self.cost_input.value() or None
        expiry = (
            self.expiry_date.date().toString("yyyy-MM-dd")
            if self.has_expiry_checkbox.isChecked()
            else None
        )

        try:
            self._api.receive_stock(
                variant_id=variant_id,
                branch_id=self._branch_id,
                quantity=quantity,
                cost_price_naira=cost_naira,
                expiry_date=expiry,
            )
        except ApiError as exc:
            self.status_label.setText(str(exc))
            return
        except Exception as exc:
            self.status_label.setText(f"Could not reach server: {exc}")
            return

        QMessageBox.information(
            self, "Stock received", f"Added {quantity} unit(s) to inventory."
        )
        self.accept()