"""
YouTube 채널 자막 일괄 추출기
yt-dlp를 사용해 공개·일부공개·비공개 영상의 자막을 텍스트 파일로 저장합니다.

의존성 설치:
    pip install yt-dlp
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ─── 사용자 설정 ─────────────────────────────────────────────────────────────
CHANNEL_URL = "https://www.youtube.com/@채널명/videos"
CHANNEL_ID  = "UCxxxxxxxxxxxxxxxxxxxxxxxx"
BROWSER     = "chrome"   # chrome / firefox / edge / safari
OUTPUT_DIR  = "transcripts"
# ─────────────────────────────────────────────────────────────────────────────

EXTRA_URLS = [
    f"https://www.youtube.com/channel/{CHANNEL_ID}/videos?view=2&sort=dd&shelf_id=0"
]


def sanitize_filename(name: str) -> str:
    """파일명에 사용 불가능한 특수문자를 제거·치환합니다."""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = name.strip(". ")
    return name[:200] if len(name) > 200 else name


def collect_video_ids(url: str) -> list[dict]:
    """
    --flat-playlist 로 영상 ID·제목 목록을 수집합니다.
    실패 시 빈 리스트를 반환합니다.
    """
    cmd = [
        "yt-dlp",
        "--cookies-from-browser", BROWSER,
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s",
        "--no-warnings",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  [경고] 목록 수집 실패: {url}")
            print(f"  {result.stderr.strip()[:300]}")
            return []

        videos = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                video_id, title = parts
                videos.append({"id": video_id.strip(), "title": title.strip()})
        return videos

    except subprocess.TimeoutExpired:
        print(f"  [경고] 목록 수집 타임아웃: {url}")
        return []
    except Exception as e:
        print(f"  [경고] 목록 수집 중 오류: {e}")
        return []


def download_subtitle(video_id: str, tmp_dir: str) -> str | None:
    """
    영상의 자막 vtt 파일을 tmp_dir 에 다운로드합니다.
    성공 시 vtt 파일 경로, 실패 시 None 반환.
    """
    cmd = [
        "yt-dlp",
        "--cookies-from-browser", BROWSER,
        "--skip-download",
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", "ko,en",
        "--sub-format", "vtt",
        "--output", os.path.join(tmp_dir, "%(id)s.%(ext)s"),
        "--no-warnings",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"    yt-dlp 오류: {result.stderr.strip()[:200]}")
            return None

        # ko 우선, 없으면 en 선택
        for lang in ("ko", "en"):
            for suffix in (f".{lang}.vtt", f".{lang}-orig.vtt"):
                candidate = os.path.join(tmp_dir, f"{video_id}{suffix}")
                if os.path.exists(candidate):
                    return candidate

        # 언어 코드 무관하게 vtt 파일이 있으면 첫 번째 반환
        for fname in os.listdir(tmp_dir):
            if fname.startswith(video_id) and fname.endswith(".vtt"):
                return os.path.join(tmp_dir, fname)

        return None

    except subprocess.TimeoutExpired:
        print("    자막 다운로드 타임아웃")
        return None
    except Exception as e:
        print(f"    자막 다운로드 중 오류: {e}")
        return None


def vtt_to_text(vtt_path: str) -> str:
    """
    vtt 파일을 읽어 타임코드·HTML 태그·중복 줄을 제거한 평문을 반환합니다.
    """
    with open(vtt_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()

    lines = raw.splitlines()
    seen: set[str] = set()
    result: list[str] = []

    for line in lines:
        # 헤더·빈 줄·타임코드 줄 건너뛰기
        if not line.strip():
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}", line):
            continue
        if re.match(r"^\d+$", line.strip()):
            continue

        # HTML 태그 제거
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if not clean:
            continue

        # 중복 줄 제거
        if clean not in seen:
            seen.add(clean)
            result.append(clean)

    return "\n".join(result)


def run():
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 영상 목록 수집 ──────────────────────────────────────────────────
    print("=" * 60)
    print("영상 목록 수집 중...")

    all_urls = [CHANNEL_URL] + EXTRA_URLS
    id_map: dict[str, str] = {}   # video_id -> title (중복 제거용)

    for url in all_urls:
        print(f"  수집: {url}")
        videos = collect_video_ids(url)
        print(f"  → {len(videos)}개 발견")
        for v in videos:
            id_map.setdefault(v["id"], v["title"])

    total = len(id_map)
    print(f"\n중복 제거 후 총 {total}개 영상\n")

    if total == 0:
        print("처리할 영상이 없습니다. CHANNEL_URL과 CHANNEL_ID를 확인하세요.")
        sys.exit(1)

    # ── 2. 영상별 자막 추출 ────────────────────────────────────────────────
    failed: list[dict] = []

    for idx, (video_id, title) in enumerate(id_map.items(), start=1):
        safe_title = sanitize_filename(title)
        out_path = out_dir / f"{safe_title}_{video_id}.txt"
        print(f"[{idx}/{total}] {title} ({video_id})")

        # 이미 처리된 파일이면 건너뜀
        if out_path.exists():
            print("    이미 존재 — 건너뜀")
            continue

        with tempfile.TemporaryDirectory() as tmp_dir:
            vtt_path = download_subtitle(video_id, tmp_dir)

            if vtt_path is None:
                print("    자막 없음 — failed.json에 기록")
                failed.append({"id": video_id, "title": title})
                continue

            text = vtt_to_text(vtt_path)
            if not text.strip():
                print("    자막 내용 없음 — failed.json에 기록")
                failed.append({"id": video_id, "title": title, "reason": "empty"})
                continue

            out_path.write_text(text, encoding="utf-8")
            print(f"    저장 완료 → {out_path.name}")

    # ── 3. 결과 기록 ───────────────────────────────────────────────────────
    failed_path = out_dir / "failed.json"
    failed_path.write_text(
        json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    success = total - len(failed)
    print("\n" + "=" * 60)
    print(f"완료: 성공 {success}개 / 실패 {len(failed)}개 / 전체 {total}개")
    print(f"실패 목록: {failed_path}")


if __name__ == "__main__":
    run()
