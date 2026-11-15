"""Settings (all from environment / .env)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NOSANA_API_KEY = os.getenv("NOSANA_API_KEY", "")
NOSANA_BASE_URL = os.getenv("NOSANA_BASE_URL", "https://inference.nosana.com/v1").rstrip("/")
NOSANA_MODEL = os.getenv("NOSANA_MODEL", "qwen/qwen3.8-27b")
NOSANA_THINKING = os.getenv("NOSANA_THINKING", "0") == "1"

# LINE_MODE: fake | real | relay (see app/line.py). Defaults to fake when keys are missing.
LINE_MODE = os.getenv("LINE_MODE") or ("real" if LINE_CHANNEL_ACCESS_TOKEN else "fake")
ANALYZER = os.getenv("ANALYZER") or ("nosana" if NOSANA_API_KEY else "claude" if ANTHROPIC_API_KEY else "fake")
# ANALYZER=remote: an external worker (deploy/nosana_worker.py) pulls jobs and posts results.
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
REMOTE_TIMEOUT_SEC = int(os.getenv("REMOTE_TIMEOUT_SEC", "90"))  # then fall back to the mock
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")

BATCH_WINDOW_SEC = int(os.getenv("BATCH_WINDOW_SEC", "45"))
TRASH_DIGEST_HOUR = int(os.getenv("TRASH_DIGEST_HOUR", "19"))
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "9"))
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tokyo"))
