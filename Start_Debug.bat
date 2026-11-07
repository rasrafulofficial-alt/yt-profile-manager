@echo off
cd /d "%~dp0"
echo Running with a visible console so errors can be seen...
".venv\Scripts\python.exe" "%~dp0main.py"
pause
