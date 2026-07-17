"""GPT-4o-mini vision으로 영상 첫 프레임에 해당 토픽(우주 객체)이 실제로 보이는지 검증.

이전 버그: capybara picked → Pexels에 hedgehog/rabbit 영상이 매칭되어 발행됨.
generic 키워드("wild mammal") 제거만으로는 부족 — Pexels 사진가가 잘못 태깅한
경우도 있어 시각 검증으로 100% 확실하게 reject.

2026-05-06 fix (Saturn 케이크 사고): rate limit 429에서 fail-OPEN(return True)로
케이크/반지 영상이 그대로 통과하던 문제 → fail-CLOSED로 전환 + 429 backoff 재시도.
2026-07-17 fix (여행봇 검증분 이식): 인프라 fault(API 키 없음 / frame 추출 실패)도
fail-CLOSED로 전환 — 키 없음은 main.py 시작 시 fail-fast로도 차단되고, 여기서도 reject한다.
garbage 발행보다 발행 안 하는 게 낫다는 정책.

비용: gpt-4o-mini vision은 1 이미지 약 $0.0005~0.001. 일 4편 × 3 영상 = 12회 ≈ 일 $0.01.
"""
import base64
import logging
import os
import subprocess
import time

from config import OPENAI_API_KEY

log = logging.getLogger("spacebot.vision")

# ffmpeg 경로: 시스템 PATH 우선 (GitHub Actions ubuntu apt-get 설치본),
# fallback으로 imageio_ffmpeg 번들 (로컬 Windows).
try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG = "ffmpeg"


def extract_frame(video_path: str, timestamp: float = 1.5) -> str:
    """ffmpeg로 영상의 timestamp초 시점 프레임을 PNG로 추출.

    timestamp=1.5: 첫 프레임은 검은 fade-in일 수 있어 1.5초가 안전.
    """
    out = video_path + ".verify.png"
    cmd = [
        FFMPEG, "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=512:-1",  # 비전 API 비용 절감 + 충분히 식별 가능
        "-q:v", "3",
        out,
    ]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not os.path.exists(out):
        return ""
    return out


def verify_animal(video_path: str, animal_single: str,
                   animal_display: str = "", animal_category: str = "") -> bool:
    """영상에 animal_single이 실제로 보이는지 GPT-4o-mini vision으로 검증.

    True: 영상에 해당 토픽이 보임
    False: 다른 토픽 / 토픽 없음 / 부적합 / vision API 자체 실패(rate limit 포함)
           / 인프라 fault(API 키 없음, frame 추출 실패) — 전부 fail-CLOSED

    2026-05-06 fix: API 호출 실패 시 fail-OPEN → fail-CLOSED 전환.
    rate limit 429에 대해서만 backoff 재시도 (3회).
    2026-07-17 fix: 인프라 fault(키 없음/frame 추출 실패)도 fail-CLOSED —
    검증할 수 없는 클립은 발행하지 않는다.
    """
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY 없음 — vision 검증 불가 → REJECT (fail-closed). "
                  "main.py 시작 시 fail-fast로도 차단되지만 방어층으로 여기서도 거절.")
        return False

    img_path = extract_frame(video_path, timestamp=1.5)
    if not img_path:
        log.warning(f"frame 추출 실패: {os.path.basename(video_path)} — REJECT (fail-closed, 다른 클립으로 대체)")
        return False

    try:
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        # 2026-05-06 v4: prompt 완화. 이전 v3은 "Saturn을 main subject로" 요구해서
        # Pexels 커버리지 한계로 정상 우주 영상까지 모두 reject (10토픽 시도 0/60). 정책 변경:
        # 우주/cosmic 이미지면 통과, 명확한 garbage(결혼/케이크/사람/지상/하늘)만 reject.
        # 토픽 정확 매칭은 포기 — Pexels에 그런 영상이 없음. slug filter가 1차 garbage 차단,
        # vision은 2차로 명백한 비-우주 콘텐츠만 거름.
        question = (
            f"This image will be used as background for a 60-second YouTube Shorts video about {animal_single}"
            + (f" ({animal_display})" if animal_display and animal_display.lower() != animal_single.lower() else "")
            + ". Is this image visually appropriate as a space/cosmic background?"
            f" Answer ONLY one word: 'yes' or 'no'."
            f" REJECT (answer 'no') ONLY if image clearly shows ANY of these:"
            f" — Wedding rings, engagement rings, jewelry, diamonds, necklaces."
            f" — Cakes, cupcakes, food, desserts, baking."
            f" — People as the main subject (selfies, portraits, models, couples, families, kissing)."
            f" — Manicures, fashion shoots, makeup, lipstick, beauty products."
            f" — Earthly indoor scenes (kitchens, bedrooms, offices, restaurants, shops, party rooms)."
            f" — Outdoor earth scenes with no cosmic content (urban streets, parks, animals, sports, beaches, forests)."
            f" — Aircraft contrails, jet trails, airplane lines, helicopters, drones (these are NOT space)."
            f" — Generic blue sky with clouds, sunset, or sunrise without any visible stars/galaxies/planets."
            f" — Image is too blurry, dark, or unidentifiable."
            f" ACCEPT (answer 'yes') for ANY space/cosmic imagery: stars, galaxies, nebulae, planets,"
            f" cosmic dust, deep space scenes, NASA/Hubble/observatory photos, telescope footage,"
            f" rocket launches, astronaut shots, spacecraft, milky way photography, aurora,"
            f" or even artist renderings of cosmic phenomena."
            f" It does NOT need to specifically show {animal_single} — generic cosmic imagery is fine."
        )

        # rate limit 429에서 fail-OPEN으로 garbage 통과되던 사고 방지 — 3회 backoff 재시도.
        # 비-rate-limit 에러는 재시도 없이 즉시 fail-CLOSED.
        last_err = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": question},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    }],
                    max_tokens=5,
                    temperature=0,
                )
                ans = (resp.choices[0].message.content or "").strip().lower()
                ok = ans.startswith("yes")
                log.info(f"vision verify: {os.path.basename(video_path)} → {animal_single}? {ans!r} ({'OK' if ok else 'REJECT'})")
                return ok
            except Exception as e:
                last_err = e
                es = str(e).lower()
                is_rate = "rate_limit" in es or "429" in es or "tpm" in es or "too many requests" in es
                if is_rate and attempt < 2:
                    wait = [4, 12][attempt]
                    log.warning(f"vision rate limit → {wait}s 후 재시도 ({attempt+1}/3): {os.path.basename(video_path)}")
                    time.sleep(wait)
                    continue
                # 비-rate-limit 또는 마지막 시도 실패 → fail-CLOSED
                log.error(f"vision API 실패 → REJECT (fail-closed, attempt {attempt+1}/3): {os.path.basename(video_path)}: {e}")
                return False
        # for-else 보호망 (정상 흐름은 위에서 return으로 빠짐)
        log.error(f"vision API 3회 모두 실패 → REJECT: {last_err}")
        return False

    except Exception as e:
        log.error(f"vision verify pre-call 실패 → REJECT (fail-closed): {e}")
        return False

    finally:
        try:
            if img_path and os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass
