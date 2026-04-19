"""FastAPI 앱 진입점 — Sprint 13.

엔드포인트:
  GET  /search              — 소스별 벡터 검색 + 재순위 (ocr_q/wd14_q/ram_q/stt_q)
  GET  /random              — 랜덤 미디어 반환
  GET  /tags/suggest        — 태그 자동완성 (prefix 매칭, count 내림차순)
  GET  /info/{id}           — 미디어 메타데이터 조회 (OCR/WD14/RAM++/STT)
  GET  /media/{id}          — 원본 파일 서빙
  GET  /thumb/{id}          — 썸네일 서빙
  POST /ingest              — 수동 인제스천 트리거 (백그라운드)
  GET  /status              — 인제스천 진행 상황 반환
  GET  /watchdog/status     — watchdog 상태 반환 (Sprint 11)
  POST /upload              — 파일 업로드 → 갤러리 저장 + 인제스천 자동 실행 (Sprint 13)
"""

from __future__ import annotations

import hmac
import logging
import math
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from server.config import (
    ALLOWED_ORIGINS,
    API_KEY,
    GALLERY_ROOT,
    HOST,
    MAX_QUERY_LEN,
    PORT,
    QDRANT_URL,
    SEARCH_RATE_LIMIT,
    THUMB_DIR,
)
from server.db.sqlite import (
    get_connection,
    get_media_by_id,
    get_random_media,
    init_db,
    rebuild_tag_stats,
    suggest_tags,
)
from server.http_utils import client_ip
from server.ingest.watcher import GalleryWatcher
from server.rate_limit import rate_limit_bucket
from server.routes_upload import router as upload_router
from server.search.query import search as _search
from server import upload_tracking

logger = logging.getLogger(__name__)

# ── lifespan (startup/shutdown 통합 — H3) ────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """DB/모델/Qdrant warmup, tag_stats rebuild, watchdog 시작 → 종료 시 정리."""
    global _watcher
    init_db()

    # 임베딩 모델 사전 로드 — 첫 쿼리 지연 방지. 실패 시 서버 기동 중단 (H16).
    from server.search.embed import get_embedding
    get_embedding("warmup")
    logger.info("[main] 임베딩 모델 로드 완료")

    # Qdrant warmup — 실패해도 서버는 기동 (컬렉션 미생성 상태 허용).
    try:
        from server.db.qdrant import collection_exists, get_client
        from server.config import EMBED_VECTOR_SIZE, QDRANT_COLLECTION
        if collection_exists():
            get_client().search(
                collection_name=QDRANT_COLLECTION,
                query_vector=[0.0] * EMBED_VECTOR_SIZE,
                limit=1,
            )
            logger.info("[main] Qdrant warmup 완료")
    except Exception as e:
        logger.warning("[main] Qdrant warmup 실패 (계속 진행): %s", e)

    if not QDRANT_URL:
        logger.warning(
            "[main] Qdrant 로컬 파일 모드: 멀티 워커(uvicorn --workers N>1)는 데이터 손상 위험이 있으니 "
            "workers=1 또는 QDRANT_URL(HTTP 모드) 사용을 권장합니다."
        )

    # tag_stats 자동 rebuild — 비어있으면 갱신
    try:
        conn = get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM tag_stats").fetchone()[0]
        finally:
            conn.close()
        if count == 0:
            rebuild_tag_stats()
            logger.info("[main] tag_stats 자동 rebuild 완료")
    except Exception as e:
        logger.warning("[main] tag_stats rebuild 실패 (계속 진행): %s", e)

    # watchdog 시작
    try:
        _watcher = GalleryWatcher(
            is_ingest_running=lambda: _ingest_state["running"],
            should_skip=upload_tracking.is_upload_in_progress,
        )
        _watcher.start()
    except Exception as e:
        logger.warning("[main] watchdog 시작 실패 (계속 진행): %s", e)

    upload_tracking.upload_sweeper_stop.clear()
    sweeper = threading.Thread(
        target=upload_tracking.upload_sweeper_loop, name="upload-sweeper", daemon=True
    )
    sweeper.start()

    # M19: client/ 디렉토리 존재 여부를 startup 시점에 평가 (import 시점 제거)
    _client_dir = Path(__file__).parent.parent / "client"
    if _client_dir.exists() and any(_client_dir.iterdir()):
        # 이미 마운트됐으면 중복 등록 방지
        if not any(r.path == "/" for r in getattr(app, "routes", [])):
            app.mount("/", StaticFiles(directory=str(_client_dir), html=True), name="static")

    try:
        yield
    finally:
        upload_tracking.upload_sweeper_stop.set()
        if _watcher is not None:
            try:
                _watcher.stop()
            except Exception as e:
                logger.warning("[main] watchdog 종료 실패: %s", e)
            _watcher = None


# ── 앱 초기화 ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Gallery Search API", version="0.13.1", lifespan=lifespan)

# ── CORS — 명시적 화이트리스트. 와일드카드는 "*" 가 origin 목록에 있을 때만 ──
_cors_wildcard = "*" in ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if not _cors_wildcard else ["*"],
    allow_credentials=not _cors_wildcard,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

# ── 인증 · 레이트리밋 미들웨어 ────────────────────────────────────────────────
# API_KEY 환경변수 가 비어있으면 loopback(127.0.0.1/::1) 에서만 요청 허용.
# 설정되어 있으면 X-API-Key 또는 Authorization: Bearer <key> 헤더 필수.

_PUBLIC_PATHS = {"/healthz"}
if not API_KEY:
    # 키 미설정(로컬 전용)일 때만 API 문서 무인증 공개
    _PUBLIC_PATHS |= {"/docs", "/openapi.json", "/redoc"}
_STATIC_PREFIXES = ("/static/",)
_CLIENT_ASSETS = {"/", "/index.html", "/style.css", "/app.js", "/favicon.ico"}


def _is_loopback(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "localhost")


def _check_api_key(request: Request) -> None:
    """API_KEY 설정 시 헤더 검증. 미설정 시 loopback-only 로 접근 제한."""
    path = request.url.path
    if request.method == "OPTIONS":
        return
    # 정적 자산 및 퍼블릭 경로는 통과
    if path in _PUBLIC_PATHS or path in _CLIENT_ASSETS:
        return
    if any(path.startswith(p) for p in _STATIC_PREFIXES):
        return
    # 클라이언트 번들 파일 (확장자 기반) 통과
    if path.endswith((".js", ".css", ".map", ".ico", ".png", ".svg", ".woff", ".woff2")):
        return

    if not API_KEY:
        # 키 미설정: 원격 접근 차단 (loopback 만 허용)
        ip = client_ip(request)
        if not _is_loopback(ip):
            raise HTTPException(
                status_code=401,
                detail="API_KEY 환경변수가 설정되지 않았습니다. 원격 접근이 차단되었습니다.",
            )
        return

    provided = request.headers.get("x-api-key", "")
    if not provided:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()

    if not provided or not hmac.compare_digest(provided, API_KEY):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키")


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """인증 체크 + 기본 레이트리밋(IP 당 300 req/min 상한)."""

    async def dispatch(self, request: Request, call_next):
        try:
            _check_api_key(request)
            if request.method != "OPTIONS":
                rate_limit_bucket(client_ip(request), "global", 300)
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
        return await call_next(request)


app.add_middleware(AuthRateLimitMiddleware)

app.include_router(upload_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

# ── 인제스천 상태 (프로세스 내 공유 상태) — H1: 락 추가 ──────────────────────

_ingest_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_ingest_lock = threading.Lock()

# ── watchdog (Sprint 11) ──────────────────────────────────────────────────────
# L7: GalleryWatcher import 는 파일 상단 import 블록으로 이동. 여기서는 상태만 선언.

_watcher: GalleryWatcher | None = None

# ── 검색 ──────────────────────────────────────────────────────────────────────

def _validate_query(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if len(value) > MAX_QUERY_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"{name} 쿼리는 최대 {MAX_QUERY_LEN}자까지 허용됩니다",
        )
    return value


@app.get("/search")
async def search_endpoint(
    request: Request,
    ocr_q:      Optional[str] = Query(None, max_length=MAX_QUERY_LEN, description="OCR 텍스트 검색어"),
    wd14_q:     Optional[str] = Query(None, max_length=MAX_QUERY_LEN, description="WD14 태그 검색어"),
    ram_q:      Optional[str] = Query(None, max_length=MAX_QUERY_LEN, description="RAM++ 태그 검색어"),
    stt_q:      Optional[str] = Query(None, max_length=MAX_QUERY_LEN, description="STT 텍스트 검색어"),
    media_type: Optional[str] = Query(None, description="미디어 타입 필터: image | gif | video"),
    page:       int = Query(1, ge=1, description="페이지 번호 (1-based)"),
    per_page:   int = Query(50, ge=1, le=200, description="페이지당 결과 수"),
):
    """소스별 쿼리로 관련 미디어를 반환한다. 최소 1개 이상의 쿼리 필요."""
    rate_limit_bucket(client_ip(request), "search", SEARCH_RATE_LIMIT)
    ocr_q  = _validate_query("ocr_q",  ocr_q)
    wd14_q = _validate_query("wd14_q", wd14_q)
    ram_q  = _validate_query("ram_q",  ram_q)
    stt_q  = _validate_query("stt_q",  stt_q)
    mt = media_type if media_type in ("image", "gif", "video") else None

    def _run_search():
        return _search(
            ocr_q=ocr_q,
            wd14_q=wd14_q,
            ram_q=ram_q,
            stt_q=stt_q,
            media_type=mt,
            page=page,
            per_page=per_page,
        )

    try:
        results, total, elapsed_ms = await run_in_threadpool(_run_search)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    total_pages = 0 if total == 0 else max(1, math.ceil(total / per_page))
    return {
        "results": results,
        "count": len(results),
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "elapsed_ms": round(elapsed_ms, 2),
    }


# ── 랜덤 미디어 ──────────────────────────────────────────────────────────────

@app.get("/random")
async def random_endpoint(
    limit:      int = Query(50, ge=1, le=200, description="반환할 미디어 수"),
    media_type: Optional[str] = Query(None, description="미디어 타입 필터: image | gif | video"),
):
    """썸네일이 있는 미디어를 RANDOM() 순서로 반환한다 (페이지네이션 없음)."""
    mt = media_type if media_type in ("image", "gif", "video") else None
    items = await run_in_threadpool(lambda: get_random_media(limit=limit, media_type=mt))
    return {
        "results": items,
        "count": len(items),
    }


# ── 태그 자동완성 ─────────────────────────────────────────────────────────────

@app.get("/tags/suggest")
async def tags_suggest_endpoint(
    q:      str = Query(..., description="자동완성 prefix (1자 이상)"),
    source: Literal["wd14", "ram"] = Query(..., description="태그 소스: wd14 | ram"),
    limit:  int = Query(20, ge=1, le=100, description="최대 반환 수"),
):
    """prefix로 시작하는 태그를 count 내림차순으로 반환한다."""
    if len(q) < 1:
        return {"results": [], "count": 0}
    tags = await run_in_threadpool(lambda: suggest_tags(prefix=q, source=source, limit=limit))
    return {"results": tags, "count": len(tags)}


# ── 파일 서빙 ─────────────────────────────────────────────────────────────────

@app.get("/info/{media_id}")
async def media_info(media_id: int):
    """미디어 메타데이터(OCR, 태그, RAM 태그)를 JSON으로 반환한다."""
    row = await run_in_threadpool(lambda: get_media_by_id(media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")
    return {
        "id":         row["id"],
        "filepath":   row["filepath"],
        "media_type": row["media_type"],
        "ocr_text":   row["ocr_text"] or "",
        "tags":       row["tags"] or "",
        "ram_tags":   row["ram_tags"] or "",
        "audio_text": row["audio_text"] or "",
    }


def _ensure_under(candidate: str, root: str) -> Path:
    """candidate 가 root 디렉토리 하위인지 재검증. 아니면 403."""
    try:
        cp = Path(candidate).resolve(strict=True)
        rp = Path(root).resolve()
        cp.relative_to(rp)
    except (FileNotFoundError, ValueError, OSError):
        raise HTTPException(status_code=403, detail="허용되지 않은 파일 경로")
    return cp


_MEDIA_CACHE_HEADERS = {
    "Cache-Control": "private, max-age=3600",
    "Accept-Ranges": "bytes",
}
_THUMB_CACHE_HEADERS = {
    "Cache-Control": "private, max-age=86400",
}


@app.get("/media/{media_id}")
async def serve_media(media_id: int):
    """원본 미디어 파일을 반환한다. GALLERY_ROOT 하위만 서빙.

    H19: 영상 스트리밍을 위해 Accept-Ranges + Cache-Control 헤더를 명시.
    Starlette FileResponse 는 Range 요청을 자동 처리한다.
    """
    row = await run_in_threadpool(lambda: get_media_by_id(media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")
    resolved = _ensure_under(row["filepath"], GALLERY_ROOT)
    return FileResponse(str(resolved), headers=_MEDIA_CACHE_HEADERS)


@app.get("/thumb/{media_id}")
async def serve_thumb(media_id: int):
    """썸네일 이미지를 반환한다. THUMB_DIR 하위만 서빙."""
    row = await run_in_threadpool(lambda: get_media_by_id(media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")
    thumb_path = row["thumb_path"]
    if not thumb_path:
        raise HTTPException(status_code=404, detail="썸네일이 존재하지 않습니다")
    resolved = _ensure_under(thumb_path, THUMB_DIR)
    return FileResponse(str(resolved), headers=_THUMB_CACHE_HEADERS)


# ── 인제스천 트리거 ───────────────────────────────────────────────────────────

def _run_ingest() -> None:
    """백그라운드 스레드에서 인제스천 파이프라인을 실행한다.

    H15: running/started_at 은 add_task 가 아닌 이 함수 진입점에서 세팅한다 —
    add_task 후 스케줄링 실패 시 running=True 가 영구 남는 것을 방지.
    """
    with _ingest_lock:
        _ingest_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _ingest_state["finished_at"] = None
        _ingest_state["error"] = None
    try:
        from server.ingest.pipeline import run_full_pipeline
        run_full_pipeline()
        with _ingest_lock:
            _ingest_state["error"] = None
        logger.info("[main] 인제스천 완료")
    except Exception as e:
        with _ingest_lock:
            _ingest_state["error"] = str(e)
        logger.exception("[main] 인제스천 예외: %s", e)
    finally:
        with _ingest_lock:
            _ingest_state["running"] = False
            _ingest_state["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.post("/ingest", status_code=202)
async def trigger_ingest(background_tasks: BackgroundTasks):
    """수동 인제스천을 시작한다. 즉시 202를 반환하고 백그라운드에서 실행된다."""
    # H1: 락으로 감싼 compare-and-set — 동시 요청 race 차단.
    with _ingest_lock:
        if _ingest_state["running"]:
            return JSONResponse(
                status_code=409,
                content={"message": "이미 인제스천이 진행 중입니다"},
            )
        _ingest_state["running"] = True
    try:
        background_tasks.add_task(_run_ingest)
    except Exception:
        with _ingest_lock:
            _ingest_state["running"] = False
        raise
    return {"message": "인제스천을 시작합니다", "status": "accepted"}


# ── 상태 조회 ─────────────────────────────────────────────────────────────────

def _status_counts() -> tuple[int, int]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN indexed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed "
            "FROM media"
        ).fetchone()
        total = row["total"] or 0
        completed = int(row["completed"] or 0)
        return total, completed
    finally:
        conn.close()


@app.get("/status")
async def ingest_status():
    """인제스천 진행 상황 및 DB 통계를 반환한다."""
    total, completed = await run_in_threadpool(_status_counts)

    return {
        "running": _ingest_state["running"],
        "total": total,
        "completed": completed,
        "pending": total - completed,
        "started_at": _ingest_state["started_at"],
        "finished_at": _ingest_state["finished_at"],
        "error": _ingest_state["error"],
    }


# ── watchdog 상태 조회 (Sprint 11) ───────────────────────────────────────────

@app.get("/watchdog/status")
async def watchdog_status():
    """watchdog 실행 상태 및 처리 통계를 반환한다."""
    if _watcher is None:
        return {"running": False, "watch_path": None, "processed": 0, "errors": 0, "queue_size": 0}
    return _watcher.stats()


# ── 정적 파일 (client/) ───────────────────────────────────────────────────────

# ── 직접 실행 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    # host/port 는 config (env HOST/PORT) 경유 — 기본값 127.0.0.1 (loopback 전용).
    # 외부 노출 시 HOST=0.0.0.0 + API_KEY 설정을 반드시 함께 해야 안전하다.
    if HOST == "0.0.0.0" and not API_KEY:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logger.warning(
            "[main] HOST=0.0.0.0 인데 API_KEY 가 설정되지 않았습니다. "
            "원격 접근은 런타임에 거부됩니다."
        )
    uvicorn.run("server.main:app", host=HOST, port=PORT, reload=False)
