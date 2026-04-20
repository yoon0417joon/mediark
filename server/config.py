import logging
import os
import platform
from pathlib import Path

# uvicorn 단독 실행 시에도 프로젝트 루트 `.env` 가 os.environ 에 반영되도록 한다.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

_log = logging.getLogger(__name__)

# ── 플랫폼 감지 ──────────────────────────────────────────────
_PLATFORM = platform.system()  # "Linux" | "Darwin" | "Windows"

# OCR 백엔드: macOS는 EasyOCR (Apple Silicon 호환), 나머지는 PaddleOCR
# OCR_BACKEND 환경변수로 강제 지정 가능 ("paddleocr" | "easyocr")
OCR_BACKEND: str = os.environ.get(
    "OCR_BACKEND",
    "easyocr" if _PLATFORM == "Darwin" else "paddleocr",
)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip(), 10)
    except ValueError:
        _log.warning("환경변수 %s=%r 가 정수가 아닙니다 — 기본값 %s 사용", name, raw, default)
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        _log.warning("환경변수 %s=%r 가 실수가 아닙니다 — 기본값 %s 사용", name, raw, default)
        return default


# ── 갤러리 경로 ──────────────────────────────────────────────
GALLERY_ROOT     = os.environ.get("GALLERY_ROOT", "./images_sample")
THUMB_DIR        = os.environ.get("THUMB_DIR", "./thumbs")

# ── 서버 바인드 / 보안 ────────────────────────────────────────
HOST              = os.environ.get("HOST", "127.0.0.1")
PORT              = _int_env("PORT", 8000)

API_KEY           = os.environ.get("API_KEY", "").strip()

# 브라우저 <img> 는 Authorization 을 보낼 수 없음 — HttpOnly 세션 쿠키 권장.
# 1 이면 GET /thumb/*, /media/* 를 토큰 없이 공개(레거시). 기본 0.
PUBLIC_MEDIA_GET: bool = os.environ.get(
    "PUBLIC_MEDIA_GET", "0"
).strip().lower() in ("1", "true", "yes", "on")

_default_origins  = "http://localhost,http://127.0.0.1,http://localhost:8000,http://127.0.0.1:8000"
ALLOWED_ORIGINS   = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]

MAX_UPLOAD_BYTES  = _int_env("MAX_UPLOAD_BYTES", 500 * 1024 * 1024)
MAX_QUERY_LEN     = _int_env("MAX_QUERY_LEN", 256)
SEARCH_RATE_LIMIT = _int_env("SEARCH_RATE_LIMIT", 90)
UPLOAD_RATE_LIMIT = _int_env("UPLOAD_RATE_LIMIT", 120)

QDRANT_URL        = os.environ.get("QDRANT_URL", "").strip()
QDRANT_API_KEY    = os.environ.get("QDRANT_API_KEY", "").strip() or None

TAGGER_MODEL     = os.environ.get("TAGGER_MODEL", "SmilingWolf/wd-eva02-large-tagger-v3")
RAM_MODEL        = os.environ.get("RAM_MODEL", "xinyu1205/recognize-anything-plus-model")
RAM_IMAGE_SIZE   = _int_env("RAM_IMAGE_SIZE", 384)
RAM_THRESHOLD    = _float_env("RAM_THRESHOLD", 0.5)
EMBED_MODEL      = os.environ.get(
    "EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
)

SQLITE_PATH      = os.environ.get("SQLITE_PATH", "./gallery.db")
QDRANT_PATH      = os.environ.get("QDRANT_PATH", "./qdrant_data")

VIDEO_SHORT_THRESHOLD = _int_env("VIDEO_SHORT_THRESHOLD", 2)
KEYFRAME_INTERVAL     = _int_env("KEYFRAME_INTERVAL", 2)
MAX_MEDIA_DURATION    = _int_env("MAX_MEDIA_DURATION", 600)
TAGGER_THRESHOLD      = _float_env("TAGGER_THRESHOLD", 0.35)
THUMB_MAX_SIZE        = (
    _int_env("THUMB_MAX_W", 320),
    _int_env("THUMB_MAX_H", 320),
)
OCR_LANG              = os.environ.get("OCR_LANG", "korean")

QDRANT_COLLECTION     = "gallery"
EMBED_VECTOR_SIZE     = 768

WATCHDOG_DEBOUNCE_SECONDS = _float_env("WATCHDOG_DEBOUNCE_SECONDS", 3.0)

STT_MODEL    = os.environ.get("STT_MODEL", "base")
STT_LANGUAGE = os.environ.get("STT_LANGUAGE", None)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
GIF_EXTENSIONS   = {".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}

# ffmpeg / ffprobe / whisper 하위 프로세스 상한(초) — 미지정 시 무한 대기 방지 (HIGH-4)
SUBPROCESS_TIMEOUT_SEC = _int_env("SUBPROCESS_TIMEOUT_SEC", 600)

# 짧은 쿼리 LIKE 폴백 시 행 상한 (MEDIUM-2)
LIKE_FALLBACK_MAX_ROWS = _int_env("LIKE_FALLBACK_MAX_ROWS", 5000)

# run_full_pipeline 끝의 tag_stats 전체 재구축 — 기본 끔(MEDIUM-3), 1 로 켤 수 있음
REBUILD_TAG_STATS_FULL_PIPELINE = os.environ.get(
    "REBUILD_TAG_STATS_FULL_PIPELINE", "0"
).strip().lower() in ("1", "true", "yes", "on")

# 레이트리밋 IP×버킷 맵 상한 (MEDIUM-6)
RATE_LIMIT_MAX_KEYS = _int_env("RATE_LIMIT_MAX_KEYS", 5000)

# 미들웨어 전역 레이트리밋: 분당 IP당 요청 수(/thumb, /media 는 제외)
GLOBAL_RATE_LIMIT = _int_env("GLOBAL_RATE_LIMIT", 2000)

# 중복 파일 감지 정책 (Sprint 14A)
# reject_only   : 409 반환, 기존 파일 유지 (기본)
# auto_delete_new: 업로드 파일 버리고 기존 파일 유지 (성공 응답, duplicate=true)
# warn_only     : 경고만 반환, 업로드 진행
DUPLICATE_POLICY: str = os.environ.get("DUPLICATE_POLICY", "reject_only").strip()

# ── 인증 (Sprint 15) ─────────────────────────────────────────
# JWT_SECRET: HMAC 서명 키. 미설정 시 프로세스 기동마다 랜덤 생성(= 재시작 시 모든 토큰 무효).
#             운영 환경에서는 반드시 고정 값을 설정.
JWT_SECRET: str = os.environ.get("JWT_SECRET", "").strip()
JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256").strip() or "HS256"
JWT_TTL_MINUTES: int = _int_env("JWT_TTL_MINUTES", 60 * 24)  # 24시간

# HttpOnly 세션 쿠키(로그인 시 JWT 저장) — <img>/fetch 가 동일 출처에서 쿠키 전송
SESSION_COOKIE_NAME: str = (
    os.environ.get("SESSION_COOKIE_NAME", "gallery_session").strip() or "gallery_session"
)
SESSION_COOKIE_SECURE: bool = os.environ.get(
    "SESSION_COOKIE_SECURE", "0"
).strip().lower() in ("1", "true", "yes", "on")
_ss = os.environ.get("SESSION_COOKIE_SAMESITE", "lax").strip().lower()
SESSION_COOKIE_SAMESITE: str = _ss if _ss in ("lax", "strict", "none") else "lax"

# 초기 admin 부트스트랩. 둘 다 설정돼 있으면 첫 실행 시 admin 계정을 자동 생성.
BOOTSTRAP_ADMIN_EMAIL: str = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
# .env 한 줄 끝 공백 제거. (의도적 후행 공백 비밀번호는 지원하지 않음)
BOOTSTRAP_ADMIN_PASSWORD: str = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()

# 로그인·가입 레이트리밋(분당 IP당 시도 횟수)
LOGIN_RATE_LIMIT: int = _int_env("LOGIN_RATE_LIMIT", 30)

# 1 이면 초대 코드 없이 회원가입 허용(기본 역할은 OPEN_REGISTRATION_ROLE). 업로드 등은 여전히 로그인 필요.
OPEN_REGISTRATION: bool = os.environ.get(
    "OPEN_REGISTRATION", "0"
).strip().lower() in ("1", "true", "yes", "on")
OPEN_REGISTRATION_ROLE: str = (
    os.environ.get("OPEN_REGISTRATION_ROLE", "viewer").strip() or "viewer"
)

# JWT 없이 접근하는 익명 사용자에게 부여할 기본 역할.
# "none" = 익명 접근 차단 (기본), "viewer" = 검색/열람 허용, "uploader" = 업로드도 허용.
DEFAULT_ANON_ROLE: str = (
    os.environ.get("DEFAULT_ANON_ROLE", "none").strip().lower() or "none"
)
