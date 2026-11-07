"""Build check, run on the GitHub Windows machine after the .exe is built:

    YTProfileManager.exe --selftest <report.txt>

1. copies the bundled browser into place
2. creates a fingerprint and launches the real browser with a persistent
   profile, twice, checking the fingerprint and cookies survive a restart
3. starts the .exe as a child process and reads its output (how the app
   talks to running profiles)
"""
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from . import browser_setup, config, profiles


def run(report_path: str) -> int:
    out = []

    def log(msg):
        out.append(str(msg))
        Path(report_path).write_text("\n".join(out), encoding="utf-8")

    ok = True
    try:
        log(f"APP_DIR={config.APP_DIR} FROZEN={config.FROZEN}")
        log(f"needs_setup={browser_setup.needs_setup()}")
        browser_setup.setup()
        log(f"after setup needs_setup={browser_setup.needs_setup()}")

        from camoufox.sync_api import Camoufox
        from .runner import build_launch_options

        settings = config.load_settings()
        prof = {"id": "selftest", "os": "windows", "proxy": "",
                "fingerprint": profiles.generate_fingerprint("windows", 1920, 1080)}
        log("fingerprint: " + profiles.fingerprint_summary(prof["fingerprint"]))
        udd = Path(tempfile.mkdtemp()) / "browser"

        seen = []
        for round_ in (1, 2):
            opts = build_launch_options(prof, settings, udd, headless=True)
            with Camoufox(**opts) as ctx:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto("https://example.com", timeout=60000)
                if round_ == 1:
                    page.evaluate("""() => {
                        document.cookie = 'ytpm=keep-me; max-age=31536000; path=/; SameSite=Lax; Secure';
                        localStorage.setItem('ytpm', 'keep-me-too');
                    }""")
                    log("set in page: " + page.evaluate("() => document.cookie"))
                info = page.evaluate("""() => ({ua: navigator.userAgent,
                    w: screen.width, h: screen.height,
                    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    lang: navigator.language,
                    gl: (() => { try { const c = document.createElement('canvas').getContext('webgl');
                        const e = c.getExtension('WEBGL_debug_renderer_info');
                        return c.getParameter(e.UNMASKED_RENDERER_WEBGL); } catch (x) { return 'n/a'; } })()
                    })""")
                info["cookie"] = page.evaluate(
                    "() => (document.cookie.match(/ytpm=([^;]+)/) || [])[1] || null")
                info["local_storage"] = page.evaluate("() => localStorage.getItem('ytpm')")
                log(f"launch {round_}: {json.dumps(info)}")
                seen.append(info)
        if seen[1]["cookie"] != "keep-me":
            ok = False
            log("FAIL: cookie did not survive restart")
        if seen[1]["local_storage"] != "keep-me-too":
            ok = False
            log("FAIL: localStorage did not survive restart")
        files = sorted(str(p.relative_to(udd)) for p in udd.rglob("*") if p.is_file())
        log(f"profile files ({len(files)}): " + ", ".join(f for f in files if "cookie" in f or "storage" in f)[:600])
        for k in ("ua", "w", "h", "gl"):
            if seen[0][k] != seen[1][k]:
                ok = False
                log(f"FAIL: {k} changed between launches")

        # child process + stdout pipe
        cmd = [sys.executable] + ([] if config.FROZEN else [str(config.APP_DIR / "main.py")]) + ["--echo"]
        r = subprocess.run(cmd, input="ping\n", capture_output=True, text=True, timeout=120)
        log(f"child stdout={r.stdout.strip()!r} code={r.returncode}")
        if "pong:ping" not in r.stdout:
            ok = False
            log("FAIL: child process output not received")
    except Exception:
        ok = False
        log("EXCEPTION:\n" + traceback.format_exc())
    log("RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1
