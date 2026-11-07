@echo off
setlocal
title YT Profile Manager - Install
cd /d "%~dp0"
echo ==========================================
echo    YT Profile Manager - one time install
echo ==========================================
echo.

set "PY="
where py >nul 2>nul && (py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12")
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not defined PY (
    echo [1/4] Python 3.12 not found. Installing it now...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        echo.
        echo Could not install Python automatically.
        echo Please install Python 3.12 from https://www.python.org/downloads/
        echo IMPORTANT: tick "Add python.exe to PATH" during install, then run Install.bat again.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Python found.
)

echo [2/4] Creating private environment...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || (echo Failed to create environment & pause & exit /b 1)
)

echo [3/4] Installing components (takes a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Install failed. Check internet and try again. & pause & exit /b 1)

echo [4/4] Downloading the anti-detect browser (about 100 MB)...
".venv\Scripts\python.exe" -m camoufox fetch || (echo Browser download failed. Run Install.bat again. & pause & exit /b 1)

echo.
echo Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\YT Profile Manager.lnk');" ^
 "$s.TargetPath='%~dp0.venv\Scripts\pythonw.exe';" ^
 "$s.Arguments='\"%~dp0main.py\"';" ^
 "$s.WorkingDirectory='%~dp0';" ^
 "$s.IconLocation='%SystemRoot%\System32\shell32.dll,13';" ^
 "$s.Save()"

echo.
if not exist "client_secret.json" (
    echo NOTE: client_secret.json is not in this folder yet.
    echo       Follow step 1 of the guide, put the file here, then open the app.
    echo.
)
echo Done! Open "YT Profile Manager" from your desktop.
pause
