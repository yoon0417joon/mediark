#!/usr/bin/env bash
# Gallery Search System — Linux / macOS setup script
set -euo pipefail

OS="$(uname -s)"
PYTHON=${PYTHON:-python3}

echo "=== Gallery Search — setup (${OS}) ==="

# ── 1. Python ───────────────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    echo "[ERROR] Python 3 is not installed."
    echo "        Install 3.10+ from https://www.python.org/downloads/"
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[OK] Python ${PY_VER}"

# ── 2. ffmpeg ───────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
    echo "[WARN] ffmpeg not found. Video features will not work."
    echo "       Linux: sudo apt install ffmpeg"
    echo "       macOS: brew install ffmpeg"
fi

# ── 3. Virtual environment ───────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "[SETUP] Creating virtual environment..."
    "$PYTHON" -m venv .venv
fi
source .venv/bin/activate

# ── 4. Dependencies (OS-specific) ────────────────────────────
echo "[SETUP] Installing dependencies..."
pip install --upgrade pip -q

if [ "$OS" = "Darwin" ]; then
    echo "[SETUP] macOS — using EasyOCR requirements"
    pip install -r requirements-mac.txt -q
else
    echo "[SETUP] Linux — using PaddleOCR requirements"
    pip install -r requirements-linux.txt -q
fi

# ── 5. RAM++ ───────────────────────────────────────────────
echo "[SETUP] Installing RAM++ (recognize-anything)..."
pip install "git+https://github.com/xinyu1205/recognize-anything.git" -q

# ── 6. .env ────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "[CONFIG] Created .env — edit if needed."
fi

# ── 7. Directories ───────────────────────────────────────────
mkdir -p images_sample thumbs

echo ""
echo "=== Setup complete ==="
echo "Run the server:"
echo "  source .venv/bin/activate"
echo "  uvicorn server.main:app --host 127.0.0.1 --port 8000"
echo ""
echo "Open: http://localhost:8000"
