import logging
import os
import platform

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

_default_origins  = "http://localhost,http://127.0.0.1,http://localhost:8000,http://127.0.0.1:8000"
ALLOWED_ORIGINS   = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]

MAX_UPLOAD_BYTES  = _int_env("MAX_UPLOAD_BYTES", 500 * 1024 * 1024)
MAX_QUERY_LEN     = _int_env("MAX_QUERY_LEN", 256)
SEARCH_RATE_LIMIT = _int_env("SEARCH_RATE_LIMIT", 30)
UPLOAD_RATE_LIMIT = _int_env("UPLOAD_RATE_LIMIT", 60)

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
