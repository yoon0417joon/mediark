"""인제스천 파이프라인 오케스트레이터.

Sprint 2: OCR + 썸네일 처리
Sprint 4: 임베딩 + Qdrant 저장 추가
Sprint 7: VLM 제거
Sprint 8: WD 태거 전체 적용으로 재구성
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile

from server.config import DUPLICATE_POLICY, MAX_MEDIA_DURATION, REBUILD_TAG_STATS_FULL_PIPELINE
from server.db.sqlite import (
    apply_tag_stats_delta,
    get_media_by_hash,
    get_media_by_id,
    get_unprocessed_media,
    get_unembedded_media,
    get_unembedded_media_by_ids,
    get_indexed_media_ids,
    get_unindexed_ids_from,
    reset_indexed_at,
    update_file_hash,
    update_media_atomic,
    update_indexed_at,
    update_index_error,
)
from server.ingest.hashing import compute_sha256

from server.ingest.ocr import run_ocr_on_image, run_ocr_on_frames
from server.ingest.tagger import tag_image, tag_frames
from server.ingest.ram import tag_image as ram_tag_image, tag_frames as ram_tag_frames
from server.ingest.thumbnail import generate_thumbnail
from server.ingest.video import extract_keyframes

logger = logging.getLogger(__name__)


# ── OCR + 태거 + 썸네일 ───────────────────────────────────────────────────────

def _process_media(media_id: int, filepath: str, media_type: str) -> dict:
    """
    단일 미디어에 대해 OCR + WD 태거 + 썸네일 처리를 수행한다.
    Returns: {"id", "ocr", "tags", "thumb", "first_frame", "tmp_dir", "error"}
    """
    result: dict = {
        "id": media_id,
        "ocr": None,
        "tags": None,
        "ram_tags": None,
        "audio_text": None,
        "thumb": None,
        "first_frame": None,
        "tmp_dir": None,
        "error": None,
    }

    try:
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"파일 없음: {filepath}")
        if os.path.getsize(filepath) == 0:
            raise ValueError(f"빈 파일 (0 bytes): {filepath}")

        # ── 해시 기반 중복 감지 (Sprint 14A) ──────────────────────────────────
        file_hash = compute_sha256(filepath)
        existing = get_media_by_hash(file_hash)
        if existing is not None and int(existing["id"]) != media_id:
            if DUPLICATE_POLICY != "warn_only":
                logger.info(
                    "[pipeline] 중복 파일 감지, 인제스천 스킵 (id=%s, 기존 id=%s): %s",
                    media_id, existing["id"], filepath,
                )
                result["error"] = f"duplicate_of:{existing['id']}"
                return result
            else:
                logger.warning(
                    "[pipeline] 중복 파일 경고 (id=%s, 기존 id=%s): %s",
                    media_id, existing["id"], filepath,
                )
        update_file_hash(media_id, file_hash)

        # H4: 증분 tag_stats 반영을 위해 기존 태그 스냅샷
        prev_row = get_media_by_id(media_id)
        prev_tags = prev_row["tags"] if prev_row else None
        prev_ram = prev_row["ram_tags"] if prev_row else None

        if media_type == "image":
            result["tags"] = tag_image(filepath)
            result["ram_tags"] = ram_tag_image(filepath)
            result["ocr"] = run_ocr_on_image(filepath)
            result["thumb"] = generate_thumbnail(media_id, filepath, "image")
            result["first_frame"] = filepath

        elif media_type in ("gif", "video"):
            from server.ingest.video import get_video_duration
            dur = get_video_duration(filepath)
            if dur > MAX_MEDIA_DURATION:
                raise ValueError(
                    f"길이 초과 스킵 ({dur:.0f}초 > {MAX_MEDIA_DURATION}초): {filepath}"
                )
            tmp_dir = tempfile.mkdtemp()
            result["tmp_dir"] = tmp_dir
            frames = extract_keyframes(filepath, tmp_dir)

            if not frames and media_type == "gif":
                try:
                    from PIL import Image as _PIL
                    pil_frame_path = os.path.join(tmp_dir, "frame_0001.jpg")
                    with _PIL.open(filepath) as _gif:
                        _gif.seek(0)
                        _gif.convert("RGB").save(pil_frame_path, "JPEG", quality=85)
                    if os.path.exists(pil_frame_path):
                        frames = [pil_frame_path]
                        logger.info("[pipeline] 1-frame GIF 폴백 적용: %s", filepath)
                except Exception as _pil_err:
                    logger.warning("[pipeline] PIL GIF 폴백 실패: %s", _pil_err)

            if not frames:
                raise RuntimeError("키프레임 추출 실패: ffmpeg 미설치 또는 파일 손상")
            result["tags"] = tag_frames(frames)
            result["ram_tags"] = ram_tag_frames(frames)
            result["ocr"] = run_ocr_on_frames(frames)
            result["thumb"] = generate_thumbnail(media_id, frames[0], "image")
            result["first_frame"] = frames[0]

            if media_type == "video":
                from server.ingest.audio import transcribe_video
                result["audio_text"] = transcribe_video(filepath)

        else:
            raise ValueError(f"알 수 없는 media_type: {media_type}")

        # H13: 단일 트랜잭션으로 원자 업데이트. H14: 비영상은 audio_text 미터치 (skip_audio).
        update_media_atomic(
            media_id,
            ocr_text=result["ocr"],
            tags=result["tags"],
            ram_tags=result["ram_tags"],
            audio_text=result["audio_text"],
            thumb_path=result["thumb"],
            skip_audio=(media_type != "video"),
        )

        # H4: 태그 변화분만 tag_stats 에 증분 반영 (풀 rebuild 회피)
        apply_tag_stats_delta(prev_tags, result["tags"], prev_ram, result["ram_tags"])

    except Exception as e:
        result["error"] = str(e)
        logger.exception("[pipeline] 처리 실패 (id=%s, %s): %s", media_id, filepath, e)
        if result["tmp_dir"]:
            shutil.rmtree(result["tmp_dir"], ignore_errors=True)
            result["tmp_dir"] = None

    return result


def run_ocr_and_thumbnail_pipeline(media_list: list[dict] | None = None) -> list[dict]:
    """
    OCR + WD 태거 + 썸네일 파이프라인을 실행한다.
    media_list가 None이면 DB에서 미처리 항목을 자동으로 가져온다.
    """
    if media_list is None:
        media_list = get_unprocessed_media()

    total = len(media_list)
    logger.info("[pipeline] OCR/태거/썸네일 대상: %d개", total)
    results = []
    for i, item in enumerate(media_list, 1):
        logger.info("[pipeline] (%d/%d) %s", i, total, item["filepath"])
        r = _process_media(item["id"], item["filepath"], item["media_type"])
        if r["tmp_dir"]:
            shutil.rmtree(r["tmp_dir"], ignore_errors=True)
        results.append(r)

    ok = sum(1 for r in results if r["error"] is None)
    logger.info("[pipeline] OCR/태거/썸네일 완료: 성공 %d, 실패 %d", ok, total - ok)
    return results


# ── 임베딩 + Qdrant 저장 ──────────────────────────────────────────────────────

def run_embed_pipeline(media_list: list[dict] | None = None) -> list[dict]:
    """
    임베딩 생성 및 Qdrant 저장 파이프라인을 실행한다.
    media_list가 None이면 DB에서 미임베딩 항목(thumb_path 있음 + indexed_at 없음)을 가져온다.

    각 항목은 다음 컬럼을 포함해야 한다:
        id, filepath, media_type, ocr_text, tags, thumb_path
    """
    from server.search.embed import build_combined_text, get_embeddings
    from server.db.qdrant import upsert_vectors_batch
    from server.db.qdrant import init_collection, collection_exists

    if not collection_exists():
        init_collection()

    if media_list is None:
        media_list = get_unembedded_media()

    # H10: get_unembedded_media 는 Phase 1 완료(썸네일 존재) 항목만 반환 —
    # 길이 초과 GIF/영상은 Phase 1 에서 이미 스킵되어 썸네일이 없으므로 재검사 불필요.

    total = len(media_list)
    logger.info("[pipeline] 임베딩 대상: %d개", total)

    if total == 0:
        logger.info("[pipeline] 임베딩할 항목 없음")
        return []

    results: list[dict] = []
    BATCH = 32

    for batch_start in range(0, total, BATCH):
        batch = media_list[batch_start : batch_start + BATCH]
        batch_filtered: list[dict] = []
        texts: list[str] = []
        for item in batch:
            text = build_combined_text(
                item.get("ocr_text"),
                item.get("tags"),
                item.get("ram_tags"),
                item.get("audio_text"),
            )
            if not text.strip():
                logger.info(
                    "[pipeline] 빈 텍스트 임베딩 스킵 (id=%s): %s",
                    item["id"], item.get("filepath"),
                )
                update_index_error(item["id"], "empty_text")
                results.append({"id": item["id"], "error": "empty_text"})
                continue
            batch_filtered.append(item)
            texts.append(text)

        if not batch_filtered:
            continue
        batch = batch_filtered

        try:
            vectors = get_embeddings(texts)
        except Exception as e:
            logger.exception("[pipeline] 임베딩 실패 (batch %d~): %s", batch_start, e)
            # M16: index_error 기록 → 다음 스캔 시 영구 재시도 루프 방지
            for item in batch:
                update_index_error(item["id"], f"embed_failed: {e}")
                results.append({"id": item["id"], "error": str(e)})
            continue

        upsert_items: list[tuple[int, list[float], dict]] = []
        for item, vector in zip(batch, vectors):
            payload = {
                "media_id":   item["id"],
                "filepath":   item["filepath"],
                "media_type": item["media_type"],
                "thumb_path": item.get("thumb_path") or "",
            }
            upsert_items.append((item["id"], vector, payload))

        try:
            upsert_vectors_batch(upsert_items)
            for item in batch:
                update_indexed_at(item["id"])
                update_index_error(item["id"], None)
                results.append({"id": item["id"], "error": None})
            end_idx = min(batch_start + BATCH, total)
            logger.info("[pipeline] 임베딩 저장 완료 (%d~%d/%d)", batch_start + 1, end_idx, total)
        except Exception as e:
            logger.exception("[pipeline] Qdrant upsert 실패 (batch %d~): %s", batch_start, e)
            # M16: index_error 기록으로 재시도 루프 차단
            for item in batch:
                update_index_error(item["id"], f"upsert_failed: {e}")
                results.append({"id": item["id"], "error": str(e)})

    ok = sum(1 for r in results if r["error"] is None)
    logger.info("[pipeline] 임베딩 완료: 성공 %d, 실패 %d", ok, total - ok)
    return results


# ── Qdrant-SQLite 정합성 복구 ────────────────────────────────────────────────

def repair_qdrant_consistency() -> int:
    """Qdrant-SQLite 양방향 정합성 불일치를 감지하고 복구한다.

    순방향: SQLite indexed_at 있음 + Qdrant 벡터 없음 → indexed_at 초기화
    역방향: Qdrant 벡터 있음 + SQLite indexed_at NULL  → indexed_at 기록
    """
    from server.db.qdrant import get_existing_ids, get_all_point_ids

    repaired = 0

    # ── 순방향: SQLite indexed → Qdrant 누락 ─────────────────────────────────
    all_indexed_ids = get_indexed_media_ids()
    if all_indexed_ids:
        existing_ids = get_existing_ids(all_indexed_ids)
        missing_in_qdrant = [id_ for id_ in all_indexed_ids if id_ not in existing_ids]
        if missing_in_qdrant:
            logger.info(
                "[pipeline] [순방향] Qdrant 누락 %d개 감지 - indexed_at 초기화",
                len(missing_in_qdrant),
            )
            for id_ in missing_in_qdrant:
                reset_indexed_at(id_)
            repaired += len(missing_in_qdrant)

    all_qdrant_ids = get_all_point_ids()
    if all_qdrant_ids:
        unindexed_ids = get_unindexed_ids_from(list(all_qdrant_ids))
        if unindexed_ids:
            logger.info(
                "[pipeline] [역방향] SQLite indexed_at 누락 %d개 감지 - indexed_at 보정",
                len(unindexed_ids),
            )
            for id_ in unindexed_ids:
                update_indexed_at(id_)
            repaired += len(unindexed_ids)

    if repaired == 0:
        logger.info("[pipeline] Qdrant-SQLite 정합성 확인: 불일치 없음")

    return repaired


# ── 전체 파이프라인 (OCR + 태거 + 썸네일 + 임베딩) ──────────────────────────

def run_full_pipeline(media_list: list[dict] | None = None) -> list[dict]:
    """
    OCR + WD 태거 + 썸네일 + 임베딩 전체 파이프라인을 순서대로 실행한다.
    """
    from server.db.qdrant import init_collection, collection_exists

    if not collection_exists():
        init_collection()

    if media_list is None:
        from server.ingest.scanner import scan_new_media
        media_list = scan_new_media()

    total = len(media_list)
    logger.info("[pipeline] 전체 파이프라인 대상: %d개", total)

    target_ids: set[int] | None = {item["id"] for item in media_list} if media_list else None
    run_ocr_and_thumbnail_pipeline(media_list if media_list else None)

    repair_qdrant_consistency()
    if target_ids:
        # H5 지원: 지정된 id 만 직접 조회하여 임베딩 (전체 미임베딩 스캔 대신)
        embed_list = get_unembedded_media_by_ids(sorted(target_ids))
        run_embed_pipeline(embed_list if embed_list else None)
    else:
        run_embed_pipeline()

    if REBUILD_TAG_STATS_FULL_PIPELINE:
        from server.db.sqlite import rebuild_tag_stats
        rebuild_tag_stats()

    return []


if __name__ == "__main__":
    import sys
    from server.db.sqlite import init_db
    from server.ingest.scanner import scan_new_media

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()

    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    if mode == "ocr":
        new_items = scan_new_media()
        run_ocr_and_thumbnail_pipeline(new_items or None)

    elif mode == "embed":
        run_embed_pipeline()

    else:
        new_items = scan_new_media()
        if new_items:
            logger.info("[pipeline] 신규 파일 %d개 스캔 완료", len(new_items))
        else:
            logger.info("[pipeline] 신규 파일 없음 - 기존 미처리 항목 확인")

        run_ocr_and_thumbnail_pipeline()
        repair_qdrant_consistency()
        run_embed_pipeline()

        from server.db.sqlite import rebuild_tag_stats
        rebuild_tag_stats()
