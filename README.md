# YT Profile Manager

Office tool: one isolated anti-detect browser profile (Camoufox) per YouTube
channel, with its own fingerprint, proxy, cookies and logins. Everything is
encrypted and synced through the office Gmail's Google Drive, so any PC that
signs in with the same Gmail gets the same profiles, unchanged.

## Download

Go to **Releases** → download `YTProfileManager-windows.zip` from the newest
release → unzip → put `client_secret.json` next to `YTProfileManager.exe` →
run `YTProfileManager.exe`.

Full setup guide (Bangla): `GUIDE_BANGLA.txt`.

## How builds work

Every push to `main` runs `.github/workflows/build-windows.yml` on a Windows
machine: it builds the .exe with PyInstaller, bundles the Camoufox browser,
self-tests the result (real browser launch, restart with same fingerprint and
cookies), and publishes a new release.

## Run from source

`Install.bat` then `Start.bat` (Windows), or `python main.py`.
