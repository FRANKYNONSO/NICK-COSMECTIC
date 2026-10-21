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
from ui.theme import apply_theme


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

    apply_theme(app)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()