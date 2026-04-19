"""ffmpeg을 이용한 영상 키프레임 추출."""

from __future__ import annotations
import functools
import logging
import os
import subprocess
from pathlib import Path

from server.config import KEYFRAME_INTERVAL, SUBPROCESS_TIMEOUT_SEC, VIDEO_SHORT_THRESHOLD

logger = logging.getLogger(__name__)


def _safe_ffmpeg_path(filepath: str) -> str:
    """ffmpeg/ffprobe argv 로 넘기기 전 파일 경로를 안전하게 만든다.

    - 하이픈으로 시작하는 경로는 ffmpeg 옵션으로 파싱될 위험 → './' prefix 추가
    - 빈 문자열은 거부 (subprocess 가 stdin 대체로 해석)
    - NUL 바이트 거부
    """
    if not filepath or "\x00" in filepath:
        raise ValueError(f"안전하지 않은 파일 경로: {filepath!r}")
    base = os.path.basename(filepath)
    if base.startswith("-"):
        # 디렉토리 구성 요소는 그대로 두고 파일명에 대해서만 ./ prefix
        d = os.path.dirname(filepath)
        if d:
            return os.path.join(d, base)
        return os.path.join(".", base)
    if filepath.startswith("-"):
        return os.path.join(".", filepath)
    return filepath


@functools.lru_cache(maxsize=1024)
def _get_video_duration_cached(filepath: str, mtime: float, size: int) -> float:
    """mtime+size 키로 ffprobe 결과 캐싱 (M6/M10: 다중 호출 시 프로세스 spawn 회피)."""
    return _probe_duration(filepath)


def get_video_duration(filepath: str) -> float:
    """ffprobe로 영상 길이(초)를 반환한다. 실패 시 0.0.

    M6/M10: 동일 파일에 대한 중복 ffprobe 호출을 mtime+size 기반 캐시로 제거.
    mtime/size 가 바뀌면 자동 무효화.
    """
    try:
        st = os.stat(filepath)
        return _get_video_duration_cached(filepath, st.st_mtime, st.st_size)
    except OSError:
        return _probe_duration(filepath)


def _probe_duration(filepath: str) -> float:
    """ffprobe 실제 호출 — 캐시 미스 시에만 실행."""
    safe = _safe_ffmpeg_path(filepath)
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        safe,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        # H17: ffprobe 실패 원인 로깅 (정적 파일이면 duration 0 정상)
        if result.returncode != 0:
            logger.warning(
                "[video] ffprobe 실패 (%s): rc=%d, %s",
                filepath, result.returncode, (result.stderr or "")[-300:],
            )
        return 0.0


def _ffmpeg_first_frame(filepath: str, out_path: str) -> bool:
    """ffmpeg으로 첫 번째 프레임을 추출한다. 성공 시 True."""
    safe_in = _safe_ffmpeg_path(filepath)
    safe_out = _safe_ffmpeg_path(out_path)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", safe_in,
            "-vf", r"select=eq(n\,0)",
            "-vframes", "1",
            "-q:v", "2",
            safe_out,
        ],
        capture_output=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def extract_keyframes(filepath: str, output_dir: str) -> list[str]:
    """
    영상/GIF에서 키프레임 이미지를 추출한다.

    - duration == 0 (1-frame GIF 등): 첫 프레임 직접 추출
    - duration <= VIDEO_SHORT_THRESHOLD: 중간 지점 프레임 1장
    - duration > VIDEO_SHORT_THRESHOLD: KEYFRAME_INTERVAL 초 간격

    Returns: 추출된 .jpg 파일 경로 목록 (시간순 정렬).
             ffmpeg 미설치 또는 추출 실패 시 빈 리스트.
    """
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(filepath)
    out_path = os.path.join(output_dir, "frame_0001.jpg")

    if duration <= 0:
        # 1-frame GIF / 메타데이터 없는 파일: seek 없이 첫 프레임 추출
        if _ffmpeg_first_frame(filepath, out_path):
            return [out_path]
        return []

    safe_in = _safe_ffmpeg_path(filepath)
    if duration <= VIDEO_SHORT_THRESHOLD:
        mid_sec = duration / 2.0
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", safe_in,
                "-ss", str(mid_sec),
                "-vframes", "1",
                "-q:v", "2",
                _safe_ffmpeg_path(out_path),
            ],
            capture_output=True,
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return [out_path]
        # seek 실패 시 첫 프레임 폴백
        if _ffmpeg_first_frame(filepath, out_path):
            return [out_path]
        return []

    out_pattern = os.path.join(output_dir, "frame_%04d.jpg")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", safe_in,
            "-vf", f"fps=1/{KEYFRAME_INTERVAL}",
            "-q:v", "2",
            _safe_ffmpeg_path(out_pattern),
        ],
        capture_output=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    frames = sorted(str(p) for p in Path(output_dir).glob("frame_*.jpg"))
    if frames:
        return frames
    # fps 필터 실패 시 첫 프레임 폴백
    if _ffmpeg_first_frame(filepath, out_path):
        return [out_path]
    return []


if __name__ == "__main__":
    import sys
    import shutil
    import tempfile

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        logger.error("Usage: python -m server.ingest.video <video_file>")
        sys.exit(1)

    path = sys.argv[1]
    tmp = tempfile.mkdtemp()
    try:
        dur = get_video_duration(path)
        logger.info("[video] 영상 길이: %.2f초", dur)
        frames = extract_keyframes(path, tmp)
        logger.info("[video] 추출된 프레임 수: %d", len(frames))
        for f in frames:
            logger.info("  %s", f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
