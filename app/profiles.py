"""Profiles: local storage, Google Drive sync, locks and proxy helpers."""
import io
import json
import os
import shutil
import time
import uuid
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from . import config, crypto

VAULT_NAME = "vault.json"

# Things inside the Firefox profile folder that are never synced (big & useless)
_SKIP_DIRS = {
    "cache2", "startupCache", "cache", "crashes", "minidumps", "datareporting",
    "saved-telemetry-pings", "shader-cache", "safebrowsing", "thumbnails",
    "OfflineCache", "jumpListCache", "settings", "security_state",
}
_SKIP_DIR_PREFIXES = ("gmp",)
_SKIP_FILES = {"parent.lock", "lock", ".parentlock", "Telemetry.ShutdownTime.txt"}
_SKIP_SUFFIXES = (".tmp", ".log")


class ProfileLocked(Exception):
    def __init__(self, pc, age_seconds):
        self.pc, self.age = pc, age_seconds
        super().__init__(f"Profile is open on {pc}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ------------------------------------------------------------------ proxy

def parse_proxy(text: str) -> dict | None:
    """Accepts:
        http://user:pass@host:port   socks5://host:port
        host:port                    host:port:user:pass
        user:pass@host:port
    Returns Playwright proxy dict or None. Raises ValueError if invalid."""
    t = (text or "").strip()
    if not t:
        return None
    scheme = "http"
    if "://" in t:
        scheme, t = t.split("://", 1)
        scheme = scheme.lower()
        if scheme not in ("http", "https", "socks5", "socks4"):
            raise ValueError(f"Unknown proxy type: {scheme}")
    user = pwd = None
    if "@" in t:
        cred, t = t.rsplit("@", 1)
        if ":" in cred:
            user, pwd = cred.split(":", 1)
        else:
            user = cred
        parts = t.split(":")
        if len(parts) != 2:
            raise ValueError("Proxy must look like host:port")
        host, port = parts
    else:
        parts = t.split(":")
        if len(parts) == 2:
            host, port = parts
        elif len(parts) == 4:
            host, port, user, pwd = parts
        else:
            raise ValueError("Proxy must look like host:port or host:port:user:pass")
    if not port.isdigit() or not (0 < int(port) < 65536):
        raise ValueError("Proxy port must be a number")
    if not host:
        raise ValueError("Proxy host is empty")
    d = {"server": f"{scheme}://{host}:{port}"}
    if user:
        d["username"] = user
        d["password"] = pwd or ""
    return d


def proxy_url(p: dict | None) -> str | None:
    if not p:
        return None
    if "username" in p:
        scheme, rest = p["server"].split("://", 1)
        from urllib.parse import quote
        return f"{scheme}://{quote(p['username'], safe='')}:{quote(p.get('password', ''), safe='')}@{rest}"
    return p["server"]


def check_proxy(text: str, timeout=15) -> dict:
    """Returns {'ip','country','city','timezone'} through the proxy."""
    import requests
    p = parse_proxy(text)
    url = proxy_url(p)
    if url and url.startswith("socks5://"):
        url = "socks5h://" + url[len("socks5://"):]
    proxies = {"http": url, "https": url} if url else None
    r = requests.get("http://ip-api.com/json/?fields=status,message,query,country,city,timezone",
                     proxies=proxies, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "success":
        raise RuntimeError(j.get("message", "lookup failed"))
    return {"ip": j["query"], "country": j.get("country", ""), "city": j.get("city", ""),
            "timezone": j.get("timezone", "")}


# ------------------------------------------------------------ fingerprint

def generate_fingerprint(os_name: str, max_w: int, max_h: int) -> dict:
    from camoufox.fingerprints import Screen, generate_fingerprint as gen
    fp = gen(os=os_name, screen=Screen(max_width=max_w, max_height=max_h))
    json.dumps(fp)  # make sure it can be saved
    return fp


def fingerprint_summary(fp: dict) -> str:
    try:
        nav = fp.get("navigator", {})
        scr = fp.get("screen", {})
        gpu = (fp.get("webgl") or {}).get("renderer") or (fp.get("gpuInfo") or {}).get("renderer") or ""
        return (f"{nav.get('platform', '?')} | {scr.get('width', '?')}x{scr.get('height', '?')}"
                + (f" | {str(gpu)[:60]}" if gpu else ""))
    except Exception:
        return "-"


# ---------------------------------------------------------- pack / unpack

def _skip(rel_parts: tuple) -> bool:
    for part in rel_parts[:-1]:
        if part in _SKIP_DIRS or part.startswith(_SKIP_DIR_PREFIXES):
            return True
    name = rel_parts[-1]
    return name in _SKIP_FILES or name.endswith(_SKIP_SUFFIXES)


def pack_dir(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        if folder.exists():
            for p in folder.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(folder)
                if _skip(rel.parts):
                    continue
                try:
                    z.write(p, rel.as_posix())
                except (PermissionError, OSError):
                    pass
    return buf.getvalue()


def unpack_dir(data: bytes, folder: Path) -> None:
    tmp = folder.with_name(folder.name + ".incoming")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.infolist():
            target = (tmp / m.filename).resolve()
            if not str(target).startswith(str(tmp.resolve())):
                continue  # zip-slip protection
            z.extract(m, tmp)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    tmp.rename(folder)


# ------------------------------------------------------------- service

class ProfileService:
    """All profile operations. `store` is a DriveStore, `key` the vault key."""

    def __init__(self, store, key: bytes):
        self.store = store
        self.key = key

    # ---- local paths
    @staticmethod
    def local_dir(pid: str) -> Path:
        return config.PROFILES_DIR / pid

    def browser_dir(self, pid: str) -> Path:
        return self.local_dir(pid) / "browser"

    def _read_local(self, pid, name, default=None):
        try:
            return json.loads((self.local_dir(pid) / name).read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_local(self, pid, name, obj):
        d = self.local_dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (name + ".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        os.replace(tmp, d / name)

    def local_profile(self, pid) -> dict | None:
        return self._read_local(pid, "profile.json")

    def local_state(self, pid) -> dict:
        return self._read_local(pid, "state.json", {})

    # ---- meta (settings + fingerprint)
    def _encode_meta(self, prof: dict) -> bytes:
        return crypto.encrypt(self.key, zlib.compress(crypto.dumps(prof), 6))

    def _decode_meta(self, blob: bytes) -> dict:
        return json.loads(zlib.decompress(crypto.decrypt(self.key, blob)))

    def push_meta(self, prof: dict) -> None:
        prof["updated_at"] = now_iso()
        info = self.store.write(f"p_{prof['id']}.meta", self._encode_meta(prof))
        self._write_local(prof["id"], "profile.json", prof)
        st = self.local_state(prof["id"])
        st["meta_modified"] = info.get("modifiedTime")
        self._write_local(prof["id"], "state.json", st)

    def fetch_remote_meta(self, pid) -> dict | None:
        blob = self.store.read(f"p_{pid}.meta")
        return self._decode_meta(blob) if blob else None

    # ---- list / sync
    def sync_list(self, progress=None) -> list[dict]:
        """Bring the local list in line with Drive. Returns all profiles."""
        files = self.store.list("p_")
        metas = {f["name"][2:-5]: f for f in files if f["name"].endswith(".meta")}
        sizes = {f["name"][2:-5]: int(f.get("size") or 0) for f in files if f["name"].endswith(".data")}
        result = []
        for i, (pid, f) in enumerate(metas.items()):
            if progress:
                progress(i + 1, len(metas))
            st = self.local_state(pid)
            prof = self.local_profile(pid)
            if prof is None or st.get("meta_modified") != f["modifiedTime"]:
                try:
                    prof = self._decode_meta(self.store.read(f["name"], f["id"]))
                except crypto.WrongPassword:
                    continue
                self._write_local(pid, "profile.json", prof)
                st["meta_modified"] = f["modifiedTime"]
                self._write_local(pid, "state.json", st)
            prof["_data_size"] = sizes.get(pid, 0)
            result.append(prof)
        # profiles deleted on another PC
        for d in config.PROFILES_DIR.iterdir():
            if d.is_dir() and d.name not in metas and (d / "profile.json").exists():
                if not (d / "running.flag").exists():
                    shutil.rmtree(d, ignore_errors=True)
        result.sort(key=lambda p: (p.get("group", "").lower(), p.get("name", "").lower()))
        return result

    def create(self, name, group="", notes="", proxy="", start_url="", fp_os="windows",
               max_w=1920, max_h=1080) -> dict:
        if proxy:
            parse_proxy(proxy)  # validate
        prof = {
            "id": uuid.uuid4().hex[:12],
            "name": name.strip() or "Unnamed",
            "group": group.strip(),
            "notes": notes,
            "proxy": proxy.strip(),
            "start_url": start_url.strip(),
            "os": fp_os,
            "created_at": now_iso(),
            "data_rev": 0,
            "last_used_at": "",
            "last_used_pc": "",
            "fingerprint": generate_fingerprint(fp_os, max_w, max_h),
        }
        self.push_meta(prof)
        st = self.local_state(prof["id"])
        st["data_rev"] = 0
        self._write_local(prof["id"], "state.json", st)
        return prof

    def update_settings(self, pid, **changes) -> dict:
        prof = self.fetch_remote_meta(pid) or self.local_profile(pid)
        if "proxy" in changes and changes["proxy"]:
            parse_proxy(changes["proxy"])
        prof.update(changes)
        self.push_meta(prof)
        return prof

    def regenerate_fingerprint(self, pid, max_w, max_h) -> dict:
        prof = self.fetch_remote_meta(pid)
        prof["fingerprint"] = generate_fingerprint(prof.get("os", "windows"), max_w, max_h)
        self.push_meta(prof)
        return prof

    def delete(self, pid) -> None:
        for suffix in (".meta", ".data"):
            self.store.delete(f"p_{pid}{suffix}")
        for f in self.store.list(f"lock_{pid}_"):
            self.store.delete_id(f["id"])
        shutil.rmtree(self.local_dir(pid), ignore_errors=True)

    # ---- locks
    def lock_status(self, pid) -> dict | None:
        """Returns {'pc','age'} of a live lock, or None."""
        now = time.time()
        for f in sorted(self.store.list(f"lock_{pid}_"), key=lambda f: f["createdTime"]):
            age = now - parse_iso(f["modifiedTime"])
            if age < config.LOCK_STALE_SECONDS:
                info = json.loads(self.store.read(f["name"], f["id"]) or b"{}")
                return {"pc": info.get("pc", "?"), "age": age, "id": f["id"]}
        return None

    def all_locks(self) -> dict:
        """pid -> pc for every live lock (one Drive call, no content reads)."""
        now = time.time()
        out = {}
        for f in self.store.list("lock_"):
            parts = f["name"].split("_")
            if len(parts) >= 3 and now - parse_iso(f["modifiedTime"]) < config.LOCK_STALE_SECONDS:
                out.setdefault(parts[1], f)
        return out

    def acquire_lock(self, pid, force=False) -> str:
        now = time.time()
        existing = self.store.list(f"lock_{pid}_")
        for f in existing:
            age = now - parse_iso(f["modifiedTime"])
            if age < config.LOCK_STALE_SECONDS and not force:
                info = json.loads(self.store.read(f["name"], f["id"]) or b"{}")
                if info.get("pc") != config.PC_NAME:
                    raise ProfileLocked(info.get("pc", "?"), age)
            self.store.delete_id(f["id"])
        token = uuid.uuid4().hex[:8]
        body = json.dumps({"pc": config.PC_NAME, "since": now_iso()}).encode()
        mine = self.store.create_new(f"lock_{pid}_{token}.json", body)
        time.sleep(2.0)
        # if two PCs raced, the oldest lock wins
        locks = sorted(self.store.list(f"lock_{pid}_"), key=lambda f: (f["createdTime"], f["name"]))
        if locks and locks[0]["id"] != mine["id"]:
            self.store.delete_id(mine["id"])
            winner = json.loads(self.store.read(locks[0]["name"], locks[0]["id"]) or b"{}")
            raise ProfileLocked(winner.get("pc", "?"), 0)
        return mine["id"]

    def heartbeat(self, lock_id) -> None:
        self.store.update_id(lock_id, json.dumps({"pc": config.PC_NAME, "beat": now_iso()}).encode())

    def release_lock(self, lock_id) -> None:
        self.store.delete_id(lock_id)

    def force_unlock(self, pid) -> None:
        for f in self.store.list(f"lock_{pid}_"):
            self.store.delete_id(f["id"])

    # ---- browser data
    def pull_data_if_needed(self, pid, log=print) -> dict:
        """Make the local copy match Drive. Returns fresh profile meta."""
        prof = self.fetch_remote_meta(pid)
        if prof is None:
            raise RuntimeError("Profile not found on Google Drive (deleted?)")
        self._write_local(pid, "profile.json", prof)
        st = self.local_state(pid)
        remote_rev = int(prof.get("data_rev", 0))
        local_rev = int(st.get("data_rev", -1))
        bdir = self.browser_dir(pid)
        if remote_rev > 0 and (remote_rev != local_rev or not bdir.exists()):
            log("Downloading profile data from Google Drive...")
            blob = self.store.read(f"p_{pid}.data")
            if blob:
                unpack_dir(crypto.decrypt(self.key, blob), bdir)
            st["data_rev"] = remote_rev
            st.pop("dirty", None)
            self._write_local(pid, "state.json", st)
        bdir.mkdir(parents=True, exist_ok=True)
        return prof

    def push_data(self, pid, log=print) -> None:
        log("Packing profile data...")
        raw = pack_dir(self.browser_dir(pid))
        log(f"Uploading {len(raw) / 1024 / 1024:.1f} MB to Google Drive...")
        self.store.write(f"p_{pid}.data", crypto.encrypt(self.key, raw))
        prof = self.fetch_remote_meta(pid) or self.local_profile(pid)
        prof["data_rev"] = int(prof.get("data_rev", 0)) + 1
        prof["last_used_at"] = now_iso()
        prof["last_used_pc"] = config.PC_NAME
        self.push_meta(prof)
        st = self.local_state(pid)
        st["data_rev"] = prof["data_rev"]
        self._write_local(pid, "state.json", st)


# ------------------------------------------------------------- vault

def load_vault(store):
    return store.read_json(VAULT_NAME)


def create_vault(store, password: str) -> bytes:
    if load_vault(store):
        raise RuntimeError("A master password already exists for this Google account")
    vault, key = crypto.new_vault(password)
    store.write_json(VAULT_NAME, vault)
    return key
