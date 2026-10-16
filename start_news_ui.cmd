@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment was not found: .venv\Scripts\python.exe
  pause
  exit /b 1
)

set PYTHONUTF8=1
".venv\Scripts\python.exe" "src\news_ui.py"

if errorlevel 1 (
  echo.
  echo [ERROR] Nigeria News UI stopped unexpectedly.
  pause
)
