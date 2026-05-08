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

# Pexels 풋티지 dedup (2026-05-07 사용자 지적: 최근 영상 풋티지 가타)
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
    """pexels_<id>.mp4 파일명에서 id 추출. 실패 시 None."""
    import re as _re
    base = os.path.basename(path or "")
    m = _re.match(r"pexels_(\d+)\.mp4$", base)
    return int(m.group(1)) if m else None


def fetch_verified_backgrounds(meta: dict, work_dir: str, n: int = 3,
                                oversample: int = 2,
                                exclude_pexels_ids: list = None) -> list:
    """비전 검증 통과한 영상 N개 확보 — 잘못된 영상 reject.

    1. fetch_backgrounds로 N*oversample 개 후보 다운
    2. 각 영상 첫 프레임을 GPT-4o-mini vision으로 검증 (호출 사이 0.5s sleep — TPM burst 회피)
    3. 통과한 영상 N개만 사용, 그 외 모두 삭제
    4. 통과 미달 → RuntimeError (호출 측에서 다른 토픽 시도)

    2026-05-06 fix: 이전엔 min_pass=1 SOFT mode가 있어서 1개만 통과해도
    나머지 reject 영상으로 채워 발행 → Saturn 케이크 사고. 이제 항상 strict.
    """
    import time as _time
    from pipeline import pexels, verify_visual
    candidates = pexels.fetch_backgrounds(
        meta["pexels_keywords"], work_dir,
        n=n * oversample,
        animal_single=meta["single"],
        exclude_pexels_ids=exclude_pexels_ids,
    )
    paths = []
    rejected = []
    for idx, cand in enumerate(candidates):
        if len(paths) >= n:
            break
        if idx > 0:
            # vision API TPM 200K 한도에 걸려서 fail-closed로 reject되는 사고 방지
            _time.sleep(0.5)
        ok = verify_visual.verify_animal(
            cand, meta["single"],
            animal_display=meta.get("display", ""),
            animal_category=meta.get("category", ""),
        )
        if ok:
            paths.append(cand)
        else:
            rejected.append(cand)

    if len(paths) >= n:
        for r in rejected:
            try:
                os.remove(r)
            except Exception:
                pass
        return paths

    # n개 미달 — AI 이미지 폴백 시도 (Pexels → codex → gpt-image2 → gemini 순)
    # 2026-05-08 추가: Pexels 우주 영상 커버리지 부족(Uranus 0/6 등) 사고 대응.
    needed = n - len(paths)
    log.warning(f"vision 통과 {len(paths)}/{n} — AI 이미지 폴백 {needed}장 시도")
    try:
        from pipeline import image_fallback, image_to_video
        png_paths = image_fallback.generate_fallback_images(meta, needed, work_dir)
        if png_paths:
            ai_clips = image_to_video.pngs_to_videos(png_paths, work_dir, duration=10.0)
            paths.extend(ai_clips)
            log.info(f"AI 이미지 폴백 완료: 총 {len(paths)}/{n} (Pexels {len(paths)-len(ai_clips)} + AI {len(ai_clips)})")
    except Exception as e:
        log.warning(f"AI 이미지 폴백 자체 실패: {e}")

    if len(paths) >= n:
        for r in rejected:
            try:
                os.remove(r)
            except Exception:
                pass
        return paths

    # 폴백도 부족 → garbage 발행 방지 위해 모두 삭제 + RuntimeError
    for r in rejected:
        try:
            os.remove(r)
        except Exception:
            pass
    for p in paths:
        try:
            os.remove(p)
        except Exception:
            pass
    raise RuntimeError(
        f"비전 검증 + AI 폴백 모두 부족 ({len(paths)}/{n}) — 다른 토픽 픽 필요"
    )


def run_once(forced_animal: str = None, dry_run: bool = False, no_upload: bool = False) -> dict:
    from pipeline import animals, pexels, tts, composer, translator

    used = _load_used()
    used_pexels = _load_used_pexels()

    if forced_animal:
        meta = animals._legacy_meta(forced_animal)
        animal_key = meta["animal_key"]
        work_dir = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), animal_key)
        os.makedirs(work_dir, exist_ok=True)
        bg_paths = fetch_verified_backgrounds(meta, work_dir, n=3, exclude_pexels_ids=used_pexels)
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
                bgs = fetch_verified_backgrounds(try_meta, wd, n=3, exclude_pexels_ids=used_pexels)
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
            # 5번 재시도 실패 → legacy 사전 fallback (모두 strict mode).
            # 2026-05-06 fix: 이전엔 min_pass=1 SOFT mode + 마지막엔 검증 없이 강제 발행이
            # 있어서 Saturn 토픽인데 케이크/반지 영상이 그대로 발행되는 사고가 있었음.
            # 이제 strict only — 5개 fallback도 모두 vision 통과 못하면 워크플로 실패시킴
            # (garbage 발행보다 발행 안 하는 게 채널 신뢰 유지에 낫다).
            log.warning("5번 재시도 실패 → legacy 사전 fallback 시작 (strict only)")
            legacy_tries = list(used)
            for legacy_attempt in range(5):
                legacy_key = animals.pick_animal(exclude=legacy_tries)
                legacy_meta = animals._legacy_meta(legacy_key)
                wd = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y%m%d"), legacy_key)
                os.makedirs(wd, exist_ok=True)
                log.info(f"[legacy try {legacy_attempt+1}/5] {legacy_key} ({legacy_meta['display']})")
                try:
                    bgs = fetch_verified_backgrounds(legacy_meta, wd, n=3, exclude_pexels_ids=used_pexels)
                except RuntimeError as e:
                    log.warning(f"[legacy try {legacy_attempt+1}] vision 부족 ({legacy_meta['display']}): {e} → 다음 fallback")
                    legacy_tries.append(legacy_key)
                    continue
                meta = legacy_meta
                animal_key = legacy_key
                work_dir = wd
                bg_paths = bgs
                break

            if meta is None or bg_paths is None:
                # garbage 발행 차단 — 워크플로 실패시킴
                raise RuntimeError(
                    "10번 토픽 시도 모두 vision 검증 실패 — 발행 중단 "
                    "(Pexels 일시 이슈 가능성, 다음 cron에서 재시도)"
                )

            # GPT로 fallback 토픽의 facts/intro/outro 진짜 생성 (generic 템플릿 회피)
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

    # 7-1) Pexels 풋티지 사용 기록 (다음 영상부터 같은 풋티지 안 뜨도록)
    new_pexels_ids = [pid for pid in (_extract_pexels_id(p) for p in bg_paths) if pid]
    if new_pexels_ids:
        used_pexels.extend(new_pexels_ids)
        _save_used_pexels(used_pexels)
        log.info(f"used_pexels.json 갱신: +{new_pexels_ids} (전체 {len(used_pexels)}개 추적)")

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
