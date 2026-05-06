"""Space Trivia 60s — 통합 설정 (Space Trivia 60s fork).

매 cron마다 SPACE_TOPICS(=ANIMALS 변수명 호환)에서 랜덤 우주 토픽 1개 선택 →
GPT가 흥미 사실 3개 생성 → edge-tts 영문 내레이션 + Pexels 우주 영상 + 시간차 자막으로 1분 영상 합성 →
YouTube Shorts (영문 메인 + 7개국 localizations) 자동 발행.

Note: pipeline/animals.py 등 코드는 변수명 (ANIMALS, animal_key 등) 그대로 사용 — 호환성 유지.
실제로는 우주 토픽 메타데이터.
"""

# 채널 메타 (animals.py GPT prompt + youtube_upload 등에서 참조 가능)
CHANNEL_NAME = "Space Trivia 60s"
CHANNEL_TAGLINE = "cosmic facts in 60 seconds"
CONTENT_DOMAIN = "space"  # animals.py가 "animal" vs "space" 모드 분기에 사용 가능
CHANNEL_NICHE = "outer space, astronomy, cosmos, planets, stars, NASA missions, space exploration"
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
CREDENTIALS_DIR = os.path.join(BASE_DIR, "credentials")

# .env
_env = os.path.join(BASE_DIR, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

# YouTube
YOUTUBE_CLIENT_SECRET = os.path.join(CREDENTIALS_DIR, "youtube_client_secret.json")
YOUTUBE_TOKEN = os.path.join(CREDENTIALS_DIR, "youtube_token.json")
YOUTUBE_CATEGORY_ID = "28"  # Science & Technology (우주)
CONTAINS_SYNTHETIC_MEDIA = True  # TTS는 AI 합성 음성

# Video — Shorts (1080x1920)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
VIDEO_DURATION_SEC = 60
VIDEO_BITRATE = "4500k"
VIDEO_PRESET = "veryfast"
VIDEO_THREADS = 8

# 영문 채널이라 영문 sans-serif 굵은 폰트 우선
FONT_PATH = os.path.join(ASSETS_DIR, "fonts", "Roboto-Bold.ttf")

# OpenAI
META_MODEL = "gpt-4o-mini"

# edge-tts voice (영문)
TTS_VOICE = "en-US-AriaNeural"  # 자연스러운 여성 보이스
TTS_RATE = "+12%"  # 인기 트리비아 채널 페이스에 맞춤
TTS_PITCH = "+0Hz"

# 자막 스타일
CAPTION_BASE_SIZE = 80
CAPTION_Y_RATIO = 0.50  # 화면 정중앙 (인스타 UI 버튼 회피)
CAPTION_PHRASE_SIZE = 4  # 한 자막에 3-4 단어 묶음 (TikTok 스타일)
CAPTION_HIGHLIGHT_NUMBERS = True  # 숫자는 옐로우 강조

# BGM (선택) — assets/bgm/*.mp3 풀에서 랜덤. 없으면 BGM 없이 진행
BGM_VOLUME_DB = -22  # TTS 가리지 않게 작게

# 우주 토픽 사전 — Pexels에서 잘 뽑히는 우주 비주얼 위주
# 변수명은 ANIMALS 그대로 유지 (animals.py 코드 호환). 실제 내용은 우주 토픽.
ANIMALS = {
    "saturn": {
        "display": "Saturn",
        "single": "Saturn",
        "pexels_keywords": [
            "saturn rings", "planet rings space", "gas giant",
            "saturn telescope", "ringed planet",
        ],
        "category": "planet",
    },
    "jupiter": {
        "display": "Jupiter",
        "single": "Jupiter",
        "pexels_keywords": [
            "jupiter planet", "jupiter storm", "gas giant clouds",
            "jupiter moons", "great red spot",
        ],
        "category": "planet",
    },
    "mars": {
        "display": "Mars",
        "single": "Mars",
        "pexels_keywords": [
            "mars planet surface", "red planet", "mars rover",
            "mars landscape", "mars exploration",
        ],
        "category": "planet",
    },
    "black_hole": {
        "display": "Black Holes",
        "single": "black hole",
        "pexels_keywords": [
            "black hole simulation", "event horizon nasa", "supermassive black hole",
            "black hole accretion disk", "spiral galaxy core",
        ],
        "category": "phenomenon",
    },
    "milky_way": {
        "display": "the Milky Way",
        "single": "Milky Way",
        "pexels_keywords": [
            "milky way galaxy", "milky way photography", "milky way time lapse",
            "galaxy spiral arm", "milky way arch",
        ],
        "category": "galaxy",
    },
    "sun": {
        "display": "the Sun",
        "single": "Sun",
        "pexels_keywords": [
            "sun nasa", "solar flare", "solar surface",
            "sun corona", "coronal mass ejection",
        ],
        "category": "star",
    },
    "moon": {
        "display": "the Moon",
        "single": "Moon",
        "pexels_keywords": [
            "moon surface", "full moon", "moon craters",
            "lunar landscape", "earth moon",
        ],
        "category": "moon",
    },
    "neutron_star": {
        "display": "Neutron Stars",
        "single": "neutron star",
        "pexels_keywords": [
            "neutron star animation", "pulsar space", "stellar remnant",
            "neutron star nasa", "cosmic explosion",
        ],
        "category": "star",
    },
    "comet": {
        "display": "Comets",
        "single": "comet",
        "pexels_keywords": [
            "halley comet", "comet astronomy nasa", "icy comet space",
            "comet tail close up", "comet hubble",
        ],
        "category": "phenomenon",
    },
    "nebula": {
        "display": "Nebulae",
        "single": "nebula",
        "pexels_keywords": [
            "nebula colorful", "space nebula", "cosmic dust cloud",
            "deep space colors", "stellar nursery",
        ],
        "category": "phenomenon",
    },
    "exoplanet": {
        "display": "Exoplanets",
        "single": "exoplanet",
        "pexels_keywords": [
            "exoplanet distant world", "alien planet", "deep space planet",
            "telescope observation", "earth like planet",
        ],
        "category": "planet",
    },
    "iss": {
        "display": "the International Space Station",
        "single": "ISS",
        "pexels_keywords": [
            "international space station", "iss orbit earth", "astronaut spacewalk",
            "space station window", "earth from space station",
        ],
        "category": "spacecraft",
    },
    "hubble": {
        "display": "the Hubble Space Telescope",
        "single": "Hubble",
        "pexels_keywords": [
            "hubble telescope", "telescope space", "deep field galaxy",
            "telescope observatory", "space photography",
        ],
        "category": "spacecraft",
    },
    "big_bang": {
        "display": "the Big Bang",
        "single": "Big Bang",
        "pexels_keywords": [
            "universe expansion animation", "cosmic explosion", "big bang origin",
            "primordial universe", "cosmic microwave background",
        ],
        "category": "phenomenon",
    },
    "asteroid": {
        "display": "Asteroids",
        "single": "asteroid",
        "pexels_keywords": [
            "asteroid belt", "space rock", "meteor impact",
            "deep space asteroid", "asteroid surface",
        ],
        "category": "object",
    },
}
