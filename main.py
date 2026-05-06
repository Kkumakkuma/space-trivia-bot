#!/usr/bin/env python3
"""Space Trivia 60s — 1분 동물 트리비아 영상 자동 발행.

흐름:
1. 최근 사용 회피하면서 동물 1마리 랜덤 선택
2. GPT가 hook + intro + fact x3 + outro + 영문 title/description 생성
3. edge-tts로 영문 내레이션 5개 생성 (intro/fact x3/outro)
4. Pexels에서 동물 영상 3개 다운로드
5. MoviePy로 1분 영상 합성 (배경 concat + 비네트 + hook 상단 + 자막 시간차 + brand 하단)
6. 영문 메인 + 7개국 localizations 번역
7. YouTube Shorts 업로드 (privacy=public, defaultLanguage=en)
8. 사용 동물 기록 (used_animals.json) — 같은 cron에서 연속 회피
"""
import argparse
import io
import json
import logging
import os
import random
import sys
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ANIMALS, BASE_DIR, OUTPUT_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("spacebot")

USED_PATH = os.path.join(BASE_DIR, "used_animals.json")
USED_KEEP = 30  # 직전 30번 사용한 동물 회피 (4편/일 × 약 7.5일 — GPT 픽 다양성 확보)


def _load_used():
    if not os.path.exists(USED_PATH):
        return []
    try:
        return json.load(open(USED_PATH, encoding="utf-8"))
    except Exception:
        return []


def _save_used(used):
    json.dump(used[-USED_KEEP:], open(USED_PATH, "w", encoding="utf-8"))


def fetch_verified_backgrounds(meta: dict, work_dir: str, n: int = 3,
                                oversample: int = 2, min_pass: int = None) -> list:
    """비전 검증 통과한 동물 영상 N개 확보 — 잘못된 동물 영상 reject.

    1. fetch_backgrounds로 N*oversample 개 후보 다운
    2. 각 영상 첫 프레임을 GPT-4o-mini vision으로 검증
    3. 통과한 영상 N개 누적 (또는 min_pass개), 미통과 reject + 파일 삭제
    4. min_pass 통과 시 부족분은 미검증 영상으로 채움 (legacy fallback에서 soft mode)
    5. 부족하면 RuntimeError → 호출 측에서 다른 동물 픽

    min_pass: None이면 strict (n개 다 통과). 값 주면 soft mode (min_pass개만 통과해도 OK,
              나머지는 미검증 영상으로 채움 — legacy fallback에서 사용).
    """
    from pipeline import pexels, verify_visual
    candidates = pexels.fetch_backgrounds(
        meta["pexels_keywords"], work_dir,
        n=n * oversample,
        animal_single=meta["single"],
    )
    paths = []
    rejected = []
    for cand in candidates:
        if len(paths) >= n:
            break
        ok = verify_visual.verify_animal(
            cand, meta["single"],
            animal_display=meta.get("display", ""),
            animal_category=meta.get("category", ""),
        )
        if ok:
            paths.append(cand)
        else:
            rejected.append(cand)

    threshold = min_pass if min_pass is not None else n
    if len(paths) >= threshold:
        # soft mode에서 통과 부족분은 reject 영상으로 채움 (없는 것보단 나음)
        while len(paths) < n and rejected:
            paths.append(rejected.pop(0))
        # 남은 reject는 삭제
        for r in rejected:
            try:
                os.remove(r)
            except Exception:
                pass
        return paths

    # threshold 미달 — 모두 삭제하고 RuntimeError
    for r in rejected:
        try:
            os.remove(r)
        except Exception:
            pass
    raise RuntimeError(
        f"비전 검증 통과 영상 부족 ({len(paths)}/{threshold}) — 다른 동물 픽 필요"
    )


def run_once(forced_animal: str = None, dry_run: bool = False, no_upload: bool = False) -> dict:
    from pipeline import animals, pexels, tts, composer, translator

    used = _load_used()

    if forced_animal:
        meta = animals._legacy_meta(forced_animal)
        animal_key = meta["animal_key"]
        work_dir = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), animal_key)
        os.makedirs(work_dir, exist_ok=True)
        bg_paths = fetch_verified_backgrounds(meta, work_dir, n=3)
    else:
        # GPT 동적 픽 + Pexels 매칭 + 비전 검증 재시도 (최대 5번)
        meta = None
        bg_paths = None
        retry_used = list(used)
        for attempt in range(5):
            try_meta = animals.pick_random_animal(exclude_keys=retry_used)
            ak = try_meta["animal_key"]
            log.info(f"[try {attempt+1}/5] picked animal: {ak} ({try_meta['display']})")
            wd = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), ak)
            os.makedirs(wd, exist_ok=True)
            try:
                bgs = fetch_verified_backgrounds(try_meta, wd, n=3)
            except RuntimeError as e:
                log.warning(f"[try {attempt+1}] 검증 실패 ({try_meta['display']}): {e} → GPT 재픽")
                retry_used.append(ak)
                continue
            meta = try_meta
            animal_key = ak
            work_dir = wd
            bg_paths = bgs
            break

        if meta is None or bg_paths is None:
            # 5번 재시도 실패 → legacy 사전 fallback (vision SOFT mode).
            # axolotl 같은 희귀 fallback에서 generic underwater 영상 박히는 문제 차단:
            # min_pass=1 — 최소 1개라도 vision 통과해야 동물 매칭 보장.
            # 1개 통과 후 부족분은 reject 영상으로 채움 (vision 0/3 = 잘못된 영상만, 회피).
            # 실패 시 다른 fallback 동물 5번까지 시도 — Pexels 커버리지 좋은 동물 자동 픽.
            log.warning("5번 재시도 실패 → legacy 사전 fallback 시작 (vision SOFT mode)")
            legacy_tries = list(used)
            for legacy_attempt in range(5):
                legacy_key = animals.pick_animal(exclude=legacy_tries)
                legacy_meta = animals._legacy_meta(legacy_key)
                wd = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), legacy_key)
                os.makedirs(wd, exist_ok=True)
                log.info(f"[legacy try {legacy_attempt+1}/5] {legacy_key} ({legacy_meta['display']})")
                try:
                    bgs = fetch_verified_backgrounds(legacy_meta, wd, n=3, min_pass=1)
                except RuntimeError as e:
                    log.warning(f"[legacy try {legacy_attempt+1}] vision 0/3 ({legacy_meta['display']}): {e} → 다음 fallback")
                    legacy_tries.append(legacy_key)
                    continue
                meta = legacy_meta
                animal_key = legacy_key
                work_dir = wd
                bg_paths = bgs
                break

            if meta is None or bg_paths is None:
                # 5개 fallback 동물 다 vision 0/3 (Pexels 사이트 광범위 이슈) — 마지막 수단:
                # vision 검증 없이 첫 fallback 동물 강제 사용 (워크플로 전체 실패 차단)
                log.error("legacy fallback 5번도 vision 통과 0건 — 검증 없이 강제 발행")
                from pipeline import pexels
                meta = animals._legacy_meta(animals.pick_animal(exclude=used))
                animal_key = meta["animal_key"]
                work_dir = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), animal_key)
                os.makedirs(work_dir, exist_ok=True)
                bg_paths = pexels.fetch_backgrounds(
                    meta["pexels_keywords"], work_dir, n=3,
                    animal_single=meta["single"],
                )

            # GPT로 fallback 동물의 facts/intro/outro 진짜 생성 (generic 템플릿 회피)
            try:
                rich = animals.expand_legacy_to_rich(meta)
                meta.update(rich)
                log.info(f"legacy → GPT-rich content 변환 완료 ({len(meta['facts'])} facts)")
            except Exception as e:
                log.warning(f"legacy GPT-rich 실패 → 기본 템플릿 유지: {e}")

    log.info(f"=== 토픽: {animal_key} ({meta['display']}, {meta['category']}) ===")
    log.info(f"title_en: {meta['title_en']}")
    log.info(f"hook: {meta['hook']}")
    for i, f in enumerate(meta["facts"]):
        log.info(f"fact[{i}]: {f}")

    # 1) TTS (intro + fact x3 + outro)
    tts_texts = [meta["intro"]] + list(meta["facts"]) + [meta["outro"]]
    tts_lines = tts.synth_lines(tts_texts, work_dir, prefix="tts")

    # 3) BGM — 동물 카테고리 기반 음악쇼츠 GDrive read-only 다운로드
    from pipeline import bgm as bgm_mod
    bgm_path = bgm_mod.fetch_bgm_by_category(meta["category"], work_dir)
    if bgm_path:
        log.info(f"bgm: {os.path.basename(bgm_path)}")
    else:
        log.info("bgm: 없음 — voice only 진행")

    # 4) 영상 합성
    out_video = os.path.join(
        work_dir, f"{animal_key}_{datetime.now().strftime('%H%M%S')}.mp4"
    )
    composer.compose(bg_paths, tts_lines, meta["hook"], out_video, bgm_path=bgm_path)
    log.info(f"video: {out_video} ({os.path.getsize(out_video) / 1024 / 1024:.1f}MB)")

    # 5) 7개국 localizations
    locs = translator.translate_for_youtube(meta["title_en"], meta["description_en"])

    if dry_run:
        log.info("--dry-run → 업로드 스킵")
        return {
            "animal": animal_key,
            "video": out_video,
            "url": None,
            "locales": list(locs.keys()),
        }

    # 6) 업로드 (동물별 맞춤 댓글 자동 포함)
    url = None
    if not no_upload:
        from pipeline import youtube_upload
        url = youtube_upload.upload(
            out_video,
            title=meta["title_en"],
            description=meta["description_en"],
            tags=meta["tags"],
            localizations=locs,
            default_language="en",
            custom_comment=meta.get("comment"),
        )

    # 7) 사용 기록 (GPT 회피용)
    used.append(animal_key)
    _save_used(used)

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
        print(result)
    except Exception as e:
        log.exception(f"실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
