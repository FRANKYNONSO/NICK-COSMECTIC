"""Shared logo helper for every downloadable PDF (Prestige / Nick Cosmetics).
Lives in apps\\api so it can be imported as `import pdf_branding`.
The images are in apps\\api\\assets."""
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "assets"


def _existing(name: str):
    p = _ASSETS / name
    return str(p) if p.is_file() else None


def mark_path():
    """Square-ish lipstick mark only (for small header boxes)."""
    return _existing("logo-mark-dark.png")


def lockup_path():
    """Wide logo: mark + PRESTIGE + NICK COSMETICS (for page headers)."""
    return _existing("logo-lockup-dark.png")
