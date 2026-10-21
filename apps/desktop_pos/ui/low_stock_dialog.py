"""
Low Stock dialog -- surfaces GET /api/v1/reports/low-stock in the
desktop app instead of leaving it as an API-docs-only endpoint.

Assumes ApiClient has (or you'll add) a method like:

    def low_stock_report(self, branch_id: str) -> list[dict]:
        return self._request("GET", "/reports/low-stock",
                              params={"branch_id": branch_id})

Each item in the response is assumed to look roughly like:
    {
        "product_name": "...", "variant_name": "...", "sku": "...",
        "quantity_remaining": 3, "reorder_level": 10
    }
Adjust the field names in _load() below to match your actual schema
if they differ.

Integration (in ui/pos_window.py):

    from ui.low_stock_dialog import LowStockDialog

    # in _build_ui(), alongside Void Sale / Receive Stock:
    low_stock_button = QPushButton("Low Stock")
    low_stock_button.clicked.connect(self._open_low_stock)
    top_bar.addWidget(low_stock_button)

    # new method on POSWindow:
    def _open_low_stock(self) -> None:
        branch_id = self._current_branch_id()
        if not branch_id:
            return
        dialog = LowStockDialog(self._api, branch_id, self)
        dialog.exec()
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from api_client import ApiClient, ApiError


class LowStockDialog(QDialog):
    """Read-only table of items at or below their reorder level for
    the current branch, so staff can flag what needs restocking
    without waiting to notice it in the search list one item at a
    time. Always hits the API directly, since this is a "point in
    time" check rather than something checkout depends on.
    """

    def __init__(self, api: ApiClient, branch_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Low Stock Report")
        self.resize(560, 420)

        layout = QVBoxLayout()

        self.status_label = QLabel("Loading...")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Product", "SKU", "In Stock", "Reorder Level"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self.setLayout(layout)
        self._load(api, branch_id)

    def _load(self, api: ApiClient, branch_id: str) -> None:
        try:
            items = api.low_stock_report(branch_id)
        except ApiError as exc:
            self.status_label.setText(str(exc))
            return
        except Exception as exc:
            self.status_label.setText(f"Could not reach server: {exc}")
            return

        if not items:
            self.status_label.setText("Nothing is low on stock right now.")
        else:
            self.status_label.setText(f"{len(items)} item(s) at or below reorder level")

        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            name = f"{item.get('product_name', '')} -- {item.get('variant_name', '')}"
            sku = item.get("sku", "")
            qty = item.get("quantity_remaining", 0)
            reorder = item.get("reorder_level", 0)

            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(sku))
            qty_item = QTableWidgetItem(str(qty))
            if qty <= 0:
                qty_item.setForeground(Qt.red)
            self.table.setItem(row, 2, qty_item)
            self.table.setItem(row, 3, QTableWidgetItem(str(reorder)))
