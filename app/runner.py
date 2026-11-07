"""Runs ONE profile in its own process.

Started by the main window as:  python main.py --run <profile_id> [--force]
The vault key is passed on stdin (first line, hex). Progress is written to
stdout as JSON lines so the main window can show it. Sending the line STOP on
stdin closes the browser cleanly (data is still uploaded).
"""
import json
import sys
import threading
import time
import traceback

from . import config, drive, profiles


def emit(kind, msg="", **extra):
    try:
        sys.stdout.write(json.dumps({"kind": kind, "msg": msg, **extra}) + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{profiles.now_iso()} [{kind}] {msg}\n")
    except Exception:
        pass


def run(pid: str, force: bool) -> int:
    key_hex = sys.stdin.readline().strip()
    key = bytes.fromhex(key_hex)
    creds = drive.load_credentials()
    if not creds:
        emit("error", "Not signed in to Google. Please sign in again.")
        return 2
    store = drive.DriveStore(creds)
    svc = profiles.ProfileService(store, key)
    settings = config.load_settings()

    emit("status", "Checking if the profile is free...")
    try:
        lock_id = svc.acquire_lock(pid, force=force)
    except profiles.ProfileLocked as e:
        emit("locked", f"This profile is already open on {e.pc}", pc=e.pc)
        return 3

    flag = svc.local_dir(pid) / "running.flag"
    stop_event = threading.Event()
    closed = threading.Event()

    def heartbeat_loop():
        hb_store = profiles.ProfileService(drive.DriveStore(drive.load_credentials() or creds), key)
        while not closed.wait(config.HEARTBEAT_SECONDS):
            try:
                hb_store.heartbeat(lock_id)
            except Exception as ex:
                emit("warn", f"Heartbeat failed: {ex}")

    def stdin_loop():
        for line in sys.stdin:
            if line.strip() == "STOP":
                stop_event.set()
                return
        # stdin closed = main window gone; keep the browser running

    try:
        svc.local_dir(pid).mkdir(parents=True, exist_ok=True)
        flag.write_text(config.PC_NAME)
        st = svc.local_state(pid)
        prof = svc.fetch_remote_meta(pid)
        if st.get("dirty") and int(st.get("data_rev", -1)) == int(prof.get("data_rev", 0)):
            emit("status", "Uploading changes left from last time...")
            svc.push_data(pid, log=lambda m: emit("status", m))
            st = svc.local_state(pid)
            st.pop("dirty", None)
            svc._write_local(pid, "state.json", st)
        prof = svc.pull_data_if_needed(pid, log=lambda m: emit("status", m))

        threading.Thread(target=heartbeat_loop, daemon=True).start()
        threading.Thread(target=stdin_loop, daemon=True).start()

        launch_browser(svc, prof, settings, stop_event)
    except Exception as e:
        emit("error", f"{e}", trace=traceback.format_exc())
        closed.set()
        try:
            svc.release_lock(lock_id)
        except Exception:
            pass
        flag.unlink(missing_ok=True)
        return 1

    # ---- browser closed: upload
    closed.set()
    ok = False
    for attempt in range(3):
        try:
            svc.push_data(pid, log=lambda m: emit("status", m))
            ok = True
            break
        except Exception as e:
            emit("warn", f"Upload failed ({e}), retrying...")
            time.sleep(5 * (attempt + 1))
    if not ok:
        st = svc.local_state(pid)
        st["dirty"] = True
        svc._write_local(pid, "state.json", st)
        emit("error", "Could not upload to Google Drive. Data is safe on this PC and will "
                      "upload next time you open this profile on THIS PC.")
    try:
        svc.release_lock(lock_id)
    except Exception:
        pass
    flag.unlink(missing_ok=True)
    if ok:
        emit("done", "Saved to Google Drive")
    return 0 if ok else 1


def build_launch_options(prof, settings, user_data_dir, headless=False) -> dict:
    from camoufox.fingerprints import Screen

    fp = prof["fingerprint"]
    scr = fp.get("screen") or {}
    proxy = profiles.parse_proxy(prof.get("proxy", ""))
    opts = dict(
        persistent_context=True,
        user_data_dir=str(user_data_dir),
        fingerprint=fp,
        os=prof.get("os", "windows"),
        headless=headless,
        i_know_what_im_doing=True,
        # Fixed screen bound = same screen on every PC
        screen=Screen(max_width=scr.get("width") or settings["screen_max_width"],
                      max_height=scr.get("height") or settings["screen_max_height"]),
        geoip=True,  # timezone / language / location follow the proxy IP
        enable_cache=True,
    )
    if proxy:
        opts["proxy"] = proxy
    return opts


def launch_browser(svc, prof, settings, stop_event):
    from camoufox.sync_api import Camoufox
    from playwright.sync_api import TimeoutError as PWTimeout
    from . import browser_setup

    if browser_setup.needs_setup():
        raise RuntimeError("The browser is still being prepared on this PC. "
                           "Wait for 'Browser ready' and try again.")
    start_url = prof.get("start_url") or settings.get("start_url") or "https://studio.youtube.com"
    opts = build_launch_options(prof, settings, svc.browser_dir(prof["id"]))

    emit("status", "Starting browser...")
    try:
        cm = Camoufox(**opts)
        context = cm.__enter__()
    except Exception as e:
        if "geoip" in str(e).lower() or "ip" in type(e).__name__.lower():
            raise RuntimeError(f"Could not reach the internet through the proxy: {e}")
        raise

    try:
        emit("running", "Browser is open")
        page = context.pages[0] if context.pages else context.new_page()
        try:
            if page.url in ("", "about:blank"):
                page.goto(start_url, timeout=60000)
        except Exception as e:
            emit("warn", f"Start page did not load: {e}")

        while not stop_event.is_set():
            try:
                context.wait_for_event("close", timeout=1000)
                break  # user closed the browser window
            except PWTimeout:
                continue
            except Exception:
                break
        emit("status", "Closing browser...")
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass
