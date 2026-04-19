"""영상 파일에서 오디오를 추출하고 Whisper STT로 텍스트를 변환한다.

영상 전용 모듈. 이미지·GIF에서는 호출하지 않는다.
"""

from __future__ import annotations
import logging
import os
import subprocess
import tempfile
import threading
from typing import TYPE_CHECKING

from server.config import STT_MODEL, STT_LANGUAGE, SUBPROCESS_TIMEOUT_SEC
from server.ingest.video import _safe_ffmpeg_path

if TYPE_CHECKING:
    import whisper as _whisper_type

logger = logging.getLogger(__name__)

_model: "_whisper_type.Whisper | None" = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()


def _get_model() -> "_whisper_type.Whisper":
    """싱글턴 Whisper 모델을 반환한다 (H11: thread-safe)."""
    global _model
    if _model is not None:
        return _model
    with _load_lock:
        if _model is None:
            import whisper
            logger.info("[audio] Whisper 모델 로드: %s", STT_MODEL)
            _model = whisper.load_model(STT_MODEL)
            logger.info("[audio] Whisper 모델 로드 완료")
    return _model


def unload_model() -> None:
    """H12: Whisper 메모리 해제."""
    global _model
    with _load_lock:
        if _model is None:
            return
        try:
            import torch
            _model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("[audio] Whisper 언로드 완료")
        except Exception as e:
            logger.warning("[audio] 언로드 중 경고: %s", e)


def _extract_audio(video_path: str, out_path: str) -> bool:
    """ffmpeg으로 영상에서 16 kHz mono WAV 오디오를 추출한다.

    Args:
        video_path: 원본 영상 경로
        out_path:   출력 WAV 파일 경로

    Returns:
        True — 추출 성공 (out_path에 비어 있지 않은 파일 존재)
        False — 오디오 스트림 없음 또는 ffmpeg 오류
    """
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", _safe_ffmpeg_path(video_path),
            "-vn",                   # 비디오 스트림 제외
            "-acodec", "pcm_s16le",  # 16-bit PCM
            "-ar", "16000",          # 16 kHz (Whisper 권장)
            "-ac", "1",              # mono
            _safe_ffmpeg_path(out_path),
        ],
        capture_output=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        # H17: ffmpeg 실패 원인을 로깅 (무음/실패 구분)
        stderr = result.stderr.decode("utf-8", errors="replace")
        if "does not contain any stream" in stderr.lower() or "audio stream" not in stderr.lower():
            logger.info("[audio] 오디오 스트림 없음: %s", video_path)
        else:
            logger.warning("[audio] ffmpeg 실패 (%s): rc=%d, %s",
                           video_path, result.returncode, stderr[-400:])
        return False
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def transcribe_video(video_path: str) -> str:
    """영상 파일에서 STT 텍스트를 추출한다.

    처리 순서:
    1. ffmpeg으로 오디오 추출 (WAV 16 kHz mono)
    2. Whisper로 STT 변환
    3. 임시 파일 정리

    Args:
        video_path: 영상 파일 경로 (.mp4 / .mkv / .avi 등)

    Returns:
        변환된 텍스트 문자열. 오디오 없음·무음·오류 시 빈 문자열.
    """
    if not os.path.isfile(video_path):
        logger.warning("[audio] 파일 없음: %s", video_path)
        return ""

    tmp_dir = tempfile.mkdtemp()
    wav_path = os.path.join(tmp_dir, "audio.wav")

    try:
        if not _extract_audio(video_path, wav_path):
            logger.info("[audio] 오디오 스트림 없음: %s", video_path)
            return ""

        model = _get_model()
        options: dict = {}
        if STT_LANGUAGE:
            options["language"] = STT_LANGUAGE

        with _infer_lock:
            result = model.transcribe(wav_path, **options)
        text: str = result.get("text", "").strip()
        if text:
            logger.info("[audio] STT 완료: %d자 (%s)", len(text), video_path)
        else:
            logger.info("[audio] 무음 또는 인식 불가: %s", video_path)
        return text

    except Exception as e:
        logger.exception("[audio] STT 실패 (%s): %s", video_path, e)
        return ""

    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        logger.error("Usage: python -m server.ingest.audio <video_file>")
        sys.exit(1)

    path = sys.argv[1]
    text = transcribe_video(path)
    logger.info("[audio] 결과:\n%s", text)
