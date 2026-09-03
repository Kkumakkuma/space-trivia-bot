#!/usr/bin/env python3
"""Space Trivia 60s — 1분 우주 트리비아 영상 자동 발행.

흐름:
1. 대본 확보 — ① --animal 강제 → ② queue/pending 대기열(사람이 쓴 대본) →
   ③ 대기열 비었고 QUEUE_ONLY=1 이면 QUEUE_EMPTY 찍고 종료 → ④ GPT 동적 픽(구 경로)
2. Pexels/NASA 소재 수집 + vision 검증(fail-closed) — fact당 CLIPS_PER_FACT개까지
3. edge-tts로 영문 내레이션 6개 생성 (intro/fact x4/outro)
4. MoviePy로 1분 영상 합성 (배경 하드컷 + 비네트 + BIG HOOK + 자막 시간차 + brand)
5. 첫 배경 클립 프레임으로 썸네일 (fail-soft)
6. 영문 메인 + 7개국 localizations (대본에 한국어 메타가 있으면 ko 는 그 값 사용)
7. YouTube Shorts 업로드 (privacy=public, defaultLanguage=en)
8. videoId 를 받은 뒤에만 대기열 파일을 queue/done 으로 이동 + 사용 이력 기록
"""
import argparse
import io
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone

if sys.platform == "win32" and "pytest" not in sys.modules:  # pytest 캡처를 깨지 않는다
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as _cfg
from config import ANIMALS, BASE_DIR, OUTPUT_DIR

# 콘텐츠 이력 설정 (없는 config에도 안전한 기본값)
CONTENT_HISTORY_PATH = getattr(_cfg, "CONTENT_HISTORY", os.path.join(BASE_DIR, "content_history.json"))
CONTENT_WINDOW_DAYS = getattr(_cfg, "CONTENT_WINDOW_DAYS", 60)
HISTORY_MIN_CANDIDATES = getattr(_cfg, "HISTORY_MIN_CANDIDATES", 8)
HISTORY_MIN_WINDOW_DAYS = getattr(_cfg, "HISTORY_MIN_WINDOW_DAYS", 7)
CLIP_DEDUP_DAYS = getattr(_cfg, "CLIP_DEDUP_DAYS", 30)
RECENT_FORMAT_KEEP = getattr(_cfg, "RECENT_FORMAT_KEEP", 2)
USE_NASA_MEDIA = getattr(_cfg, "USE_NASA_MEDIA", False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("spacebot")

USED_PATH = os.path.join(BASE_DIR, "used_animals.json")
USED_KEEP = 30  # 직전 30개 토픽 회피 + 코드 레벨 강제 dedup (토픽 풀 ~40개, 중복 방지)

# Pexels 풋티지 dedup (2026-05-07 우주봇 사용자 지적 동기화: 최근 영상 풋티지 가타)
USED_PEXELS_PATH = os.path.join(BASE_DIR, "used_pexels.json")
USED_PEXELS_KEEP = 30  # 직전 30개 영상 풋티지 차단 (1편당 3개 풋티지 × 10편 분량)


def _load_used():
    if not os.path.exists(USED_PATH):
        return []
    try:
        return json.load(open(USED_PATH, encoding="utf-8"))
    except Exception:
        return []


def _save_used(used):
    json.dump(used[-USED_KEEP:], open(USED_PATH, "w", encoding="utf-8"))


def _load_used_pexels() -> list:
    if not os.path.exists(USED_PEXELS_PATH):
        return []
    try:
        return [int(i) for i in json.load(open(USED_PEXELS_PATH, encoding="utf-8")) if str(i).isdigit() or isinstance(i, int)]
    except Exception:
        return []


def _save_used_pexels(used: list):
    json.dump(used[-USED_PEXELS_KEEP:], open(USED_PEXELS_PATH, "w", encoding="utf-8"))


def _extract_pexels_id(path: str):
    """pexels_<id>.mp4 파일명에서 id 추출 (int). 실패 시 None."""
    import re as _re
    base = os.path.basename(path or "")
    m = _re.match(r"pexels_(\d+)\.mp4$", base)
    return int(m.group(1)) if m else None


def _extract_clip_id(path: str):
    """pexels_<id>.mp4 파일명에서 pexels id 추출 (str digit). 실패 시 None."""
    import re as _re
    base = os.path.basename(path or "")
    m = _re.match(r"pexels_(\d+)\.mp4$", base)
    return m.group(1) if m else None


# === 대본 대기열 (Claude 가 쓴 대본을 그대로 발행) =================================
# GPT 동적 픽/후처리를 타지 않는 독립 갈래다. pending 에 넣어두면 cron 이 파일명 순으로
# 집어가고, **실제 업로드가 videoId 를 받은 뒤에만** done 으로 옮긴다.
QUEUE_DIR = os.path.join(BASE_DIR, "queue")
QUEUE_PENDING_DIR = os.path.join(QUEUE_DIR, "pending")
QUEUE_DONE_DIR = os.path.join(QUEUE_DIR, "done")
QUEUE_FAILED_DIR = os.path.join(QUEUE_DIR, "failed")
QUEUE_MAX_TRIES = 3
QUEUE_EMPTY_MARKER = "QUEUE_EMPTY"  # 워크플로 알림이 grep 하는 ASCII 마커

_QUEUE_KEY_RE = re.compile(r"^[a-z0-9_]+$")
_QUEUE_STR_FIELDS = (
    "animal_key", "display", "single", "category", "angle", "format",
    "title_en", "description_en", "hook", "intro", "outro", "comment",
)


def queue_only_mode() -> bool:
    """env QUEUE_ONLY 가 켜져 있으면 대기열이 빌 때 GPT 갈래로 흘리지 않는다."""
    return (os.environ.get("QUEUE_ONLY", "") or "").strip().lower() not in ("", "0", "false", "no")


def queue_pending_files() -> list:
    if not os.path.isdir(QUEUE_PENDING_DIR):
        return []
    return [os.path.join(QUEUE_PENDING_DIR, f)
            for f in sorted(os.listdir(QUEUE_PENDING_DIR)) if f.endswith(".json")]


QUEUE_UNLOCKED_PREFIX = "[unlocked] "  # NO_OPENAI 잠금 정보(clips/localizations)만 아직 없는 대본 — failed 가 아니라 pending 유지


def validate_queue_meta(data, filename: str) -> list:
    """대기열 대본 스키마 검증. 문제 사유 리스트를 돌려준다(빈 리스트 = 통과).

    garbage 발행 0 이 최우선이라 여기서 막히면 렌더까지 가지 않고 failed 로 보낸다.
    """
    r = []
    if not isinstance(data, dict):
        return ["최상위가 JSON object 가 아님"]
    if (data.get("author") or "") != "claude":
        r.append('author 가 "claude" 가 아님')

    for k in _QUEUE_STR_FIELDS:
        v = data.get(k)
        if not isinstance(v, str) or not v.strip():
            r.append(f"{k}: 비어있거나 문자열이 아님")

    key = data.get("animal_key") if isinstance(data.get("animal_key"), str) else ""
    if key and not _QUEUE_KEY_RE.match(key):
        r.append(f"animal_key 형식 위반(^[a-z0-9_]+$): {key!r}")
    stem = os.path.splitext(os.path.basename(filename))[0]
    slug = stem.split("_", 1)[1] if "_" in stem and stem.split("_", 1)[0].isdigit() else stem
    if key and slug != key:
        r.append(f"파일명 슬러그({slug!r}) != animal_key({key!r})")

    title = data.get("title_en")
    if isinstance(title, str) and len(title) > 95:
        r.append(f"title_en 이 95자 초과({len(title)}자)")

    facts = data.get("facts")
    if not isinstance(facts, list) or len(facts) != 4 or not all(
            isinstance(f, str) and f.strip() for f in facts):
        r.append("facts 가 비어있지 않은 문자열 4개가 아님")

    fkw = data.get("fact_keywords")
    if not isinstance(fkw, list) or len(fkw) != 4 or not all(
            isinstance(f, str) and f.strip() for f in fkw):
        r.append("fact_keywords 가 비어있지 않은 문자열 4개가 아님")

    pkw = data.get("pexels_keywords")
    if not isinstance(pkw, list) or len(pkw) < 5 or not all(
            isinstance(f, str) and f.strip() for f in pkw):
        r.append("pexels_keywords 가 비어있지 않은 문자열 5개 이상이 아님")

    tags = data.get("tags")
    if not isinstance(tags, list) or not tags or not all(
            isinstance(t, str) and t.strip() for t in tags):
        r.append("tags 가 비어있지 않은 문자열 배열이 아님")

    for k in ("title_ko", "description_ko"):  # 선택 키 — 있으면 문자열이어야 한다
        v = data.get(k)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            r.append(f"{k}: 값이 있으나 문자열이 아니거나 비어있음")

    # 2026-09-03 NO_OPENAI: clips(잠금 Pexels id)·localizations(7개국) 는 NO_OPENAI 면 필수, 아니면 선택.
    n_facts = len(facts) if isinstance(facts, list) and facts else 4
    clips = data.get("clips")
    if clips is not None or _cfg.no_openai_mode():
        ok = (isinstance(clips, list) and len(clips) == n_facts and all(
            isinstance(sl, list) and 1 <= len(sl) <= 2
            and all(isinstance(c, int) and not isinstance(c, bool) and c > 0 for c in sl)
            for sl in clips))
        if not ok:
            r.append((QUEUE_UNLOCKED_PREFIX if clips is None else "")
                     + f"clips 가 facts 수({n_facts})만큼의 [양의 정수 id 1~2개] 슬롯이 아님")
        else:
            flat = [c for sl in clips for c in sl]
            if len(flat) != len(set(flat)):
                r.append("clips 에 같은 id 가 두 번 들어감(영상 내 중복)")
    locs = data.get("localizations")
    if locs is not None or _cfg.no_openai_mode():
        need = ("ko", "ja", "es", "pt", "de", "fr", "hi")
        good = isinstance(locs, dict) and all(
            isinstance(locs.get(c), dict)
            and isinstance(locs[c].get("title"), str) and locs[c]["title"].strip()
            and len(locs[c]["title"]) <= 95
            and isinstance(locs[c].get("description"), str) and locs[c]["description"].strip()
            for c in need)
        if not good:
            r.append((QUEUE_UNLOCKED_PREFIX if locs is None else "")
                     + "localizations 가 7개 언어(ko/ja/es/pt/de/fr/hi) 모두 title(≤95자)·description 문자열을 갖지 않음")
    caps = data.get("captions")
    if caps is not None:
        n_lines = n_facts + 2  # TTS 라인 = intro + facts + outro
        good = isinstance(caps, dict) and caps and all(
            isinstance(v, list) and len(v) == n_lines and all(isinstance(t, str) and t.strip() for t in v)
            for v in caps.values())
        if not good:
            r.append(f"captions 가 언어별 {n_lines}줄(intro+facts+outro) 비어있지 않은 문자열 배열이 아님")
    return r


def _queue_move_failed(path: str, reasons: list):
    """검증 실패 파일을 failed/ 로 옮기고 같은 이름 .reason.txt 에 사유를 남긴다."""
    os.makedirs(QUEUE_FAILED_DIR, exist_ok=True)
    name = os.path.basename(path)
    dest = os.path.join(QUEUE_FAILED_DIR, name)
    try:
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(path, dest)
    except Exception as e:
        log.warning(f"[queue] failed 이동 실패 {name}: {e}")
        return
    reason_path = os.path.join(QUEUE_FAILED_DIR, os.path.splitext(name)[0] + ".reason.txt")
    try:
        with open(reason_path, "w", encoding="utf-8") as f:
            print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), file=f)
            for r in reasons:
                print(f"- {r}", file=f)
    except Exception as e:
        log.warning(f"[queue] reason 파일 기록 실패 {name}: {e}")
    log.error(f"[queue] 스키마 검증 실패 → failed/{name}: {reasons}")


def queue_mark_done(path: str, video_id: str, video_url: str):
    """업로드가 videoId 를 받은 뒤에만 호출. pending → done 이동 후 발행 정보를 덧붙인다.

    순서가 중요하다(codex 지적): '쓰고 나서 지우기'로 하면 삭제가 실패했을 때 pending 에
    원본이 남아 **다음 cron 이 같은 대본을 또 발행**한다. 먼저 옮겨(os.replace = 원자적
    rename) pending 에서 사라지게 한 뒤, 옮긴 파일에 메타를 덧쓴다. 덧쓰기가 실패해도
    최악이 '발행 정보 없는 done 파일'이라 중복 발행은 일어나지 않는다.
    """
    os.makedirs(QUEUE_DONE_DIR, exist_ok=True)
    name = os.path.basename(path)
    dest = os.path.join(QUEUE_DONE_DIR, name)
    try:
        os.replace(path, dest)  # 같은 볼륨 내 원자적 이동
    except Exception as e:
        log.error(f"[queue] done 이동 실패 — pending 에 남음(중복 발행 위험!) {name}: {e}")
        return
    try:
        data = json.load(open(dest, encoding="utf-8"))
        data["published_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data["video_id"] = video_id
        data["video_url"] = video_url
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            print(file=f)
        log.info(f"[queue] done/{name} 이동 완료 (video_id={video_id})")
    except Exception as e:
        log.warning(f"[queue] done/{name} 발행정보 기록 실패(이동 자체는 완료): {e}")


def try_queue_script(used_pexels: list, recent_clips, budget: list = None):
    """대기열에서 발행 가능한 대본 1건 확보.

    파일명 순으로 최대 QUEUE_MAX_TRIES 개까지 시도한다.
    - 스키마 위반 → failed/ 이동(시도 횟수에 안 셈)
    - 소재 매칭 실패(RuntimeError) → pending 에 그대로 두고 다음 파일
    - 그 외 예외(vision 오류·네트워크)는 그대로 전파 → 실행 실패 → 다음 cron 재시도

    budget: [남은 시도 횟수] 형태의 1칸 리스트. 호출부(run_once)가 '내레이션 길이 초과'로
    재호출할 때도 같은 리스트를 넘겨 **한 실행의 총 시도 횟수를 3개로 묶는다**.
    반환: (meta, animal_key, work_dir, bg_groups, bg_clip_ids, queue_path) 또는 None
    """
    if budget is None:
        budget = [QUEUE_MAX_TRIES]
    files = queue_pending_files()
    if not files:
        return None
    log.info(f"[queue] pending {len(files)}건 — 남은 시도 {budget[0]}건")
    for path in files:
        if budget[0] <= 0:
            log.warning(f"[queue] 시도 횟수({QUEUE_MAX_TRIES}) 소진 — 중단")
            break
        name = os.path.basename(path)
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            _queue_move_failed(path, [f"JSON 파싱 실패: {e}"])
            continue
        reasons = validate_queue_meta(data, name)
        if reasons and all(x.startswith(QUEUE_UNLOCKED_PREFIX) for x in reasons):
            # 아직 clips/localizations 를 못 채운 대본 — 고장이 아니라 미완성. pending 에 두고 다음 파일로.
            log.warning(f"[queue] {name}: 잠금 정보 없음(NO_OPENAI) → pending 유지, 다음 대본 시도")
            continue
        if reasons:
            _queue_move_failed(path, reasons)
            continue

        budget[0] -= 1
        meta = dict(data)
        key = meta["animal_key"]
        meta["use_nasa"] = USE_NASA_MEDIA
        wd = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), key)
        os.makedirs(wd, exist_ok=True)
        log.info(f"[queue {QUEUE_MAX_TRIES - budget[0]}/{QUEUE_MAX_TRIES}] {name} — "
                 f"{meta['display']} ({meta['category']})")
        try:
            groups, cids = fetch_verified_backgrounds(
                meta, wd, exclude_pexels_ids=used_pexels, exclude_clip_ids=recent_clips)
        except RuntimeError as e:
            log.warning(f"[queue] 소재 매칭 실패 — pending 유지하고 다음 파일: {name}: {e}")
            continue
        return meta, key, wd, groups, cids, path
    return None


def _fetch_locked_backgrounds(meta: dict, work_dir: str, locked: list, n: int,
                              exclude_pexels_ids=None, exclude_clip_ids=None):
    """대본이 잠근 Pexels id 로만 배경을 채운다 — vision·NASA·AI 폴백 전부 건너뜀(2026-09-03 NO_OPENAI).

    id 는 사람이 포스터를 보고 고른 것이라 최근 사용 dedup(used_pexels/recent_clips)보다 우선한다(경고만).
    기존 안전선은 유지: 슬롯 min(n,3)개 이상 채워져야 하고, 빈 슬롯은 이웃 슬롯 클립을 빌린다.
    반환 (bg_groups, clip_ids) — fetch_verified_backgrounds 와 동일 형식.
    """
    import time as _time
    from pipeline import pexels
    _t0 = _time.monotonic()
    if not isinstance(locked, list) or len(locked) != n:
        got = len(locked) if isinstance(locked, list) else "?"
        raise RuntimeError(f"clips 슬롯 수({got}) != facts({n})")
    soft = {str(c) for c in (exclude_pexels_ids or [])}
    hard = {str(c) for c in (exclude_clip_ids or set())}
    want = max(1, int(getattr(_cfg, "CLIPS_PER_FACT", 1)))
    slots = [[] for _ in range(n)]
    seen = set()
    for i, ids in enumerate(locked):
        for cid in list(ids or [])[:want]:
            cid = str(cid)
            if cid in seen:
                log.warning(f"clips fact[{i}] id={cid} 영상 내 중복 → 건너뜀")
                continue
            if cid in hard or cid in soft:
                log.warning(f"clips fact[{i}] id={cid} 는 최근 사용 풋티지 — 대본 잠금이 우선(허용)")
            path = pexels.fetch_by_id(cid, work_dir)
            if path:
                slots[i].append((path, cid))
                seen.add(cid)
    filled_idx = [i for i in range(n) if slots[i]]
    min_pass = min(n, 3)
    if len(filled_idx) < min_pass:
        for i in filled_idx:
            for path, _ in slots[i]:
                try:
                    os.remove(path)
                except Exception:
                    pass
        raise RuntimeError(f"잠금 클립 다운로드 부족 ({len(filled_idx)}/{n}, min={min_pass})")
    bg_groups = []
    for i in range(n):
        if slots[i]:
            bg_groups.append(list(slots[i]))
            continue
        j = min(filled_idx, key=lambda x: (abs(x - i), x))
        log.warning(f"fact[{i}] 잠금 클립 없음 → fact[{j}] 클립 재사용 (fact↔영상 정렬 유지)")
        bg_groups.append([slots[j][0]])
    ids = list(dict.fromkeys(cid for i in filled_idx for _, cid in slots[i]))
    log.info(f"배경 확보(잠금): fact {len(filled_idx)}/{n} 슬롯, 컷 {sum(len(g) for g in bg_groups)}개 "
             f"(슬롯별 {[len(g) for g in bg_groups]}, vision 0회)")
    log.info(f"[timing] media={_time.monotonic() - _t0:.1f} (locked)")
    return bg_groups, ids


def _meta_localizations(meta: dict):
    """대본이 준 localizations({lang: {title, description}}) 를 YouTube 형식으로 정리. 없으면 None."""
    raw = meta.get("localizations")
    if not isinstance(raw, dict) or not raw:
        return None
    out = {}
    for code, e in raw.items():
        if not isinstance(e, dict):
            continue
        title = (e.get("title") or "").strip()
        desc = (e.get("description") or "").strip()
        if title:
            out[str(code)] = {"title": title[:95], "description": desc[:4900]}
    return out or None


def fetch_verified_backgrounds(meta: dict, work_dir: str,
                                oversample: int = 2,
                                exclude_pexels_ids: list = None,
                                exclude_clip_ids=None):
    """비전 검증(fail-closed) 통과 배경을 **fact 슬롯 단위**로 확보 (배경 수 = fact 수 지향).

    각 slot i(=fact i)에 대해 소스 순서대로 시도 → 슬롯 인덱스 보존(fact와 영상 밀림 방지):
      우주봇(use_nasa): NASA 영상 → Pexels → NASA 이미지 Ken Burns
      동물봇: Pexels
    미충족 슬롯은 AI 이미지 폴백으로 채우되 **AI도 vision 검증 필수**(fail-closed 우회 금지).
    모든 클립은 서로 다름(영상 내 중복 없음). 최종 검증 통과분 < min_pass면 RuntimeError.

    2026-09-03 컷 페이싱: 슬롯마다 config.CLIPS_PER_FACT개까지 모은다(모자라면 1개 폴백).

    clip id 태깅(L): NASA는 "nasa:<id>", Pexels는 숫자 문자열, AI는 "".
    반환: (bg_groups, clip_ids).
      bg_groups = [[(path, clip_id), ...], ...]  — 바깥 리스트가 fact 순서, 안쪽이 그 fact의 컷들
      clip_ids  = 위 그룹을 평탄화한 clip id 리스트(사용 기록/dedup용)
    """
    import time as _time
    from pipeline import pexels, verify_visual

    _t_media0 = _time.monotonic()
    single = meta.get("single", "")
    display = meta.get("display", "")
    category = meta.get("category", "")
    facts = meta.get("facts") or []
    n = max(1, len(facts))  # 배경 수 = fact 수

    # 대기열 대본(author=claude)이 clips 로 Pexels id 를 잠갔으면 vision 없이 그 id 만 쓴다(NO_OPENAI).
    if meta.get("author") == "claude" and meta.get("clips"):
        return _fetch_locked_backgrounds(meta, work_dir, meta.get("clips"), n,
                                         exclude_pexels_ids=exclude_pexels_ids,
                                         exclude_clip_ids=exclude_clip_ids)

    ordered_kw = [k for k in (meta.get("fact_keywords") or []) if isinstance(k, str) and k.strip()]
    generic_kw = [g for g in (meta.get("pexels_keywords") or []) if isinstance(g, str) and g.strip()]
    while len(ordered_kw) < n:
        ordered_kw.append(generic_kw[len(ordered_kw) % len(generic_kw)] if generic_kw else (single or "nature"))

    # exclude 분류(L): "nasa:" 접두어 → NASA hard, 숫자 → Pexels hard. exclude_pexels_ids는 soft.
    excl = {str(c) for c in (exclude_clip_ids or set())}
    hard_nasa = {c[len("nasa:"):] for c in excl if c.startswith("nasa:")}
    hard_pexels = [int(c) for c in excl if c.isdigit()]
    soft_pexels = [int(c) for c in (exclude_pexels_ids or []) if str(c).isdigit()]

    want = max(1, int(getattr(_cfg, "CLIPS_PER_FACT", 1)))  # fact당 목표 클립 수(부족하면 1개 폴백)
    slots = [[] for _ in range(n)]          # slots[i] = [(path, clip_id), ...]
    rejected = []
    used_clip_set = set()  # 이번 영상 내 중복 방지 (태깅된 id)
    verify_count = [0]

    nasa = None
    if meta.get("use_nasa"):
        try:
            from pipeline import nasa_media as nasa
        except Exception as e:
            log.warning(f"nasa_media 임포트 실패 — NASA 소스 skip: {e}")
            nasa = None

    def _verify(path):
        if not path or not os.path.exists(path):
            return False
        if verify_count[0] > 0:
            _time.sleep(0.5)  # vision TPM burst 회피
        verify_count[0] += 1
        return verify_visual.verify_animal(path, single, animal_display=display,
                                           animal_category=category)

    def _assign(i, path, cid):
        slots[i].append((path, cid))
        if cid:
            used_clip_set.add(cid)

    def _slot_paths(i):
        return [p for p, _ in slots[i]]

    def _used_nasa():
        return hard_nasa | {c[len("nasa:"):] for c in used_clip_set if c.startswith("nasa:")}

    def _used_pexels():
        return [int(c) for c in used_clip_set if c.isdigit()]

    # === 슬롯별 채움 (NASA영상 → Pexels → NASA이미지) — 슬롯 인덱스 = fact 인덱스 ===
    for i in range(n):
        kw = ordered_kw[i]
        slot_kw = [kw] + [g for g in generic_kw if g != kw]

        # 1) NASA 영상 (슬롯당 1개까지)
        if nasa is not None and len(slots[i]) < want:
            p, nid = nasa.fetch_video(kw, work_dir, exclude_ids=_used_nasa())
            if p:
                if _verify(p):
                    _assign(i, p, f"nasa:{nid}")
                else:
                    rejected.append(p)

        # 2) Pexels — 남은 수만큼 (slot 키워드 순서, hard/soft exclude 분리)
        need = want - len(slots[i])
        if need > 0:
            try:
                cands = pexels.fetch_backgrounds(
                    slot_kw, work_dir, n=oversample + need,
                    animal_single=single, ordered=True,
                    exclude_pexels_ids=soft_pexels + _used_pexels(),
                    hard_exclude_ids=hard_pexels + _used_pexels(),
                )
            except RuntimeError:
                cands = []
            for cand in cands:
                if len(slots[i]) >= want:
                    break
                cid = _extract_clip_id(cand)
                if cid and cid in used_clip_set:
                    rejected.append(cand)
                    continue
                if _verify(cand):
                    _assign(i, cand, cid or "")
                else:
                    rejected.append(cand)
            # 이 슬롯에서 못 쓴 나머지 후보는 정리 대상
            taken = set(_slot_paths(i))
            for other in cands:
                if other not in taken and other not in rejected:
                    rejected.append(other)

        # 3) NASA 이미지 → Ken Burns (파일명 nasa_img_* — 영상과 분리, H)
        #    슬롯이 아예 비었을 때만 — 정지 이미지는 컷 페이싱 소재로 쓰지 않는다.
        if nasa is not None and not slots[i]:
            png, nid = nasa.fetch_image(kw, work_dir, exclude_ids=_used_nasa())
            if png:
                from pipeline import image_to_video
                safe = ("".join(ch if ch.isalnum() else "_" for ch in str(nid))[:40]) if nid else f"kb_{i}"
                mp4 = os.path.join(work_dir, f"nasa_img_{safe}.mp4")
                try:
                    image_to_video.png_to_video(png, mp4, duration=10.0)
                except Exception as e:
                    log.warning(f"NASA 이미지 Ken Burns 실패: {e}")
                    mp4 = None
                finally:
                    try:
                        os.remove(png)
                    except Exception:
                        pass
                if mp4 and _verify(mp4):
                    _assign(i, mp4, f"nasa:{nid}")
                elif mp4:
                    rejected.append(mp4)
        # slot 미충족 → 빈 리스트 유지

    # === AI 이미지 폴백 (K: 반드시 vision 검증) — 완전히 빈 슬롯만 채움 ===
    unfilled = [i for i in range(n) if not slots[i]]
    if unfilled:
        log.warning(f"미충족 슬롯 {len(unfilled)}개 → AI 이미지 폴백 (vision 검증 필수)")
        try:
            from pipeline import image_fallback, image_to_video
            pngs = image_fallback.generate_fallback_images(meta, len(unfilled), work_dir)
            mp4s = image_to_video.pngs_to_videos(pngs, work_dir, duration=10.0) if pngs else []
            mi = 0
            for i in unfilled:
                if mi >= len(mp4s):
                    break
                cand = mp4s[mi]
                mi += 1
                if _verify(cand):
                    _assign(i, cand, "")
                else:
                    rejected.append(cand)
        except Exception as e:
            log.warning(f"AI 이미지 폴백 실패: {e}")

    # === 결과 정리 ===
    # 미충족 슬롯을 그냥 빼버리면 리스트가 앞으로 당겨져 **fact 와 영상이 밀린다**
    # (codex 지적: 4 facts / 3 슬롯이면 composer 가 균등분할 폴백으로 떨어져 정렬이 통째로 깨짐).
    # 그래서 슬롯 개수(n)는 유지하고, 빈 슬롯은 가장 가까운 채워진 슬롯의 클립 1개를 빌려 채운다.
    filled_idx = [i for i in range(n) if slots[i]]
    accepted = {p for i in filled_idx for p, _ in slots[i]}
    for r in rejected:
        if r in accepted:  # H: 채택된 경로는 삭제 skip
            continue
        try:
            os.remove(r)
        except Exception:
            pass

    min_pass = min(n, 3)  # garbage 0: 최소 통과 배경 수(기존 바 유지)
    if len(filled_idx) < min_pass:
        for p in accepted:
            try:
                os.remove(p)
            except Exception:
                pass
        raise RuntimeError(
            f"비전 검증 통과 배경 부족 ({len(filled_idx)}/{n}, min={min_pass}) — 다른 토픽 픽 필요"
        )

    bg_groups = []
    for i in range(n):
        if slots[i]:
            bg_groups.append(list(slots[i]))
            continue
        j = min(filled_idx, key=lambda x: (abs(x - i), x))
        log.warning(f"fact[{i}] 소재 없음 → fact[{j}] 클립 재사용 (fact↔영상 정렬 유지)")
        bg_groups.append([slots[j][0]])
    # 사용 기록용 id 는 실제 검증분만(빌려온 중복 제외), 순서 유지 dedup
    filled_ids = list(dict.fromkeys(cid for i in filled_idx for _, cid in slots[i]))
    n_clips = sum(len(g) for g in bg_groups)
    log.info(f"배경 확보: fact {len(filled_idx)}/{n} 슬롯 검증 통과, 컷 {n_clips}개 "
             f"(fact당 목표 {want}개, 슬롯별 {[len(g) for g in bg_groups]})")
    log.info(f"[timing] media={_time.monotonic() - _t_media0:.1f} (vision {verify_count[0]}회)")
    return bg_groups, filled_ids


def run_once(forced_animal: str = None, dry_run: bool = False, no_upload: bool = False) -> dict:
    from pipeline import animals, pexels, tts, composer, translator
    from pipeline import history as hist_mod

    # 2026-07-17: 필수 API 키 fail-fast — 시크릿 누락/오타 상태로 vision 0회 검증 발행이
    # 조용히 성공하는 것을 차단(verify_visual도 무키 reject하지만 여기서 시작 자체를 막아
    # GPT/Pexels 호출 낭비 없이 명확한 에러로 실패시킨다).
    _required = ("OPENAI_API_KEY", "PEXELS_API_KEY")
    if _cfg.no_openai_mode():
        # 2026-09-03: OpenAI 를 한 번도 부르지 않는 모드 — 대본이 clips/localizations/captions 를 제공한다.
        # GPT 갈래가 열려 있으면 안 되므로 QUEUE_ONLY 가 같이 켜져 있어야 한다.
        if not queue_only_mode():
            raise RuntimeError("NO_OPENAI=1 인데 QUEUE_ONLY 가 아님 — GPT 갈래 차단을 위해 QUEUE_ONLY=1 필요")
        log.info("NO_OPENAI=1 — OpenAI 무호출 모드(클립 잠금·localizations·captions 는 대본 제공)")
        _required = ("PEXELS_API_KEY",)
    _missing = [k for k in _required if not (getattr(_cfg, k, "") or "").strip()]
    if _missing:
        raise RuntimeError(f"필수 API 키 누락: {', '.join(_missing)} — fail-fast (무검증 발행 차단)")

    used = _load_used()
    used_pexels = _load_used_pexels()

    # 콘텐츠 이력 로드. 없을 때만 used_animals.json에서 마이그레이션 —
    # 단, .corrupt 백업이 있으면(손상 감지) 재마이그레이션 금지(이력 전량 소실 방지, A).
    hist = hist_mod.load_history(CONTENT_HISTORY_PATH)
    if not hist and used:
        if os.path.exists(CONTENT_HISTORY_PATH + hist_mod.CORRUPT_SUFFIX):
            log.warning("content_history 손상 백업(.corrupt) 존재 → 재마이그레이션 skip (이력 소실 방지)")
        else:
            hist = hist_mod.migrate_from_used(used, CONTENT_WINDOW_DAYS)
            hist_mod.save_history(CONTENT_HISTORY_PATH, hist)
            log.info(f"content_history.json 없음 → used_animals.json {len(used)}건 마이그레이션")

    # (D) 동적 GPT 픽/hard-block용 exclude = 완화 없는 시간창 ∪ used[-KEEP:] 전체
    dynamic_exclude, _ = hist_mod.compute_exclude(
        hist, used, USED_KEEP, CONTENT_WINDOW_DAYS, pool=None)
    dynamic_exclude_list = sorted(dynamic_exclude)
    # (D) 정적 풀 fallback 사전 픽용 exclude = 후보 8개 미만이면 창 완화
    fallback_exclude, eff_window = hist_mod.compute_exclude(
        hist, used, USED_KEEP, CONTENT_WINDOW_DAYS,
        pool=list(ANIMALS.keys()), min_candidates=HISTORY_MIN_CANDIDATES,
        min_window=HISTORY_MIN_WINDOW_DAYS)
    avoid_formats = hist_mod.recent_formats(hist, RECENT_FORMAT_KEEP)
    recent_clips = hist_mod.recent_clip_ids(hist, CLIP_DEDUP_DAYS)  # 태깅된 id (nasa:/숫자)
    log.info(f"dynamic_exclude {len(dynamic_exclude_list)}개 / fallback_exclude {len(fallback_exclude)}개 (window={eff_window:.0f}d) / avoid_formats={avoid_formats} / recent_clips={len(recent_clips)}")

    # 대본 확보 → TTS → 합성. 대기열 갈래는 '내레이션이 길어 음성이 잘리는' 대본을 만나면
    # 그 대본을 queue/failed 로 빼고 다음 pending 으로 재시도한다(총 시도는 queue_budget 로 3건 제한).
    queue_budget = [QUEUE_MAX_TRIES]
    while True:
        bg_clip_ids = []
        queue_path = None  # 대기열 대본을 쓴 경우 그 pending 파일 경로 (업로드 성공 후 done 이동)

        # 우선순위: ① CLI 강제(--animal) → ② 대기열 → ③ QUEUE_ONLY 이면 건너뜀 → ④ GPT 갈래
        queue_pick = (None if forced_animal
                      else try_queue_script(used_pexels, recent_clips, queue_budget))

        if forced_animal:
            meta = animals._legacy_meta(forced_animal)
            try:
                meta.update(animals.expand_legacy_to_rich(meta))
            except Exception as e:
                log.warning(f"forced expand 실패 → 템플릿 유지: {e}")
            animal_key = meta["animal_key"]
            work_dir = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), animal_key)
            os.makedirs(work_dir, exist_ok=True)
            meta["use_nasa"] = USE_NASA_MEDIA
            bg_groups, bg_clip_ids = fetch_verified_backgrounds(
                meta, work_dir, exclude_pexels_ids=used_pexels, exclude_clip_ids=recent_clips)
        elif queue_pick:
            # 대기열 대본은 GPT 후처리(reground/factcheck/retitle/differentiate/전역 novelty)를
            # 타지 않는다. dynamic_exclude(GPT 중복 회피) 검사도 우회 — 대본 관리는 사람이 한다.
            meta, animal_key, work_dir, bg_groups, bg_clip_ids, queue_path = queue_pick
            log.info(f"[queue] 대기열 대본 채택: {os.path.basename(queue_path)} "
                     f"(GPT 후처리·중복검사 우회)")
        elif queue_only_mode():
            if queue_pending_files():
                # 대본은 있는데 소재를 못 구한 상태 — GPT 갈래로 흘리지 않고 실패시켜 다음 cron 재시도.
                raise RuntimeError(
                    "QUEUE_ONLY=1 — 대기열 대본은 있으나 소재/비전 검증에 모두 실패 (다음 cron 재시도)")
            log.warning("대기열(queue/pending)이 비어 있음 — QUEUE_ONLY=1 이라 발행을 건너뜀")
            print(QUEUE_EMPTY_MARKER, flush=True)
            return {"queue_empty": True, "animal": None, "video": None, "url": None, "locales": []}
        else:
            # GPT 동적 픽 + Pexels 매칭 + 비전 검증 재시도 (최대 5번)
            meta = None
            bg_groups = None
            retry_used = list(dynamic_exclude_list)  # (D) 완화 없는 전체 exclude
            for attempt in range(5):
                try:  # (G) pick 자체 예외도 다음 시도로
                    if _cfg.no_openai_mode():
                        raise RuntimeError("NO_OPENAI 모드에서 GPT 대본 갈래 도달 — 대기열이 비었거나 QUEUE_ONLY 누락")
                    try_meta = animals.pick_random_animal(
                        exclude_keys=retry_used, history=hist, avoid_formats=avoid_formats)
                except Exception as e:
                    log.warning(f"[try {attempt+1}] pick_random_animal 예외 → 재시도: {e}")
                    continue
                ak = try_meta["animal_key"]
                log.info(f"[try {attempt+1}/5] picked animal: {ak} ({try_meta['display']})")
                # 강제 중복 차단: 회피는 GPT 프롬프트 의존이라 무시될 때가 있음 → 최근 사용
                # 동물을 다시 픽하면 코드가 확실히 거르고 재픽한다 (쿠마님 중복 신고 대응).
                if ak in retry_used:
                    log.warning(f"[try {attempt+1}] 중복 토픽 {ak} (제외 목록에 있음) → 재픽")
                    retry_used.append(ak)
                    continue
                wd = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), ak)
                os.makedirs(wd, exist_ok=True)
                try_meta["use_nasa"] = USE_NASA_MEDIA
                try:
                    bgs, cids = fetch_verified_backgrounds(
                        try_meta, wd, exclude_pexels_ids=used_pexels, exclude_clip_ids=recent_clips)
                except RuntimeError as e:
                    log.warning(f"[try {attempt+1}] 검증 실패 ({try_meta['display']}): {e} → GPT 재픽")
                    retry_used.append(ak)
                    continue
                meta = try_meta
                animal_key = ak
                work_dir = wd
                bg_groups = bgs
                bg_clip_ids = cids
                break

            if meta is None or bg_groups is None:
                # 5번 재시도 실패 → legacy 사전 fallback (모두 strict mode).
                # 2026-05-06 fix (우주봇 Saturn 케이크 사고 동기화): 이전엔 min_pass=1 SOFT mode +
                # 마지막엔 검증 없이 강제 발행이 있어서 garbage 영상이 그대로 발행되는 사고가 있었음.
                # 이제 strict only — 5개 fallback도 모두 vision 통과 못하면 워크플로 실패시킴.
                # (F) 정적 풀 fallback은 expand_legacy_to_rich를 먼저 → fact_keywords 확정 → 배경 fetch.
                log.warning("5번 재시도 실패 → legacy 사전 fallback 시작 (strict only)")
                legacy_tries = sorted(fallback_exclude)  # (D) 완화 적용된 exclude
                for legacy_attempt in range(5):
                    legacy_key = animals.pick_animal(exclude=legacy_tries)
                    legacy_meta = animals._legacy_meta(legacy_key)
                    wd = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), legacy_key)
                    os.makedirs(wd, exist_ok=True)
                    log.info(f"[legacy try {legacy_attempt+1}/5] {legacy_key} ({legacy_meta['display']})")
                    # (F) 먼저 expand → 최종 facts/fact_keywords 확정 후 배경 fetch
                    try:
                        legacy_meta.update(animals.expand_legacy_to_rich(legacy_meta))
                        log.info(f"legacy → GPT-rich content 변환 완료 ({len(legacy_meta['facts'])} facts)")
                    except Exception as e:
                        log.warning(f"legacy GPT-rich 실패 → 기본 템플릿 유지: {e}")
                    legacy_meta["use_nasa"] = USE_NASA_MEDIA
                    try:
                        bgs, cids = fetch_verified_backgrounds(
                            legacy_meta, wd, exclude_pexels_ids=used_pexels, exclude_clip_ids=recent_clips)
                    except RuntimeError as e:
                        log.warning(f"[legacy try {legacy_attempt+1}] vision 부족 ({legacy_meta['display']}): {e} → 다음 fallback")
                        legacy_tries.append(legacy_key)
                        continue
                    meta = legacy_meta
                    animal_key = legacy_key
                    work_dir = wd
                    bg_groups = bgs
                    bg_clip_ids = cids
                    break

                if meta is None or bg_groups is None:
                    # garbage 발행 차단 — 워크플로 실패시킴
                    raise RuntimeError(
                        "10번 토픽 시도 모두 vision 검증 실패 — 발행 중단 "
                        "(Pexels 일시 이슈 가능성, 다음 cron에서 재시도)"
                    )

        log.info(f"=== 토픽: {animal_key} ({meta['display']}, {meta['category']}) ===")
        log.info(f"title_en: {meta['title_en']}")
        log.info(f"hook: {meta['hook']}")
        for i, f in enumerate(meta["facts"]):
            log.info(f"fact[{i}]: {f}")

        # 1) TTS (intro + fact x4 + outro)
        _t = time.monotonic()
        tts_texts = [meta["intro"]] + list(meta["facts"]) + [meta["outro"]]
        tts_lines = tts.synth_lines(tts_texts, work_dir, prefix="tts")
        log.info(f"[timing] tts={time.monotonic() - _t:.1f}")

        # 3) BGM — 우주 카테고리 기반 음악쇼츠 GDrive read-only 다운로드
        #    (path, file_id) 튜플 — 사용 기록은 발행 성공 시에만 mark_bgm_used (2026-07-17)
        from pipeline import bgm as bgm_mod
        bgm_path, bgm_file_id = bgm_mod.fetch_bgm_by_category(meta["category"], work_dir)
        if bgm_path:
            log.info(f"bgm: {os.path.basename(bgm_path)}")
        else:
            log.info("bgm: 없음 — voice only 진행")

        # 4) 영상 합성
        out_video = os.path.join(
            work_dir, f"{animal_key}_{datetime.now().strftime('%H%M%S')}.mp4"
        )
        try:
            composer.compose(bg_groups, tts_lines, meta["hook"], out_video, bgm_path=bgm_path,
                             title=meta.get("title_en"), topic_label=meta.get("display"))
        except composer.NarrationTooLongError as e:
            # fail-closed: 음성이 잘리는 대본은 발행하지 않는다. 대기열 대본이면 failed 로
            # 빼고 다음 pending 을 시도(시도 예산은 소재 매칭과 공유해 총 3건).
            if not queue_path:
                raise
            _queue_move_failed(queue_path, [f"narration_too_long {e.cut_sec:.2f}"])
            log.warning(f"[queue] 내레이션 길이 초과({e.cut_sec:.2f}s 잘림) → failed 이동, "
                        f"다음 대본 시도 (남은 시도 {queue_budget[0]}건)")
            queue_path = None
            if queue_budget[0] > 0:
                continue
            raise RuntimeError(
                "대기열 대본이 모두 내레이션 길이 초과/소재 실패 — 발행 중단 (대본 보충 필요)")
        log.info(f"video: {out_video} ({os.path.getsize(out_video) / 1024 / 1024:.1f}MB)")
        break

    # 4-1) 썸네일 — 첫 배경 클립 1.0초 프레임 + 상단 후크(대문자) (fail-soft: 실패해도 발행 진행)
    thumbnail_path = None
    if getattr(_cfg, "ENABLE_THUMBNAIL", False):
        _t = time.monotonic()
        try:
            from pipeline.thumbnail import generate_thumbnail
            first_bg = bg_groups[0][0][0] if (bg_groups and bg_groups[0]) else None
            punch = (meta.get("hook") or "").strip().upper()
            if not punch:
                # 후크가 비었으면 제목 앞 4단어 대문자
                punch = " ".join((meta.get("title_en") or "").split()[:4]).upper()
            tp = os.path.join(work_dir, "thumbnail.jpg")
            if first_bg and punch and generate_thumbnail(
                    first_bg, tp, text=punch, frame_time=1.0):
                thumbnail_path = tp
        except Exception as e:
            log.warning(f"썸네일 생성 실패(영상은 정상 진행): {e}")
        log.info(f"[timing] thumbnail={time.monotonic() - _t:.1f}")

    # 5) 7개국 localizations — 대기열 대본에 한국어 메타가 있으면 번역 대신 그 값을 쓴다.
    locs = _meta_localizations(meta)
    if locs is None:
        if _cfg.no_openai_mode():
            log.warning("NO_OPENAI 인데 대본 localizations 없음 → 번역 없이 발행")
            locs = {}
        else:
            locs = translator.translate_for_youtube(meta["title_en"], meta["description_en"])
    else:
        log.info(f"localizations 를 대본 값으로 사용(번역 호출 없음): {','.join(locs.keys())}")
    ko_title = (meta.get("title_ko") or "").strip()
    ko_desc = (meta.get("description_ko") or "").strip()
    if ko_title or ko_desc:
        ko = dict(locs.get("ko") or {})
        if ko_title:
            ko["title"] = ko_title[:95]
        if ko_desc:
            ko["description"] = ko_desc[:4900]
        if ko.get("title"):  # 제목 없는 localization 은 YouTube 가 거부한다
            ko.setdefault("description", meta.get("description_en", ""))
            locs["ko"] = ko
            log.info("locs['ko'] 를 대본의 한국어 메타로 덮어씀 (번역 무시)")

    if dry_run:
        log.info("--dry-run → 업로드 스킵 (대기열 pending 소비 안 함)")
        return {
            "animal": animal_key,
            "video": out_video,
            "url": None,
            "locales": list(locs.keys()),
            "thumbnail": thumbnail_path,
        }

    # 6) 업로드 (동물별 맞춤 댓글 자동 포함)
    url = None
    if not no_upload:
        from pipeline import youtube_upload
        _t = time.monotonic()
        url = youtube_upload.upload(
            out_video,
            title=meta["title_en"],
            description=meta["description_en"],
            tags=meta["tags"],
            thumbnail_path=thumbnail_path,
            localizations=locs,
            default_language="en",
            custom_comment=meta.get("comment"),
        )
        log.info(f"[timing] upload={time.monotonic() - _t:.1f}")

    # 6-1) 대기열 소비 — **실제 업로드가 videoId 를 받은 뒤에만** done 으로 옮긴다.
    #      --dry-run/--no-upload 는 여기까지 오지 않거나 url 이 None 이라 pending 이 그대로 남는다.
    if queue_path and url:
        _vid = url.rstrip("/").split("/")[-1].split("?")[0]
        # videoId 모양이 아니면 소비하지 않는다 — 업로더가 이상한 truthy 값을 돌려줘도
        # 대본을 done 으로 태워버리지 않게(codex 지적).
        if str(url).startswith("http") and re.fullmatch(r"[A-Za-z0-9_-]{8,20}", _vid or ""):
            queue_mark_done(queue_path, _vid, url)
        else:
            log.error(f"[queue] 업로드 반환값이 videoId 형태가 아님 → pending 유지: {url!r}")
    elif queue_path:
        log.info("[queue] 업로드 videoId 없음 → pending 유지 (다음 실행에서 재시도)")

    # 7) 사용 기록 — 실제로 발행된 경우에만. (2026-07-17 fix: --no-upload 테스트 런이
    #    used_animals/used_pexels/content_history/used_bgm을 '발행됨'으로 오염시키던 문제)
    used_clip_ids = [c for c in (bg_clip_ids or []) if c]
    if no_upload:
        log.info("--no-upload → 사용 기록(used_animals/used_pexels/content_history/used_bgm) 저장 스킵 (상태 오염 방지)")
    else:
        used.append(animal_key)
        _save_used(used)

        # 7-1) Pexels 풋티지 사용 기록 (다음 영상부터 같은 풋티지 안 뜨도록)
        new_pexels_ids = [int(c) for c in used_clip_ids if str(c).isdigit()]
        if new_pexels_ids:
            used_pexels.extend(new_pexels_ids)
            _save_used_pexels(used_pexels)
            log.info(f"used_pexels.json 갱신: +{new_pexels_ids} (전체 {len(used_pexels)}개 추적)")

        # 7-2) content_history.json 기록 (시간 기반 재등장 차단 + 클립 dedup + angle/format 로테이션)
        try:
            entry = hist_mod.build_entry(
                animal_key,
                angle=meta.get("angle", ""),
                fmt=meta.get("format", ""),
                facts=meta.get("facts", []),
                title=meta.get("title_en", ""),
                clip_ids=used_clip_ids,
            )
            hist_mod.add_entry(hist, animal_key, entry)
            hist_mod.save_history(CONTENT_HISTORY_PATH, hist)
            log.info(f"content_history.json 기록: {animal_key} (angle={entry['angle']}, format={entry['format']}, clips={len(used_clip_ids)})")
        except Exception as e:
            log.warning(f"content_history 기록 실패: {e}")

        # 7-3) BGM 사용 기록 — 테마 풀 소진 전 중복 방지 (발행 성공 시에만)
        if bgm_file_id:
            bgm_mod.mark_bgm_used(bgm_file_id)
            log.info(f"used_bgm.json 갱신: +{bgm_file_id}")

    # 8) CC 자막 트랙 (en + 7개국) — 기록(used/history) 저장을 먼저 끝낸 뒤 수행.
    #    **fail-soft**: 자막 실패가 영상 발행/기록을 실패시키지 않음. CC 전체 예산 180s.
    cap_ok = {}
    if url and not no_upload:
        _t_cc = time.monotonic()
        try:
            import time as _time
            from pipeline import captions
            cc_deadline = _time.monotonic() + 180.0  # CC 단계 전체 예산(초)
            video_id = url.rstrip("/").split("/")[-1].split("?")[0]
            # 적응형 길이(composer v4)라 실제 렌더 길이를 실측해 자막 총길이로 사용
            try:
                from moviepy.editor import VideoFileClip as _VFC
                with _VFC(out_video) as _vc:
                    _actual_dur = float(_vc.duration)
            except Exception:
                _actual_dur = composer.final_video_duration()
            line_tr = meta.get("captions") if isinstance(meta.get("captions"), dict) else None
            if line_tr or _cfg.no_openai_mode():
                # 대본이 준 라인별 번역으로 CC 생성 — OpenAI 무호출(없는 언어는 en 만)
                srts = captions.generate_from_lines(
                    tts_lines, work_dir, line_tr or {}, total_dur=_actual_dur)
            else:
                srts = captions.generate_all(
                    tts_lines, work_dir,
                    total_dur=_actual_dur, deadline=cc_deadline,
                )
            cap_ok = captions.upload_captions(video_id, srts, deadline=cc_deadline)
            n_ok = sum(1 for v in cap_ok.values() if v)
            log.info(f"CC 자막: {n_ok}/{captions.EXPECTED_CC_LANGS} 언어 등록")
        except Exception as e:
            log.warning(f"CC 자막 전체 실패 (영상 정상 발행됨): {e}")
        log.info(f"[timing] captions={time.monotonic() - _t_cc:.1f}")

    return {
        "animal": animal_key,
        "video": out_video,
        "url": url,
        "locales": list(locs.keys()),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--animal", help="강제 동물 키 (예: otter)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-upload", action="store_true")
    args = p.parse_args()

    try:
        result = run_once(
            forced_animal=args.animal,
            dry_run=args.dry_run,
            no_upload=args.no_upload,
        )
        if result.get("queue_empty"):
            # 대기열이 비어 정상 종료(실패 아님). 마커는 run_once 가 이미 stdout 에 찍었다.
            sys.exit(0)
        print(result)
    except Exception as e:
        log.exception(f"실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
