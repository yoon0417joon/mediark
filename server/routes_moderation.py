"""모더레이션 API — 신고 처리·미디어 숨김·삭제 (권한 키와 연결)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from server.auth.deps import require_login, require_permission
from server.config import GALLERY_ROOT, THUMB_DIR
from server.db.qdrant import delete_points_by_media_ids
from server.db.sqlite import (
    delete_media_row,
    get_media_by_id,
    get_media_report_by_id,
    insert_media_report,
    list_media_reports,
    resolve_media_report,
    set_media_hidden,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/moderation")

_perm_report = require_permission("report_review")
_perm_hide = require_permission("media_hide")
_perm_delete = require_permission("media_delete")


def _unlink_if_under(path: str | None, root: str) -> None:
    if not path or not str(path).strip():
        return
    try:
        p = Path(path).resolve(strict=True)
        p.relative_to(Path(root).resolve())
    except (FileNotFoundError, ValueError, OSError):
        return
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("[moderation] 파일 삭제 실패: %s", e)


class CreateReportBody(BaseModel):
    media_id: int = Field(ge=1)
    reason:   str | None = Field(default=None, max_length=2000)


@router.post("/reports", status_code=201)
async def create_report(
    payload: CreateReportBody,
    user: dict = Depends(require_login),
) -> dict:
    row = await run_in_threadpool(lambda: get_media_by_id(payload.media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")
    rid = await run_in_threadpool(
        lambda: insert_media_report(
            media_id=payload.media_id,
            reporter_id=int(user["id"]),
            reason=payload.reason,
        )
    )
    return {"id": rid, "media_id": payload.media_id, "status": "pending"}


@router.get("/reports")
async def list_reports(
    status: str | None = None,
    user: dict = Depends(_perm_report),
) -> dict:
    if status is not None and status not in ("pending", "reviewed", "dismissed"):
        raise HTTPException(status_code=400, detail="유효하지 않은 status")
    rows = await run_in_threadpool(lambda: list_media_reports(status=status))
    return {
        "results": [
            {
                "id":           int(r["id"]),
                "media_id":     int(r["media_id"]),
                "reporter_id":  r["reporter_id"],
                "reason":       r["reason"],
                "status":       r["status"],
                "created_at":   r["created_at"],
                "reviewed_by":  r["reviewed_by"],
                "reviewed_at":  r["reviewed_at"],
                "notes":        r["notes"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


class ReviewReportBody(BaseModel):
    status: str = Field(description="reviewed | dismissed")
    notes:  str | None = Field(default=None, max_length=4000)


@router.post("/reports/{report_id}/review")
async def review_report(
    report_id: int,
    payload: ReviewReportBody,
    user: dict = Depends(_perm_report),
) -> dict:
    if payload.status not in ("reviewed", "dismissed"):
        raise HTTPException(status_code=400, detail="status 는 reviewed 또는 dismissed")

    def _do() -> bool:
        return resolve_media_report(
            report_id=report_id,
            reviewer_id=int(user["id"]),
            status=payload.status,
            notes=payload.notes,
        )

    ok = await run_in_threadpool(_do)
    if not ok:
        row = await run_in_threadpool(lambda: get_media_report_by_id(report_id))
        if row is None:
            raise HTTPException(status_code=404, detail="신고를 찾을 수 없습니다")
        raise HTTPException(status_code=400, detail="이미 처리된 신고입니다")
    return {"id": report_id, "status": payload.status}


@router.post("/media/{media_id}/hide")
async def hide_media(
    media_id: int,
    user: dict = Depends(_perm_hide),
) -> dict:
    row = await run_in_threadpool(lambda: get_media_by_id(media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")
    await run_in_threadpool(lambda: set_media_hidden(media_id, True))
    return {"id": media_id, "hidden": True}


@router.post("/media/{media_id}/unhide")
async def unhide_media(
    media_id: int,
    user: dict = Depends(_perm_hide),
) -> dict:
    row = await run_in_threadpool(lambda: get_media_by_id(media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")
    await run_in_threadpool(lambda: set_media_hidden(media_id, False))
    return {"id": media_id, "hidden": False}


@router.delete("/media/{media_id}", status_code=200)
async def delete_media_moderation(
    media_id: int,
    user: dict = Depends(_perm_delete),
) -> dict:
    row = await run_in_threadpool(lambda: get_media_by_id(media_id))
    if row is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")

    await run_in_threadpool(lambda: delete_points_by_media_ids([media_id]))
    fp = str(row["filepath"])
    tp = row["thumb_path"]
    await run_in_threadpool(lambda: _unlink_if_under(fp, GALLERY_ROOT))
    await run_in_threadpool(lambda: _unlink_if_under(str(tp) if tp else "", THUMB_DIR))

    deleted = await run_in_threadpool(lambda: delete_media_row(media_id))
    if deleted is None:
        raise HTTPException(status_code=404, detail="미디어를 찾을 수 없습니다")
    return {"id": media_id, "deleted": True}
