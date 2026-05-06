"""GPT-4o-mini vision으로 영상 첫 프레임에 해당 토픽(우주 객체)이 실제로 보이는지 검증.

이전 버그: capybara picked → Pexels에 hedgehog/rabbit 영상이 매칭되어 발행됨.
generic 키워드("wild mammal") 제거만으로는 부족 — Pexels 사진가가 잘못 태깅한
경우도 있어 시각 검증으로 100% 확실하게 reject.

2026-05-06 fix (Saturn 케이크 사고): rate limit 429에서 fail-OPEN(return True)로
케이크/반지 영상이 그대로 통과하던 문제 → fail-CLOSED로 전환 + 429 backoff 재시도.
인프라 fault(API 키 없음 / frame 추출 실패)만 통과 처리하고 vision 응답 자체가
실패한 경우는 reject — garbage 발행보다 발행 안 하는 게 낫다는 정책.

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

    True: 영상에 해당 토픽이 보임 (또는 인프라 fault — API 키 없음/frame 추출 실패만 통과)
    False: 다른 토픽 / 토픽 없음 / 부적합 / vision API 자체 실패 (rate limit 포함)

    2026-05-06 fix: API 호출 실패 시 fail-OPEN → fail-CLOSED 전환.
    rate limit 429에 대해서만 backoff 재시도 (3회).
    """
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY 없음 — vision 검증 스킵 (안전 통과)")
        return True

    img_path = extract_frame(video_path, timestamp=1.5)
    if not img_path:
        log.warning(f"frame 추출 실패: {os.path.basename(video_path)} — 안전 통과")
        return True

    try:
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        # 단일 yes/no 응답 — 토큰 최소화 + 일관된 판정
        # NOTE: animal_single 변수명은 호환 유지. 의미상은 "subject" (우주 토픽).
        # v3 더 엄격: 다른 행성/객체 + 비행운/지상 객체 + 일반 하늘 모두 reject.
        question = (
            f"Does this image specifically show {animal_single}"
            + (f" ({animal_display})" if animal_display and animal_display.lower() != animal_single.lower() else "")
            + " as the main subject?"
            f" Answer ONLY one word: 'yes' or 'no'."
            f" REJECT (answer 'no') if:"
            f" — Shows a different planet/object (e.g. Moon when asked about Jupiter, Saturn when asked about Mars)."
            f" — Shows aircraft contrails, jet trails, airplane lines, helicopter, drones (these are NOT comets/meteors)."
            f" — Shows generic blue sky, generic clouds, sunset, sunrise (when asked about a specific cosmic object)."
            f" — Shows ground objects (buildings, mountains, trees, cars, people, animals)."
            f" — Shows wedding rings, engagement rings, jewelry, cakes, food, fashion, weddings, manicured nails."
            f" — Shows generic 'space' or just stars when asked about a SPECIFIC named planet or object."
            f" — Image is too blurry, dark, or unidentifiable."
            f" Accept (answer 'yes') ONLY if image clearly shows {animal_single} as the visible main subject"
            f" OR shows a directly related NASA/Hubble/observatory photo of THIS specific topic."
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
