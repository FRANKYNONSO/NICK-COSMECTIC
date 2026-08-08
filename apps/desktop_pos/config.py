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
