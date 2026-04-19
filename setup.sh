#!/usr/bin/env bash
# Gallery Search System — Linux / macOS 설치 스크립트
set -euo pipefail

OS="$(uname -s)"
PYTHON=${PYTHON:-python3}

echo "=== Gallery Search — 설치 시작 (${OS}) ==="

# ── 1. Python 버전 확인 ─────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    echo "[오류] Python 3 가 설치되어 있지 않습니다."
    echo "       https://www.python.org/downloads/ 에서 3.10 이상을 설치하세요."
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[OK] Python ${PY_VER}"

# ── 2. ffmpeg 확인 ──────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
    echo "[경고] ffmpeg 가 없습니다. 영상 처리 기능이 동작하지 않습니다."
    echo "       Linux:  sudo apt install ffmpeg"
    echo "       macOS:  brew install ffmpeg"
fi

# ── 3. 가상환경 생성 ─────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "[설치] 가상환경 생성 중..."
    "$PYTHON" -m venv .venv
fi
source .venv/bin/activate

# ── 4. OS별 의존성 설치 ──────────────────────────────────────
echo "[설치] 공통 의존성 설치 중..."
pip install --upgrade pip -q

if [ "$OS" = "Darwin" ]; then
    echo "[설치] macOS — EasyOCR 의존성 사용"
    pip install -r requirements-mac.txt -q
else
    echo "[설치] Linux — PaddleOCR 의존성 사용"
    pip install -r requirements-linux.txt -q
fi

# ── 5. RAM++ 설치 ────────────────────────────────────────────
echo "[설치] RAM++ (recognize-anything) 설치 중..."
pip install "git+https://github.com/xinyu1205/recognize-anything.git" -q

# ── 6. .env 파일 초기화 ──────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "[설정] .env 파일 생성됨 — 필요 시 편집하세요."
fi

# ── 7. 디렉토리 생성 ─────────────────────────────────────────
mkdir -p images_sample thumbs

echo ""
echo "=== 설치 완료 ==="
echo "서버 실행:"
echo "  source .venv/bin/activate"
echo "  uvicorn server.main:app --host 127.0.0.1 --port 8000"
echo ""
echo "브라우저: http://localhost:8000"
