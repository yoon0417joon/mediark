@echo off
REM Gallery Search System — Windows 10/11 설치 스크립트
setlocal enabledelayedexpansion

echo === Gallery Search — 설치 시작 (Windows) ===

REM ── 1. Python 확인 ──────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Python 이 설치되어 있지 않습니다.
    echo        https://www.python.org/downloads/ 에서 3.10 이상을 설치하세요.
    exit /b 1
)
python -c "import sys; v=sys.version_info; exit(0 if v.major==3 and v.minor>=10 else 1)" 2>nul
if %errorlevel% neq 0 (
    echo [경고] Python 3.10 이상을 권장합니다.
)
echo [OK] Python 확인 완료

REM ── 2. ffmpeg 확인 ──────────────────────────────────────────
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo [경고] ffmpeg 가 없습니다. 영상 처리 기능이 동작하지 않습니다.
    echo        https://ffmpeg.org/download.html 에서 설치하고 PATH 에 추가하세요.
)

REM ── 3. 가상환경 생성 ─────────────────────────────────────────
if not exist ".venv" (
    echo [설치] 가상환경 생성 중...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

REM ── 4. 의존성 설치 ──────────────────────────────────────────
echo [설치] 공통 의존성 설치 중...
python -m pip install --upgrade pip -q
echo [설치] Windows — PaddleOCR 의존성 사용
python -m pip install -r requirements-windows.txt -q

REM ── 5. RAM++ 설치 ────────────────────────────────────────────
echo [설치] RAM++ (recognize-anything) 설치 중...
python -m pip install "git+https://github.com/xinyu1205/recognize-anything.git" -q

REM ── 6. .env 파일 초기화 ──────────────────────────────────────
if not exist ".env" (
    copy .env.example .env >nul
    echo [설정] .env 파일 생성됨 — 필요 시 편집하세요.
)

REM ── 7. 디렉토리 생성 ─────────────────────────────────────────
if not exist "images_sample" mkdir images_sample
if not exist "thumbs" mkdir thumbs

echo.
echo === 설치 완료 ===
echo 서버 실행:
echo   .venv\Scripts\activate
echo   uvicorn server.main:app --host 127.0.0.1 --port 8000
echo.
echo 브라우저: http://localhost:8000
