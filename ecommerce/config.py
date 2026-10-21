"""
Configuration for the Public E-commerce Website / Customer Portal app.

This app is a THIN CLIENT over the existing cloud API (apps/api). It never
touches Postgres directly — same principle as the desktop POS, just over
HTTP instead of SQLite+outbox.
"""
import os


class Settings:
    # Base URL of your existing FastAPI cloud backend (apps/api)
    API_BASE_URL: str = os.getenv("NICK_API_BASE_URL", "http://localhost:8000")

    # Which branch's stock counts as "online" stock. If you sell online
    # from a single warehouse/branch, set this to that branch's ID.
    # If you want online stock to be the SUM across all branches, leave
    # this None and we aggregate instead (see api_client.get_products).
    ONLINE_BRANCH_ID: int | None = None

    # Session cookie secret for the shopping cart (server-side session).
    # CHANGE THIS before deploying — generate with: openssl rand -hex 32
    SESSION_SECRET: str = os.getenv("NICK_ECOMMERCE_SESSION_SECRET", "dev-only-change-me")

    SITE_NAME: str = "Nick Cosmetics"


settings = Settings()
