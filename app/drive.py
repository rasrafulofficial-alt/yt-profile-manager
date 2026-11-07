"""Google sign-in + Google Drive "appDataFolder" storage.

appDataFolder is a hidden folder inside the user's own Google Drive that only
this app (this Google Cloud OAuth client) can see. It does not show up in the
normal Drive file list and counts against the normal Drive quota.
"""
import io
import json
import threading
import time

from . import config

_lock = threading.Lock()


class NotSignedIn(Exception):
    pass


class MissingClientSecret(Exception):
    pass


# ---------------------------------------------------------------- sign-in

def load_credentials():
    """Return saved, valid credentials or None."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not config.TOKEN_FILE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(config.TOKEN_FILE), config.GOOGLE_SCOPES)
    except Exception:
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            with _lock:
                creds.refresh(Request())
                config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            return None
    return None


def sign_in_interactive():
    """Opens the normal browser for 'Sign in with Google'."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not config.CLIENT_SECRET_FILE.exists():
        raise MissingClientSecret(str(config.CLIENT_SECRET_FILE))
    flow = InstalledAppFlow.from_client_secrets_file(str(config.CLIENT_SECRET_FILE), config.GOOGLE_SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="select_account",
        success_message="Login successful. You can close this tab and go back to YT Profile Manager.",
        open_browser=True,
    )
    config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def sign_out():
    try:
        config.TOKEN_FILE.unlink()
    except FileNotFoundError:
        pass


def get_email(creds) -> str:
    from googleapiclient.discovery import build
    svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
    return svc.userinfo().get().execute().get("email", "")


# ---------------------------------------------------------------- storage

class DriveStore:
    """Flat key/value file storage inside appDataFolder."""

    def __init__(self, creds):
        self.creds = creds
        self._svc = None
        self._ids = {}  # name -> file id cache

    @property
    def svc(self):
        if self._svc is None:
            from googleapiclient.discovery import build
            self._svc = build("drive", "v3", credentials=self.creds, cache_discovery=False)
        return self._svc

    def _retry(self, fn, tries=4):
        last = None
        for i in range(tries):
            try:
                return fn()
            except Exception as e:  # network blips / 5xx / rate limit
                last = e
                status = getattr(getattr(e, "resp", None), "status", None)
                if status in (400, 401, 404):
                    raise
                time.sleep(1.5 * (i + 1))
        raise last

    def list(self, prefix: str = "") -> list[dict]:
        files, token = [], None
        q = f"name contains '{prefix}' and trashed = false" if prefix else "trashed = false"
        while True:
            resp = self._retry(lambda: self.svc.files().list(
                spaces="appDataFolder", q=q, pageSize=1000, pageToken=token,
                fields="nextPageToken, files(id, name, modifiedTime, createdTime, size)",
            ).execute())
            files.extend(f for f in resp.get("files", []) if f["name"].startswith(prefix))
            token = resp.get("nextPageToken")
            if not token:
                break
        for f in files:
            self._ids[f["name"]] = f["id"]
        return files

    def _find_id(self, name: str):
        if name in self._ids:
            return self._ids[name]
        files = self.list(name)
        exact = [f for f in files if f["name"] == name]
        return exact[0]["id"] if exact else None

    def read(self, name: str, file_id: str | None = None) -> bytes | None:
        from googleapiclient.http import MediaIoBaseDownload
        fid = file_id or self._find_id(name)
        if not fid:
            return None

        def _dl():
            buf = io.BytesIO()
            req = self.svc.files().get_media(fileId=fid)
            dl = MediaIoBaseDownload(buf, req, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk()
            return buf.getvalue()

        try:
            return self._retry(_dl)
        except Exception as e:
            if getattr(getattr(e, "resp", None), "status", None) == 404:
                self._ids.pop(name, None)
                return None
            raise

    def write(self, name: str, data: bytes) -> dict:
        from googleapiclient.http import MediaIoBaseUpload
        fid = self._find_id(name)

        def _up():
            media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/octet-stream",
                                      resumable=len(data) > 5 * 1024 * 1024)
            fields = "id, name, modifiedTime, createdTime"
            if fid:
                return self.svc.files().update(fileId=fid, media_body=media, fields=fields).execute()
            return self.svc.files().create(
                body={"name": name, "parents": ["appDataFolder"]},
                media_body=media, fields=fields).execute()

        meta = self._retry(_up)
        self._ids[name] = meta["id"]
        return meta

    def create_new(self, name: str, data: bytes) -> dict:
        """Always creates a new file (used for locks)."""
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/octet-stream")
        return self._retry(lambda: self.svc.files().create(
            body={"name": name, "parents": ["appDataFolder"]}, media_body=media,
            fields="id, name, modifiedTime, createdTime").execute())

    def update_id(self, file_id: str, data: bytes) -> None:
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/octet-stream")
        self._retry(lambda: self.svc.files().update(fileId=file_id, media_body=media).execute())

    def delete(self, name: str) -> None:
        for f in self.list(name):
            if f["name"] == name:
                self.delete_id(f["id"])
        self._ids.pop(name, None)

    def delete_id(self, file_id: str) -> None:
        try:
            self._retry(lambda: self.svc.files().delete(fileId=file_id).execute())
        except Exception as e:
            if getattr(getattr(e, "resp", None), "status", None) != 404:
                raise
        for k, v in list(self._ids.items()):
            if v == file_id:
                self._ids.pop(k, None)

    def read_json(self, name: str):
        b = self.read(name)
        return json.loads(b) if b else None

    def write_json(self, name: str, obj) -> None:
        self.write(name, json.dumps(obj, indent=1).encode("utf-8"))
