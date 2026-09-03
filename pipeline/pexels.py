"""Pexels에서 우주 영상 다운로드 (세로/portrait 우선).

2026-05-06 fix: Pexels "saturn rings" 검색에 wedding/engagement ring 영상이
섞여서 vision 통과 못 하고 토픽 자체가 fail하는 사고. URL slug 기반 사전
필터로 vision 호출 전에 garbage 후보를 일찍 거름.
"""
import logging
import os
import shutil
import subprocess
import random

import requests

from config import PEXELS_API_KEY, VIDEO_DURATION_SEC

log = logging.getLogger("spacebot.pexels")
PEXELS_API = "https://api.pexels.com/videos/search"

# Pexels video.url slug에 이 단어가 있고 토픽 단어가 없으면 사전 reject.
# 우주봇은 wedding/jewelry/cake/fashion 류 garbage가 "rings"/"sun" 검색에 섞여 들어옴.
_BLOCK_SLUG_WORDS = {
    "wedding", "weddings", "bride", "groom", "engagement",
    "jewelry", "necklace", "diamond", "ring", "rings",
    "cake", "cupcake", "muffin", "pastry", "donut", "icecream", "dessert",
    "love", "valentine", "couple", "kissing", "kiss",
    "manicure", "fashion", "model", "selfie", "makeup",
    "baby", "newborn", "infant", "toddler",
}

# 우주 토픽이 slug에 들어 있으면 위 차단어가 있어도 "혹시 진짜 우주 사진"이라
# 보고 통과시킴 (예: "saturn-photo-with-ring-... " 같은 진짜 사진).
_SPACE_TOPIC_TOKENS = {
    "space", "spacecraft", "galaxy", "galaxies", "galactic",
    "nebula", "nebulae", "cosmos", "cosmic", "universe", "universes",
    "planet", "planets", "planetary",
    "moon", "lunar",
    "star", "stars", "stellar", "starry", "supernova", "pulsar",
    "sun", "solar", "corona",
    "comet", "asteroid", "meteor", "meteorite",
    "saturn", "jupiter", "mars", "venus", "mercury", "neptune", "uranus", "pluto",
    "telescope", "observatory", "satellite", "satellites", "orbit", "orbital",
    "celestial", "interstellar", "milky", "blackhole", "constellation",
    "hubble", "nasa", "esa", "iss", "astronaut", "astronomy", "astronomical",
    "earth", "exoplanet",
    "rocket", "spaceship", "voyager",
}


def _media_ok(path: str, min_bytes: int = 30_000) -> bool:
    """다운로드 클립 무결성 검증 — 잘린/손상 파일이 MoviePy/ffmpeg 인코딩을 깨뜨리는 것 방지.
    ffmpeg로 전체 디코드(-xerror)해 스트림 중간 truncation('0 bytes read at frame N')을 잡는다.
    도구(ffmpeg)가 없으면 크기만 검사해 통과 — 인프라 fault로 발행을 막지 않는다(fail-open on tooling).
    (여행봇 pexels.py 원본 이식 — 3봇 동기, 2026-07-20)"""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < min_bytes:
            return False
    except OSError:
        return False
    ff = shutil.which("ffmpeg")
    if not ff:
        return True
    try:
        r = subprocess.run(
            [ff, "-v", "error", "-xerror", "-i", path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=90,
        )
        return r.returncode == 0 and not (r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return True


def _slug_tokens(url: str) -> set:
    """Pexels url slug → 단어 토큰 집합 (소문자)."""
    if not url:
        return set()
    try:
        slug = url.lower().split("/video/", 1)[1].rstrip("/")
        # 끝의 숫자 ID 제거: "saturn-rings-9168193" → "saturn-rings"
        parts = slug.split("-")
        if parts and parts[-1].isdigit():
            parts = parts[:-1]
        return {p for p in parts if p}
    except Exception:
        return set()


def _passes_slug_filter(video: dict, single_lower: str) -> bool:
    """Pexels url slug 토큰 기반 사전 필터.

    True: vision 검증으로 넘김
    False: 사전 reject (garbage 후보 일찍 거름)
    """
    tokens = _slug_tokens(video.get("url", ""))
    if not tokens:
        # url 정보 없으면 일단 vision으로 넘김 (안전 통과)
        return True
    blocked = tokens & _BLOCK_SLUG_WORDS
    if not blocked:
        return True
    # 차단어 있어도 우주 토픽이 같이 들어있으면 진짜 우주 사진일 가능성 → 통과
    space_hit = tokens & _SPACE_TOPIC_TOKENS
    if space_hit:
        return True
    if single_lower:
        single_words = {w for w in single_lower.split() if len(w) >= 3}
        if tokens & single_words:
            return True
    return False


def fetch_background(keywords: list, output_dir: str) -> str:
    """키워드 후보 → portrait 영상 → 로컬 mp4 경로."""
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY 없음")

    os.makedirs(output_dir, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}

    last_err = None
    candidates = list(keywords)
    random.shuffle(candidates)

    for kw in candidates:
        try:
            r = requests.get(
                PEXELS_API,
                headers=headers,
                params={"query": kw, "per_page": 20, "orientation": "portrait"},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            videos = data.get("videos", [])
            if not videos:
                continue
            random.shuffle(videos)
            for v in videos:
                if v.get("duration", 0) < 8:
                    continue
                files = v.get("video_files", [])
                portrait_files = [f for f in files
                                  if f.get("width", 0) <= f.get("height", 0)
                                  and (f.get("file_type") == "video/mp4" or "mp4" in (f.get("link", "")))]
                if not portrait_files:
                    portrait_files = [f for f in files if f.get("file_type") == "video/mp4"]
                portrait_files.sort(key=lambda f: abs((f.get("width") or 0) - 1080))
                if not portrait_files:
                    continue
                pick = portrait_files[0]
                url = pick["link"]
                fname = f"pexels_{v['id']}.mp4"
                out = os.path.join(output_dir, fname)
                with requests.get(url, stream=True, timeout=60) as resp:
                    resp.raise_for_status()
                    with open(out, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            fh.write(chunk)
                log.info(f"pexels bg: kw={kw} id={v['id']} dur={v.get('duration')}s")
                return out
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Pexels 배경 못 찾음: {last_err}")


def fetch_backgrounds(keywords: list, output_dir: str, n: int = 3,
                       animal_single: str = "",
                       exclude_pexels_ids: list = None,
                       ordered: bool = False,
                       hard_exclude_ids: list = None) -> list:
    """동영상 N개를 다운로드 (각 fact 구간별로 다른 영상).

    animal_single: 안전 보장 — 모든 키워드가 동물명을 포함하지 않으면
    fallback으로 동물명 단독 검색. generic 키워드("wild mammal" 등)로
    엉뚱한 동물 매칭되는 문제 방지.

    exclude_pexels_ids: 최근 N개 영상에서 사용된 Pexels video_id 리스트.
    같은 풋티지 반복으로 채널 영상이 비슷해 보이는 문제 방지 (2026-05-07
    사용자 지적: Saturn/Uranus 두 영상 풋티지 가타). 풀이 부족할 때만 일부 허용.

    ordered: True면 키워드를 셔플하지 않고 주어진 순서대로 각 키워드당 1클립씩
    수집 → fact_keywords를 fact 순서대로 넘기면 배경도 fact 순서로 매칭됨.
    (2026-07-05 per-fact 배경 매칭)
    """
    paths = []
    used_ids = set()
    blocked_soft = set(int(i) for i in (exclude_pexels_ids or []) if str(i).isdigit() or isinstance(i, int))
    blocked_hard = set(int(i) for i in (hard_exclude_ids or []) if str(i).isdigit() or isinstance(i, int))
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY 없음")
    os.makedirs(output_dir, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}

    # 토픽명 검증 안전망 — 키워드에 토픽명이 없으면 generic으로 매칭 위험
    single_lower = ""
    if animal_single:
        single_lower = animal_single.lower()
        verified = [kw for kw in keywords if single_lower in kw.lower()]
        if not verified:
            # 우주 토픽이라 "in wild" 같은 동물 fallback은 부적절 → "nasa"/"telescope"로 안전 fallback
            verified = [animal_single, f"{animal_single} space", f"{animal_single} nasa",
                        f"{animal_single} telescope"]
        keywords = verified
        log.info(f"pexels keywords (topic-locked): {keywords}")

    if ordered:
        candidates = list(keywords)  # fact 순서 유지 (셔플/증식 안 함)
    else:
        candidates = list(keywords) * 3  # 키워드 풀 늘림
        random.shuffle(candidates)

    skipped_slug = 0
    skipped_recent = 0
    # 1차: soft+hard 모두 차단. 2차: 풀 부족 시 soft만 완화, **hard는 유지**.
    for pass_idx in (0, 1):
        block_set = (blocked_soft | blocked_hard) if pass_idx == 0 else blocked_hard
        if pass_idx == 1 and len(paths) >= n:
            break
        if pass_idx == 1:
            log.warning(f"pexels: 1차 후 {len(paths)}/{n} 부족 — 최근 사용 차단 풀고 재시도")
        for kw in candidates:
            if len(paths) >= n:
                break
            try:
                r = requests.get(
                    PEXELS_API,
                    headers=headers,
                    params={"query": kw, "per_page": 20, "orientation": "portrait"},
                    timeout=20,
                )
                r.raise_for_status()
                data = r.json()
                videos = data.get("videos", [])
                random.shuffle(videos)
                for v in videos:
                    if v.get("id") in used_ids:
                        continue
                    if v.get("id") in block_set:
                        skipped_recent += 1
                        continue
                    if v.get("duration", 0) < 6:
                        continue
                    # URL slug 사전 필터 (vision 비용/TPM 절약 + garbage 사전 차단)
                    if not _passes_slug_filter(v, single_lower):
                        skipped_slug += 1
                        continue
                    files = v.get("video_files", [])
                    portrait_files = [f for f in files
                                      if f.get("width", 0) <= f.get("height", 0)
                                      and (f.get("file_type") == "video/mp4" or "mp4" in (f.get("link", "")))]
                    if not portrait_files:
                        portrait_files = [f for f in files if f.get("file_type") == "video/mp4"]
                    portrait_files.sort(key=lambda f: abs((f.get("width") or 0) - 1080))
                    if not portrait_files:
                        continue
                    pick = portrait_files[0]
                    url = pick["link"]
                    fname = f"pexels_{v['id']}.mp4"
                    out = os.path.join(output_dir, fname)
                    with requests.get(url, stream=True, timeout=60) as resp:
                        resp.raise_for_status()
                        with open(out, "wb") as fh:
                            for chunk in resp.iter_content(chunk_size=1 << 20):
                                fh.write(chunk)
                    if not _media_ok(out):
                        log.warning(f"pexels 손상/잘린 클립 폐기: id={v['id']}")
                        try:
                            os.remove(out)
                        except OSError:
                            pass
                        continue
                    paths.append(out)
                    used_ids.add(v["id"])
                    log.info(f"pexels bg[{len(paths)}]: kw={kw} id={v['id']} dur={v.get('duration')}s")
                    if len(paths) >= n:
                        break
                    if ordered:
                        break  # 키워드당 1클립 → 다음 키워드로 (fact 순서 유지)
            except Exception as e:
                log.warning(f"pexels kw={kw} 실패: {e}")
                continue

    if skipped_slug:
        log.info(f"pexels slug filter: {skipped_slug}개 garbage 후보 사전 차단")
    if skipped_recent:
        log.info(f"pexels dedup: {skipped_recent}개 최근 사용 풋티지 차단 (used_pexels.json)")
    if not paths:
        raise RuntimeError("Pexels 영상 하나도 못 찾음")
    # 호출 측에서 사용한 ID 추적 가능하도록 paths의 파일명에 video_id가 포함됨 (pexels_<id>.mp4).
    return paths


PEXELS_VIDEO_BY_ID = "https://api.pexels.com/videos/videos/{id}"


def fetch_by_id(video_id, output_dir: str):
    """대기열 대본이 잠근 Pexels 클립 1개를 id 로 내려받는다(2026-09-03 NO_OPENAI 잠금 경로).

    vision 검증을 대신해 사람이 포스터를 보고 고른 id 라, 검색·slug 필터·dedup 을 타지 않는다.
    반환: 파일 경로(pexels_<id>.mp4) / 실패 None (호출 측이 슬롯 폴백 처리). 부분 파일은 지운다.
    """
    try:
        vid = int(str(video_id).strip())
    except (TypeError, ValueError):
        log.warning(f"pexels by-id: id 형식 오류 {video_id!r}")
        return None
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY 없음")
    os.makedirs(output_dir, exist_ok=True)
    out = os.path.join(output_dir, f"pexels_{vid}.mp4")
    try:
        r = requests.get(PEXELS_VIDEO_BY_ID.format(id=vid),
                         headers={"Authorization": PEXELS_API_KEY}, timeout=20)
        r.raise_for_status()
        v = r.json()
    except Exception as e:
        log.warning(f"pexels by-id {vid}: 조회 실패 {e}")
        return None
    if not isinstance(v, dict) or str(v.get("id")) != str(vid):
        log.warning(f"pexels by-id {vid}: 응답 id 불일치 {getattr(v, 'get', lambda k: None)('id')!r}")
        return None
    files = [f for f in (v.get("video_files") or []) if isinstance(f, dict) and f.get("link")]
    portrait = [f for f in files
                if (f.get("width") or 0) <= (f.get("height") or 0)
                and (f.get("file_type") == "video/mp4" or "mp4" in (f.get("link") or ""))]
    if not portrait:
        portrait = [f for f in files if f.get("file_type") == "video/mp4"]
    if not portrait:
        log.warning(f"pexels by-id {vid}: mp4 파일 없음")
        return None
    portrait.sort(key=lambda f: abs((f.get("width") or 0) - 1080))
    try:
        with requests.get(portrait[0]["link"], stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(out, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
    except Exception as e:
        log.warning(f"pexels by-id {vid}: 다운로드 실패 {e}")
        try:
            os.remove(out)
        except OSError:
            pass
        return None
    if not _media_ok(out):
        log.warning(f"pexels by-id {vid}: 손상/잘린 클립 폐기")
        try:
            os.remove(out)
        except OSError:
            pass
        return None
    log.info(f"pexels by-id: id={vid} dur={v.get('duration')}s → {os.path.basename(out)}")
    return out
