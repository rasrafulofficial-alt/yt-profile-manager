"""Puts the Camoufox anti-detect browser in place on this PC.

The .exe release ships the browser in a folder called `browser_bundle` next to
YTProfileManager.exe. Every PC gets the exact same browser build from it, which
keeps the fingerprint identical on every PC. On first start (or after an
update brings a new bundle) it is copied into Camoufox's own folder.

When running from source without a bundle, the browser is downloaded instead.
"""
import os
import shutil
from pathlib import Path

from . import config

_MARKER = ".ytpm_bundle_id"


def _install_dir() -> Path:
    from camoufox.pkgman import INSTALL_DIR
    return Path(INSTALL_DIR)


def _bundle_id() -> str | None:
    f = config.BROWSER_BUNDLE_DIR / "bundle_id.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


def needs_setup() -> bool:
    bid = _bundle_id()
    if bid:
        try:
            return (_install_dir() / _MARKER).read_text(encoding="utf-8").strip() != bid
        except OSError:
            return True
    from camoufox.pkgman import camoufox_path
    try:
        camoufox_path(download_if_missing=False)
        return False
    except Exception:
        return True


def setup() -> None:
    bid = _bundle_id()
    dest = _install_dir()
    if bid:
        src = config.BROWSER_BUNDLE_DIR / "camoufox"
        tmp = dest.with_name(dest.name + ".ytpm-new")
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp)
        if dest.exists():
            old = dest.with_name(dest.name + ".ytpm-old")
            shutil.rmtree(old, ignore_errors=True)
            os.replace(dest, old)
            shutil.rmtree(old, ignore_errors=True)
        os.replace(tmp, dest)
        (dest / _MARKER).write_text(bid, encoding="utf-8")
        return
    # No bundle (update package / running from source): download the browser
    # build this program version is pinned to (same as `camoufox fetch`).
    import sys
    from camoufox.__main__ import cli
    saved = sys.stdout, sys.stderr
    log = open(config.DATA_DIR / "browser_download.log", "a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    try:
        cli(["fetch"], standalone_mode=False)
    except SystemExit as e:
        if e.code not in (0, None):
            raise RuntimeError("Browser download failed. Check the internet and try again "
                               "(GitHub may limit downloads: wait an hour if many PCs "
                               "downloaded at once).") from e
    finally:
        sys.stdout, sys.stderr = saved
        log.close()
    if needs_setup():
        raise RuntimeError("Browser download did not finish. Please try again.")
