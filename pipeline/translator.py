"""title/description을 7개국 언어로 번역 → YouTube localizations."""
import json
import logging

from config import META_MODEL, OPENAI_API_KEY

log = logging.getLogger("spacebot.translator")

LOCALIZATION_LANGS = [
    ("ko", "Korean"),
    ("ja", "Japanese"),
    ("es", "Spanish"),
    ("pt", "Portuguese (Brazilian)"),
    ("de", "German"),
    ("fr", "French"),
    ("hi", "Hindi"),
]


def translate_for_youtube(title_en: str, description_en: str) -> dict:
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY 없음 — localizations 스킵")
        return {}

    lang_list = "\n".join(f"- {c}: {n}" for c, n in LOCALIZATION_LANGS)
    prompt = f"""You are a YouTube Shorts localization expert for an animal trivia channel. Rewrite the English title for each language as a NATIVE-VIRAL title — NOT a literal translation. Each language has its own click-through patterns.

Languages:
{lang_list}

ENGLISH TITLE (source mood/topic):
{title_en}

ENGLISH DESCRIPTION:
{description_en}

PER-LANGUAGE VIRAL TITLE STYLE (CRITICAL — match the platform feel):

- **ko (Korean)**: 짧고 후크감 강한 한국 YouTube Shorts 톤. 패턴 예:
  "[충격] 돌고래가 1,000개 단어로 대화한다고?!" / "이거 알면 돌고래 못 봐;;" /
  "돌고래 IQ가 인간이랑 비슷하다는 게 사실?" / "돌고래는 왜 ___ 할까?" /
  "??? 돌고래가 이런 짓을 한다고?" / "지금 안 보면 후회함 — 돌고래 진짜 능력".
  Max 60 chars. NO emoji. 호기심 갭 + 의문형 + 숫자/비교 활용.

- **ja (Japanese)**: ショート動画らしい強いフック. 例:
  "イルカが1000個の言葉で会話する!?" / "これ知ったらイルカ見れない…" /
  "ヤバい…イルカの本当の能力". 60文字以内. 絵文字なし.

- **es (Spanish)**: titulares cortos con gancho. 예: "¡Los delfines hablan con 1.000 palabras?!" / "Esto cambiará cómo ves a los delfines". Max 70 chars. Sin emojis.

- **pt (Portuguese-BR)**: títulos curtos com choque. 예: "Os golfinhos falam mil palavras?!" / "Isso vai mudar tudo que você sabe sobre golfinhos". Max 70 chars. Sem emojis.

- **de (German)**: kurze Hook-Titel. "Delfine sprechen mit 1.000 Wörtern?!" / "So clever sind Delfine wirklich". Max 75 chars. Keine Emojis.

- **fr (French)**: titres courts accrocheurs. "Les dauphins parlent avec 1000 mots?!" / "Ce que les dauphins font vraiment". Max 75 chars. Sans emoji.

- **hi (Hindi)**: छोटे आकर्षक शीर्षक. "डॉल्फिन 1,000 शब्दों से बात करती हैं?!" / "ये जानकर डॉल्फिन देखना नहीं भूलोगे". Max 80 chars.

DO NOT translate "Amazing Facts About X" literally. Always rewrite with curiosity gap + number/twist. Description: natural translation, keep 5 hashtags at end (translate or keep English where idiomatic).

Output STRICT JSON only (no markdown):
{{
  "ko": {{"title":"...", "description":"..."}},
  "ja": {{"title":"...", "description":"..."}},
  "es": {{...}},
  "pt": {{...}},
  "de": {{...}},
  "fr": {{...}},
  "hi": {{...}}
}}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=META_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=2500,
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as e:
        log.warning(f"번역 실패 — locales 스킵: {e}")
        return {}

    out = {}
    for code, _ in LOCALIZATION_LANGS:
        e = data.get(code) or {}
        title = (e.get("title") or "").strip()
        desc = (e.get("description") or "").strip()
        if title:
            out[code] = {"title": title[:95], "description": desc[:4900]}
    log.info(f"localizations 완료: {','.join(out.keys())}")
    return out
