import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
SAMMY_HOME = Path(os.environ.get("SAMMY_HOME", Path.home() / ".sammy"))
SAMMY_HOME.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("SAMMY_DB", SAMMY_HOME / "sammy.sqlite"))
KEY_PATH = Path(os.environ.get("SAMMY_KEY", SAMMY_HOME / "sammy.key"))
UPLOAD_DIR = SAMMY_HOME / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
HOST = os.environ.get("SAMMY_HOST", "127.0.0.1")
PORT = int(os.environ.get("SAMMY_PORT", "3131"))
PUBLIC_URL = os.environ.get("SAMMY_PUBLIC_URL", f"http://localhost:{PORT}").rstrip("/")
OAUTH_REDIRECT_URI = os.environ.get("SAMMY_OAUTH_REDIRECT_URI", f"{PUBLIC_URL}/api/oauth/callback").rstrip("/")


def dist_dir() -> Path:
    return APP_ROOT / "frontend" / "dist"
