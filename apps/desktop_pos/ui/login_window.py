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
