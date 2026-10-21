"""App configuration, loaded from environment variables (with a local
.env override if present -- see .env.example for the full list).
"""

from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

# Local SQLite file for offline-first operation (see local_db/models.py).
# Lives next to this file by default so the app works out of the box
# with zero setup; override via LOCAL_DB_PATH in .env if you want it
# somewhere else (e.g. a shared/synced folder).
LOCAL_DB_PATH = os.getenv(
    "LOCAL_DB_PATH",
    str(Path(__file__).parent / "pos_local.db"),
)
