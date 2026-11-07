"""Paths and constants for YT Profile Manager."""
import json
import os
import platform
import sys
from pathlib import Path

APP_NAME = "YT Profile Manager"
APP_VERSION = "1.0.0"

# Folder where the program itself lives (client_secret.json goes here)
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:  # running as YTProfileManager.exe
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

# Browser shipped next to the .exe (copied into place on first start)
BROWSER_BUNDLE_DIR = APP_DIR / "browser_bundle"

if sys.platform == "win32":
    _base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
else:
    _base = Path.home() / ".local" / "share"

DATA_DIR = _base / "YTProfileManager"
PROFILES_DIR = DATA_DIR / "profiles"
TOKEN_FILE = DATA_DIR / "google_token.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "app.log"

CLIENT_SECRET_FILE = APP_DIR / "client_secret.json"
# Copy kept in the app's data folder (always writable, survives app updates)
SAVED_CLIENT_SECRET = DATA_DIR / "client_secret.json"


class BadClientSecret(Exception):
    pass


def check_client_secret(path) -> None:
    """Raise BadClientSecret with a clear message if the file is not a Desktop-app client."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        raise BadClientSecret("This is not a valid client_secret JSON file.")
    if "web" in data:
        raise BadClientSecret("This client was created as 'Web application'. In Google Cloud, "
                              "create a new client with Application type = 'Desktop app'.")
    if "installed" not in data or not data["installed"].get("client_id"):
        raise BadClientSecret("This is not a Google OAuth client file (Desktop app).")


def _candidates():
    yield CLIENT_SECRET_FILE
    yield from sorted(APP_DIR.glob("client_secret*.json"))  # long Google name, or .json.json
    yield SAVED_CLIENT_SECRET
    downloads = Path.home() / "Downloads"
    try:
        yield from sorted(downloads.glob("client_secret*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass


def find_client_secret():
    """Path of a usable client secret, or None. A file found elsewhere is
    copied into the app's data folder so it keeps working after updates."""
    import shutil
    for p in _candidates():
        try:
            if not p.is_file():
                continue
            check_client_secret(p)
        except (BadClientSecret, OSError):
            continue
        if p != SAVED_CLIENT_SECRET:
            try:
                shutil.copyfile(p, SAVED_CLIENT_SECRET)
            except OSError:
                return p
        return SAVED_CLIENT_SECRET
    return None


def import_client_secret(path) -> None:
    import shutil
    check_client_secret(path)
    shutil.copyfile(path, SAVED_CLIENT_SECRET)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.appdata",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# A lock older than this (no heartbeat) is treated as stale (PC crashed / offline)
LOCK_STALE_SECONDS = 5 * 60
HEARTBEAT_SECONDS = 60

PC_NAME = platform.node() or "Unknown-PC"

DEFAULT_SETTINGS = {
    "start_url": "https://studio.youtube.com",
    "fingerprint_os": "windows",
    "screen_max_width": 1920,
    "screen_max_height": 1080,
}

for _d in (DATA_DIR, PROFILES_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return s


def save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
