"""GPT-4o-mini vision으로 영상 첫 프레임에 해당 동물이 실제로 보이는지 검증.

이전 버그: capybara picked → Pexels에 hedgehog/rabbit 영상이 매칭되어 발행됨.
generic 키워드("wild mammal") 제거만으로는 부족 — Pexels 사진가가 잘못 태깅한
경우도 있어 시각 검증으로 100% 확실하게 reject.

비용: gpt-4o-mini vision은 1 이미지 약 $0.0005~0.001. 일 4편 × 3 영상 = 12회 ≈ 일 $0.01.
"""
import base64
import logging
import os
import subprocess

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

    True: 영상에 해당 동물이 보임 (또는 검증 인프라 실패 — 안전하게 통과 처리)
    False: 다른 동물 / 동물 없음 / 부적합

    인프라 실패(API 키 없음, frame 추출 실패, API 에러)는 통과 처리하여
    발행 자체가 망가지지는 않게 함. 단, 명확하게 다른 동물이 보이면 reject.
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
        question = (
            f"Does this image show {animal_single}"
            + (f" ({animal_display})" if animal_display and animal_display.lower() != animal_single.lower() else "")
            + " or a clearly related space/cosmos visual (planet, star, galaxy, nebula, telescope, deep space, astronaut, etc.)?"
            f" Answer ONLY one word: 'yes' or 'no'."
            f" If the image shows something completely unrelated to space (e.g. a kitchen, a car, an animal), answer 'no'."
            f" If the image is too blurry to identify, answer 'no'."
            f" If the image shows ANY plausible space/astronomy visual that fits the cosmic theme, answer 'yes'."
        )

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
        log.warning(f"vision API 실패 — 안전 통과: {e}")
        return True

    finally:
        try:
            if img_path and os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass
