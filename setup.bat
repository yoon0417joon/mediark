@echo off
REM Gallery Search System — Windows 10/11 setup script
setlocal enabledelayedexpansion

echo === Gallery Search — setup (Windows) ===

REM ── 1. Python ───────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed.
    echo         Install 3.10+ from https://www.python.org/downloads/
    exit /b 1
)
python -c "import sys; v=sys.version_info; exit(0 if v.major==3 and v.minor>=10 else 1)" 2>nul
if %errorlevel% neq 0 (
    echo [WARN] Python 3.10 or newer is recommended.
)
echo [OK] Python is available

REM ── 2. ffmpeg ───────────────────────────────────────────────
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] ffmpeg not found. Video features will not work.
    echo        Install from https://ffmpeg.org/download.html and add to PATH.
)

REM ── 3. Virtual environment ───────────────────────────────────
if not exist ".venv" (
    echo [SETUP] Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

REM ── 4. Dependencies ─────────────────────────────────────────
echo [SETUP] Installing dependencies...
python -m pip install --upgrade pip -q
echo [SETUP] Windows — using PaddleOCR requirements
python -m pip install -r requirements-windows.txt -q

REM ── 5. RAM++ ───────────────────────────────────────────────
echo [SETUP] Installing RAM++ (recognize-anything)...
python -m pip install "git+https://github.com/xinyu1205/recognize-anything.git" -q

REM ── 6. .env ────────────────────────────────────────────────
if not exist ".env" (
    copy .env.example .env >nul
    echo [CONFIG] Created .env — edit if needed.
)

REM ── 7. Directories ─────────────────────────────────────────
if not exist "images_sample" mkdir images_sample
if not exist "thumbs" mkdir thumbs

echo.
echo === Setup complete ===
echo Run the server:
echo   .venv\Scripts\activate
echo   uvicorn server.main:app --host 127.0.0.1 --port 8000
echo.
echo Open: http://localhost:8000
