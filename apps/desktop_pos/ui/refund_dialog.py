"""
Refund / Return dialog -- lets staff look up a completed (synced) sale
by ID and process a full or partial refund via the existing
POST /api/v1/sales/{sale_id}/refund endpoint.

Distinct from VoidSaleDialog (which only cancels UNsynced local sales
and restores stock before they ever reach the server). This dialog is
for sales that have already gone through -- a genuine customer return
after the fact -- and always talks to the API directly rather than
the local cache, since refund is a server-authoritative action.

Assumes ApiClient has (or you'll add) a method like:

    def refund_sale(self, sale_id: str, amount_naira: float | None = None,
                     reason: str = "") -> dict:
        payload = {"reason": reason}
        if amount_naira is not None:
            payload["amount_kobo"] = int(round(amount_naira * 100))
        return self._request("POST", f"/sales/{sale_id}/refund", json=payload)

    def get_sale(self, sale_id: str) -> dict:
        return self._request("GET", f"/sales/{sale_id}")

If your ApiClient names these differently, adjust the two calls below.

Integration (in ui/pos_window.py):

    from ui.refund_dialog import RefundDialog

    # in _build_ui(), alongside Void Sale:
    refund_button = QPushButton("Refund Sale")
    refund_button.clicked.connect(self._open_refund_dialog)
    top_bar.addWidget(refund_button)

    # new method on POSWindow:
    def _open_refund_dialog(self) -> None:
        dialog = RefundDialog(self._api, self)
        dialog.exec()
"""

from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from api_client import ApiClient, ApiError


class RefundDialog(QDialog):
    """Two-step flow: look up a sale by ID to show its total and
    confirm it's the right one, then submit a refund for either the
    full amount or a partial amount with a reason.
    """

    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self._api = api
        self._sale = None

        self.setWindowTitle("Refund a Sale")
        self.resize(420, 380)

        layout = QVBoxLayout()

        layout.addWidget(QLabel("Sale ID (from receipt or 'My Sales'):"))
        lookup_row = QHBoxLayout()
        self.sale_id_input = QLineEdit()
        self.sale_id_input.setPlaceholderText("e.g. 3f1a9c2e-...")
        lookup_row.addWidget(self.sale_id_input)
        lookup_button = QPushButton("Look Up")
        lookup_button.clicked.connect(self._lookup_sale)
        lookup_row.addWidget(lookup_button)
        layout.addLayout(lookup_row)

        self.sale_info_label = QLabel("No sale looked up yet.")
        self.sale_info_label.setWordWrap(True)
        layout.addWidget(self.sale_info_label)

        layout.addWidget(QLabel("Refund amount (\u20a6) -- leave at 0 for full refund:"))
        self.amount_input = QDoubleSpinBox()
        self.amount_input.setRange(0, 10_000_000)
        self.amount_input.setDecimals(2)
        layout.addWidget(self.amount_input)

        layout.addWidget(QLabel("Reason:"))
        self.reason_input = QTextEdit()
        self.reason_input.setPlaceholderText("e.g. Customer returned item, wrong shade")
        self.reason_input.setFixedHeight(70)
        layout.addWidget(self.reason_input)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        self.refund_button = QPushButton("Process Refund")
        self.refund_button.setEnabled(False)
        self.refund_button.setStyleSheet(
            "background-color: #b00020; color: white; padding: 8px;"
        )
        self.refund_button.clicked.connect(self._submit_refund)
        button_row.addWidget(self.refund_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self.setLayout(layout)

    def _lookup_sale(self) -> None:
        sale_id = self.sale_id_input.text().strip()
        if not sale_id:
            self.status_label.setText("Enter a sale ID first.")
            return

        try:
            self._sale = self._api.get_sale(sale_id)
        except ApiError as exc:
            self.status_label.setText(str(exc))
            self.refund_button.setEnabled(False)
            return
        except Exception as exc:
            self.status_label.setText(f"Could not reach server: {exc}")
            self.refund_button.setEnabled(False)
            return

        total = self._sale.get("total_naira", 0.0)
        created = (self._sale.get("created_at") or "")[:16].replace("T", " ")
        already_refunded = self._sale.get("refunded_naira", 0.0)

        self.sale_info_label.setText(
            f"Sale from {created}\nTotal: \u20a6{total:,.2f}\n"
            f"Already refunded: \u20a6{already_refunded:,.2f}"
        )
        self.amount_input.setValue(0.0)
        self.status_label.setText("")
        self.refund_button.setEnabled(True)

    def _submit_refund(self) -> None:
        if self._sale is None:
            return

        sale_id = self._sale.get("id") or self.sale_id_input.text().strip()
        amount = self.amount_input.value() or None  # None -> full refund server-side
        reason = self.reason_input.toPlainText().strip()

        if not reason:
            self.status_label.setText("Enter a reason for the refund.")
            return

        confirm_amount = f"\u20a6{amount:,.2f}" if amount else "the full amount"
        confirm = QMessageBox.question(
            self, "Confirm refund", f"Refund {confirm_amount} for this sale?"
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            self._api.refund_sale(sale_id, amount_naira=amount, reason=reason)
        except ApiError as exc:
            self.status_label.setText(str(exc))
            return
        except Exception as exc:
            self.status_label.setText(f"Could not reach server: {exc}")
            return

        QMessageBox.information(self, "Refund processed", "The refund was recorded.")
        self.accept()
