"""
Configuration for the Public E-commerce Website / Customer Portal app.

This app is a THIN CLIENT over the existing cloud API (apps/api). It never
touches Postgres directly -- same principle as the desktop POS, just over
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
    # CHANGE THIS before deploying -- generate with: openssl rand -hex 32
    SESSION_SECRET: str = os.getenv("NICK_ECOMMERCE_SESSION_SECRET", "")

    SITE_NAME: str = "Nick Cosmetics"

    # Set NICK_HTTPS_ONLY=1 once the site is served over https, so the
    # session cookie is only ever sent over https. Leave unset on localhost.
    SESSION_HTTPS_ONLY: bool = os.getenv("NICK_HTTPS_ONLY", "").strip().lower() in ("1", "true", "yes")

    # Service account the storefront logs in as to call authenticated
    # endpoints (create customer, checkout) on a guest shopper's behalf.
    STOREFRONT_SERVICE_EMAIL: str = os.getenv("NICK_STOREFRONT_EMAIL", "storefront@nickcosmetics.com")
    STOREFRONT_SERVICE_PASSWORD: str = os.getenv("NICK_STOREFRONT_PASSWORD", "")
    STOREFRONT_BRANCH_ID: str = os.getenv("NICK_STOREFRONT_BRANCH_ID", "aac794fa-026c-463b-81e4-f7eff6cfb04c")

    # Contact details shown on the site (About/Contact pages, footer, and
    # the "we'll confirm delivery cost" message at checkout). Edit these
    # to your real details, or set the matching environment variables.
    CONTACT_PHONE: str = os.getenv("NICK_CONTACT_PHONE", "+234 800 000 0000")
    CONTACT_WHATSAPP: str = os.getenv("NICK_CONTACT_WHATSAPP", "+234 800 000 0000")
    CONTACT_EMAIL: str = os.getenv("NICK_CONTACT_EMAIL", "hello@nickcosmetics.com")
    CONTACT_ADDRESS: str = os.getenv("NICK_CONTACT_ADDRESS", "Lagos, Nigeria")

    # Payment details shown on the order confirmation page (patch 12b).
    # All optional and empty by default. The bank transfer block is shown
    # only when bank name, account name AND account number are all set.
    BANK_NAME: str = os.getenv("NICK_BANK_NAME", "").strip()
    BANK_ACCOUNT_NAME: str = os.getenv("NICK_BANK_ACCOUNT_NAME", "").strip()
    BANK_ACCOUNT_NUMBER: str = os.getenv("NICK_BANK_ACCOUNT_NUMBER", "").strip()
    PAYMENT_PHONE: str = os.getenv("NICK_PAYMENT_PHONE", "").strip()


_PLACEHOLDER_VALUES = {"", "dev-only-change-me", "ChangeThisSecurePassword123!"}


def _check_required_secrets(s):
    """Refuse to start with missing or placeholder secrets (never prints them)."""
    problems = []
    if s.SESSION_SECRET in _PLACEHOLDER_VALUES or len(s.SESSION_SECRET) < 32:
        problems.append(
            "NICK_ECOMMERCE_SESSION_SECRET is missing, a placeholder, or shorter than 32 characters."
        )
    if s.STOREFRONT_SERVICE_PASSWORD in _PLACEHOLDER_VALUES:
        problems.append("NICK_STOREFRONT_PASSWORD is missing or still a placeholder.")
    if problems:
        raise RuntimeError(
            "Storefront cannot start: " + " ".join(problems)
            + " Set the environment variables, then start the storefront again."
        )


settings = Settings()
_check_required_secrets(settings)