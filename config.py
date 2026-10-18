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


def no_openai_mode() -> bool:
    """NO_OPENAI=1 이면 OpenAI 를 한 번도 부르지 않는 대기열 전용 모드(2026-09-03, 크레딧 0·충전 금지).
    클립은 대본의 clips(사람이 고른 Pexels id), 제목·설명은 localizations, 자막은 captions 로 대체한다.
    호출 시점에 환경변수를 읽는다(import 시 상수로 굳히지 않음 — 테스트·워크플로 모두 안전)."""
    return (os.environ.get("NO_OPENAI", "") or "").strip().lower() not in ("", "0", "false", "no")
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
MIN_VIDEO_DURATION_SEC = 40  # 적응형 길이 하한 (composer v4, 2026-10-18 tail 0.9). 우주 내레이션 실측 ~46.6s라 보통 48s+ — 하한은 안전망
VIDEO_BITRATE = "7000k"   # 2026-09-03 화질 강화: 4500k → 7000k (쇼츠 재인코딩 여유)
VIDEO_PRESET = "fast"     # veryfast → fast (같은 비트레이트에서 디테일 보존)
VIDEO_THREADS = 8

# 영문 채널이라 영문 sans-serif 굵은 폰트 우선
FONT_PATH = os.path.join(ASSETS_DIR, "fonts", "Roboto-Bold.ttf")

# OpenAI
# 2026-07-10: 사실 오류(토성 고리 소멸 100M vs 300M년 모순) 재발 방지로 모델 3분할.
# 생성과 검증을 다른 계열로 두어 자기합치(self-agreement) 편향을 줄인다.
META_MODEL = "gpt-4o-mini"      # 경량 호출(번역·키워드 등)
SCRIPT_MODEL = "gpt-4o"         # 토픽 픽 + 스크립트 생성 (사실 정확도 우선)
FACTCHECK_MODEL = "gpt-4.1"     # 적대적 검증 — 생성 모델과 다른 계열

# edge-tts voice (영문)
TTS_VOICE = "en-US-AndrewMultilingualNeural"  # 2026-09-03: 남성 내레이션(다큐 톤)으로 교체
TTS_RATE = "+5%"   # +12% → +5% (숨 쉴 틈 확보, 사실 전달 명료도 우선)
TTS_PITCH = "+0Hz"

# 자막 스타일
CAPTION_BASE_SIZE = 80
CAPTION_Y_RATIO = 0.60  # 하단 1/3 (피사체 가림 해소 + 인스타 UI 버튼 회피, 쇼츠 가독성)
CAPTION_PHRASE_SIZE = 4  # 한 자막에 3-4 단어 묶음 (TikTok 스타일)
CAPTION_HIGHLIGHT_NUMBERS = True  # 숫자는 옐로우 강조

# 컷 페이싱 — 사실 1개당 배경 클립 수 (2026-09-03). 수집이 모자라면 자동으로 1개로 폴백.
# 클립 사이는 하드컷(크로스페이드 금지 — Actions 러너 메모리 보호).
CHANNEL_HANDLE = "@SpaceTrivia60s"  # 썸네일 하단 브랜드 태그 (thumbnail.py 3봇 공통, 봇별 값은 여기서)
CLIPS_PER_FACT = 2

# 썸네일 — 첫 배경 클립 1.0초 프레임 + 상단 후크 (pipeline/thumbnail.py, fail-soft: 실패해도 발행 진행)
ENABLE_THUMBNAIL = True
THUMBNAIL_FONT_SIZE = 190  # 시작 크기 — 폭 초과 시 14씩 자동 축소 (하한 80)

# BGM (선택) — assets/bgm/*.mp3 풀에서 랜덤. 없으면 BGM 없이 진행
BGM_VOLUME_DB = -22  # TTS 가리지 않게 작게

# 콘텐츠 이력 (시간 기반 중복 방지 + 클립 dedup + angle/format 로테이션) — 2026-07-05
CONTENT_HISTORY = os.path.join(BASE_DIR, "content_history.json")
CONTENT_WINDOW_DAYS = 45        # 우주: 45일 내 같은 topic 재등장 차단
HISTORY_MIN_CANDIDATES = 8      # 제외 후 후보 8개 미만이면 창 0.75배씩 완화
HISTORY_MIN_WINDOW_DAYS = 7     # 완화 하한 (한 번에 전체 리셋 금지)
CLIP_DEDUP_DAYS = 30            # 30일 내 사용한 클립(Pexels/NASA id) 재사용 금지
RECENT_FORMAT_KEEP = 2          # 전역 직전 N편 format 연속 금지
USE_NASA_MEDIA = True           # 우주봇: NASA Images and Video Library 소스 사용

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
    # === 2026-07-05 확장 (NASA 커버리지 좋은 토픽 위주로 40+개) ===
    "venus": {
        "display": "Venus", "single": "Venus", "category": "planet",
        "pexels_keywords": ["venus planet", "venus surface", "planet venus space",
                            "venus atmosphere", "bright planet"],
    },
    "mercury": {
        "display": "Mercury", "single": "Mercury", "category": "planet",
        "pexels_keywords": ["mercury planet", "mercury surface", "planet mercury space",
                            "cratered planet", "innermost planet"],
    },
    "uranus": {
        "display": "Uranus", "single": "Uranus", "category": "planet",
        "pexels_keywords": ["uranus planet", "ice giant", "planet uranus space",
                            "blue planet space", "tilted planet"],
    },
    "neptune": {
        "display": "Neptune", "single": "Neptune", "category": "planet",
        "pexels_keywords": ["neptune planet", "ice giant blue", "planet neptune space",
                            "deep blue planet", "windy planet"],
    },
    "pluto": {
        "display": "Pluto", "single": "Pluto", "category": "object",
        "pexels_keywords": ["pluto dwarf planet", "pluto surface", "pluto new horizons",
                            "distant world space", "kuiper belt object"],
    },
    "europa": {
        "display": "Europa", "single": "Europa", "category": "moon",
        "pexels_keywords": ["europa moon", "icy moon jupiter", "europa surface",
                            "frozen moon space", "moon of jupiter"],
    },
    "titan": {
        "display": "Titan", "single": "Titan", "category": "moon",
        "pexels_keywords": ["titan moon saturn", "titan surface", "hazy moon",
                            "saturn largest moon", "titan atmosphere"],
    },
    "enceladus": {
        "display": "Enceladus", "single": "Enceladus", "category": "moon",
        "pexels_keywords": ["enceladus moon", "icy moon geysers", "enceladus saturn",
                            "frozen moon plumes", "saturn moon ice"],
    },
    "supernova": {
        "display": "Supernovae", "single": "supernova", "category": "phenomenon",
        "pexels_keywords": ["supernova explosion", "star explosion space", "supernova remnant",
                            "stellar explosion", "exploding star"],
    },
    "pulsar": {
        "display": "Pulsars", "single": "pulsar", "category": "star",
        "pexels_keywords": ["pulsar star", "spinning neutron star", "pulsar space",
                            "rotating star beam", "cosmic lighthouse"],
    },
    "quasar": {
        "display": "Quasars", "single": "quasar", "category": "phenomenon",
        "pexels_keywords": ["quasar galaxy", "active galactic nucleus", "quasar space",
                            "distant bright galaxy", "black hole jet"],
    },
    "dark_matter": {
        "display": "Dark Matter", "single": "dark matter", "category": "phenomenon",
        "pexels_keywords": ["dark matter universe", "cosmic web", "galaxy dark matter",
                            "invisible mass space", "universe structure"],
    },
    "solar_flare": {
        "display": "Solar Flares", "single": "solar flare", "category": "phenomenon",
        "pexels_keywords": ["solar flare sun", "solar eruption", "sun flare space",
                            "coronal mass ejection", "solar surface activity"],
    },
    "aurora": {
        "display": "Auroras", "single": "aurora", "category": "phenomenon",
        "pexels_keywords": ["aurora borealis", "northern lights", "aurora night sky",
                            "green aurora", "aurora time lapse"],
    },
    "meteor_shower": {
        "display": "Meteor Showers", "single": "meteor shower", "category": "phenomenon",
        "pexels_keywords": ["meteor shower night", "shooting stars", "meteor sky",
                            "perseid meteor", "meteor streak sky"],
    },
    "kuiper_belt": {
        "display": "the Kuiper Belt", "single": "Kuiper Belt", "category": "object",
        "pexels_keywords": ["kuiper belt space", "outer solar system", "icy objects space",
                            "distant solar system", "deep space objects"],
    },
    "voyager": {
        "display": "the Voyager Probes", "single": "Voyager", "category": "spacecraft",
        "pexels_keywords": ["voyager spacecraft", "deep space probe", "voyager nasa",
                            "interstellar probe", "space probe stars"],
    },
    "james_webb": {
        "display": "the James Webb Space Telescope", "single": "James Webb", "category": "spacecraft",
        "pexels_keywords": ["james webb telescope", "space telescope", "webb deep field",
                            "infrared telescope space", "telescope galaxies"],
    },
    "apollo": {
        "display": "the Apollo Missions", "single": "Apollo", "category": "mission",
        "pexels_keywords": ["apollo moon landing", "astronaut moon", "lunar mission",
                            "moon landing nasa", "apollo spacecraft"],
    },
    "andromeda": {
        "display": "the Andromeda Galaxy", "single": "Andromeda", "category": "galaxy",
        "pexels_keywords": ["andromeda galaxy", "spiral galaxy space", "andromeda night sky",
                            "distant galaxy", "galaxy stars"],
    },
    "red_giant": {
        "display": "Red Giants", "single": "red giant", "category": "star",
        "pexels_keywords": ["red giant star", "dying star space", "giant star",
                            "stellar evolution", "large red star"],
    },
    "white_dwarf": {
        "display": "White Dwarfs", "single": "white dwarf", "category": "star",
        "pexels_keywords": ["white dwarf star", "dense star space", "stellar remnant",
                            "collapsed star", "small hot star"],
    },
    "gravitational_waves": {
        "display": "Gravitational Waves", "single": "gravitational waves", "category": "phenomenon",
        "pexels_keywords": ["gravitational waves space", "spacetime ripples", "black hole merger",
                            "space time distortion", "cosmic collision"],
    },
    "mars_rover": {
        "display": "Mars Rovers", "single": "Mars rover", "category": "spacecraft",
        "pexels_keywords": ["mars rover", "rover red planet", "mars exploration rover",
                            "curiosity rover mars", "mars surface robot"],
    },
    "solar_system": {
        "display": "the Solar System", "single": "solar system", "category": "object",
        "pexels_keywords": ["solar system planets", "planets orbit", "solar system space",
                            "planetary system", "sun and planets"],
    },
    "milky_way_center": {
        "display": "the Galactic Center", "single": "galactic center", "category": "galaxy",
        "pexels_keywords": ["galactic center", "milky way core", "galaxy center space",
                            "sagittarius a star", "dense star field"],
    },
}
