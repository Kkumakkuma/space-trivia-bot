"""GPT가 우주 토픽 + Pexels 키워드 + 영문 hook/intro/facts/outro 일괄 생성.

ANIMALS 사전(=우주 토픽 사전)은 fallback용 (legacy). 기본은 GPT 동적 픽.

Note: 변수명/함수명은 animals/animal_key 그대로 유지 (main.py 호환). 의미상 우주 토픽.
"""
import json
import logging
import random

from config import ANIMALS, META_MODEL, OPENAI_API_KEY

log = logging.getLogger("spacebot.topics")


def pick_animal(rng: random.Random = None, exclude: list = None) -> str:
    """legacy fallback (사전에서 픽). pick_random_animal() 우선."""
    rng = rng or random.Random()
    pool = [k for k in ANIMALS.keys() if not exclude or k not in exclude]
    return rng.choice(pool)


# 컴필레이션 단어 차단 (단일 영상이므로)
_BAD_TITLE_EN = ["compilation", "best of", "top 10", "top 20", " mix", " list"]
_VALID_CATEGORIES = {"planet", "star", "galaxy", "moon", "phenomenon",
                     "spacecraft", "object", "constellation", "mission"}


def _has_bad(text: str, words) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def pick_random_animal(exclude_keys: list = None) -> dict:
    """GPT가 동적으로 우주 토픽 1개 + 메타데이터 일괄 생성.

    풀: well-known 50% + 덜 알려진 우주 사실 50%.
    실패 시 사전에서 fallback.

    반환: {animal_key, display, single, category, pexels_keywords, title_en,
           description_en, hook, intro, facts, outro, tags, comment}
    """
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY 없음 — fallback 사전 사용")
        key = pick_animal(exclude=exclude_keys or [])
        return _legacy_meta(key)

    exclude_str = ", ".join(exclude_keys[-30:]) if exclude_keys else "none"

    prompt = f"""You are a viral space scriptwriter for YouTube Shorts channel "Space Trivia 60s".
Pick ONE surprising space topic RIGHT NOW and write a 55-second script (1-minute Shorts).

CRITICAL CONSTRAINT: pexels_keywords MUST find PORTRAIT video on Pexels. So you must pick topics that are
WELL-PHOTOGRAPHED on stock footage sites. Avoid extremely obscure phenomena with no usable visuals.

GOOD SPACE TOPIC POOL (these have abundant Pexels portrait video — planet/galaxy/space/cosmos/stars):
- Planets: Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto (dwarf planet)
- Moons: Earth's Moon, Europa, Titan, Io, Enceladus, Phobos
- Stars/Phenomena: the Sun, supernova, neutron star, pulsar, black hole, white dwarf, red giant
- Galaxies: Milky Way, Andromeda, spiral galaxy, elliptical galaxy
- Cosmos: Big Bang, dark matter, dark energy, cosmic microwave background, gravitational waves
- Objects: asteroids, comets, meteors, nebulae (Orion, Eagle, Crab, Carina), exoplanets
- Spacecraft: ISS, Hubble, James Webb, Voyager, Apollo missions, SpaceX, Mars rovers
- Constellations: Orion, Big Dipper, Cassiopeia, Southern Cross, Pleiades

AVOID picking these topics (recently used): {exclude_str}

REQUIREMENTS:
- The fact MUST be SURPRISING / counter-intuitive / mind-blowing with a specific number when possible.
- **Each fact MUST be 36-46 words. NOT LESS THAN 36. NOT MORE THAN 46.** Count the words. Three short facts = REJECT and rewrite.
- Each fact = ONE rich sentence packed with a specific number + comparison/context + surprising consequence. Spoken at TTS rate +12% this gives ~12s per fact.
- Provide EXACTLY 4 facts (not 3). Total spoken length target: intro ~3s + 4 facts × ~12.5s + outro ~3s ≈ 56s.
- Hook line (intro narration) MUST start with "Did you know" / "Wait until you see" / "This is why" / "Watch this".
- Outro CTA MUST be one of: "Follow for more space facts." / "Follow for daily cosmic facts." / "Comment your favorite space mystery."
- Each fact MUST start differently (vary openers — never repeat "X is way more interesting" or any generic opener).
- pexels_keywords: 5 keywords. **EVERY keyword MUST be space/cosmos related** (e.g., for Saturn: "saturn rings", "saturn telescope", "planet rings space", "gas giant", "ringed planet"). NEVER use generic earthly terms.
- category: one of {sorted(_VALID_CATEGORIES)}.
- **Title (CRITICAL — single most important CTR factor)**: max 70 chars, MUST include the topic name + a CURIOSITY GAP hook. NEVER use "compilation", "best of", "top 10", "Amazing Facts About X" (too generic, kills CTR).
  Use ONE of these viral patterns (vary across topics):
  (a) Shocking number: "Saturn's Rings Will Vanish in 100 Million Years"
  (b) Wait what: "Wait, Jupiter Has 95 Moons (And One Has Oceans)"
  (c) You'd never guess: "You Won't Believe What Mars Looks Like at Night"
  (d) Conditional twist: "If the Sun Vanished, We'd Have 8 Minutes To Live"
  (e) The truth: "The Truth About Why Black Holes Aren't Holes"
  (f) Stop scrolling: "Stop Scrolling — A Star Just Exploded Last Night"
  (g) Question hook: "How Loud Is the Sun? You'd Be Shocked"
  Keep it punchy. Lowercase or Title Case both fine. NO emoji in title.
- Description: 2 sentences + 5 hashtags (no emoji).
- Hook (on-screen overlay text): 2-4 words like "Saturn • Trivia" or "Black Hole Facts".
- **comment** (auto-pinned reply-magnet comment under the video): ONE line, 8-20 words.
  GOAL: provoke viewers to actually reply. Pick ONE of these reply-magnet patterns:
  (a) Hot take / fight me — "Hot take: living on Mars would be miserable. Fight me 🚀"
  (b) Forced choice — "Saturn's rings or Jupiter's storm — which is the cooler view?"
  (c) Reincarnation / what-if — "If I could visit one planet for a day, it'd be Titan. Yours?"
  (d) Personal challenge — "Bet you can name 3 moons of Jupiter without Googling. Try it."
  (e) Confession bait — "Confession: I still get chills looking up at the Milky Way. Anyone else?"
  (f) Fill-in-the-blank — "If aliens watched Earth, they'd think we're ___"
  Vary the pattern across topics. Use 0-1 emoji max. **NEVER** these dead patterns: "Which fact surprised you most?", "Tag a friend who...", "Did you know any of these?", "Like if you learned something" — those get zero replies.

Output STRICT JSON only:
{{
  "animal_key": "snake_case_id (e.g. saturn, black_hole)",
  "display": "Saturn",
  "single": "Saturn",
  "category": "planet",
  "pexels_keywords": ["saturn rings", "planet rings space", "gas giant", "saturn telescope", "ringed planet"],
  "title_en": "...",
  "description_en": "... #shorts #space #astronomy #cosmos #saturn",
  "hook": "Saturn • Trivia",
  "intro": "Did you know Saturn does this?",
  "facts": ["fact1 (36-46 words)", "fact2 (36-46 words)", "fact3 (36-46 words)", "fact4 (36-46 words)"],
  "outro": "Follow for more space facts.",
  "comment": "Saturn's rings could fit 1 million Earths inside — let that sink in",
  "tags": ["shorts","space","astronomy","cosmos","trivia","did you know","saturn","planet","nasa"]
}}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=META_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=1.0,
            max_tokens=1200,
        )
        d = json.loads(resp.choices[0].message.content)
    except Exception as e:
        log.warning(f"GPT 우주 토픽 픽 실패 → legacy fallback: {e}")
        key = pick_animal(exclude=exclude_keys or [])
        return _legacy_meta(key)

    # 검증 + 정제
    animal_key = (d.get("animal_key") or "").strip().lower().replace(" ", "_") or "space_topic"
    display = (d.get("display") or "Space").strip()
    single = (d.get("single") or "space topic").strip()
    category = (d.get("category") or "phenomenon").strip().lower()
    if category not in _VALID_CATEGORIES:
        category = "phenomenon"

    # === 매칭 안전선: pexels_keywords가 너무 generic("space", "stars" 단독)이 아닌지 확인 ===
    raw_kw = d.get("pexels_keywords") or []
    if not isinstance(raw_kw, list):
        raw_kw = []
    sanitized_kw = []
    for kw in raw_kw:
        if not isinstance(kw, str):
            continue
        kw = kw.strip()
        if not kw or len(kw) < 4:
            continue
        sanitized_kw.append(kw)
    if not sanitized_kw:
        sanitized_kw = [single, f"{single} space", "deep space", "cosmos", "stars night"]
    pexels_kw = sanitized_kw[:8]

    title_en = (d.get("title_en") or f"{display} Facts").strip()[:95]
    if _has_bad(title_en, _BAD_TITLE_EN):
        title_en = f"{display}: 4 Cosmic Facts"
    _generic = ["amazing facts about", "wild facts about", "interesting facts about",
                "fascinating facts about", "facts you didn't know"]
    if any(g in title_en.lower() for g in _generic):
        log.warning(f"generic title pattern → CTR-friendly fallback: {title_en}")
        title_en = f"Wait — {display} Does This?!"

    facts = d.get("facts") or []
    if not isinstance(facts, list) or len(facts) < 3:
        facts = [f"{display} surprises scientists with phenomena we still don't fully understand."] * 4
    facts = [str(f).strip() for f in facts[:5] if str(f).strip()]
    if len(facts) < 3:
        facts = facts + [f"{display} hides cosmic secrets most people never learn."] * (3 - len(facts))
    if any(len(f.split()) < 32 for f in facts):
        log.warning(f"facts too short ({[len(f.split()) for f in facts]}), expanding to 36-46 words each...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            n = len(facts)
            joined = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
            expand_prompt = f"""Rewrite each fact to EXACTLY 36-46 words. Add a specific number, a comparison or context, and a surprising consequence. Keep them TRUE. Vary the sentence openers.

Original {n} facts about {display}:
{joined}

Output STRICT JSON: {{"facts": [{', '.join(['"fact (36-46 words)"'] * n)}]}}"""
            r2 = client.chat.completions.create(
                model=META_MODEL,
                messages=[{"role": "user", "content": expand_prompt}],
                response_format={"type": "json_object"},
                temperature=0.7,
                max_tokens=900,
            )
            d2 = json.loads(r2.choices[0].message.content)
            new_facts = d2.get("facts") or []
            if isinstance(new_facts, list) and len(new_facts) >= len(facts):
                facts = [str(f).strip() for f in new_facts[:len(facts)]]
                log.info(f"facts expanded to {[len(f.split()) for f in facts]}")
        except Exception as e:
            log.warning(f"expand 실패 — 원본 유지: {e}")

    comment = (d.get("comment") or "").strip()
    if not comment or len(comment) < 10:
        comment = f"What's the wildest thing you knew about {display}?"
    comment = comment[:280]

    return {
        "animal_key": animal_key,
        "display": display,
        "single": single,
        "category": category,
        "pexels_keywords": pexels_kw,
        "title_en": title_en,
        "description_en": (d.get("description_en") or f"4 cosmic facts about {display}.")[:4900],
        "hook": (d.get("hook") or f"{display} • Trivia")[:40],
        "intro": (d.get("intro") or f"Did you know {display} does this?").strip(),
        "facts": facts,
        "outro": (d.get("outro") or "Follow for more space facts.").strip(),
        "comment": comment,
        "tags": d.get("tags") or ["shorts", "space", "astronomy", "cosmos", "trivia",
                                  "did you know", animal_key.replace("_", " "), single, "nasa"],
    }


def expand_legacy_to_rich(meta: dict) -> dict:
    """legacy fallback에서 만든 generic meta를 GPT로 풀어 진짜 facts 생성.
    실패 시 호출 측에서 원본 meta 그대로 사용 (안전).
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 없음 — expand 불가")

    display = meta.get("display", "Space")
    single = meta.get("single", "space topic")

    prompt = f"""Write a 55-second YouTube Shorts script about {display} ({single}).

REQUIREMENTS:
- 4 facts, each EXACTLY 36-46 words. Specific numbers + comparison + surprising consequence per fact.
- Each fact opens DIFFERENTLY (no repeated openers).
- Intro: 5-9 words, MUST start with "Did you know" / "Wait until you see" / "This is why" / "Watch this".
- Outro: 4-7 words CTA, one of: "Follow for more space facts." / "Follow for daily cosmic facts." / "Comment your favorite space mystery."
- Hook (overlay text): 2-4 words like "{display} • Trivia".
- **Title (CRITICAL CTR — pick ONE viral pattern, max 70 chars, must include "{single}" or "{display}", NO emoji)**:
  (a) Shocking number: "{display} Has ___ ?! Here's Why"
  (b) Wait what: "Wait, {display} ___ A Year"
  (c) You won't believe: "You Won't Believe What {display} Does at Night"
  (d) Conditional twist: "If {display} ___, We'd Be in Trouble"
  (e) Stop scrolling: "Stop Scrolling — {display} Just ___"
  (f) Question hook: "How Does {display} Survive ___?"
  NEVER use "Amazing Facts About", "Wild Facts About", "Facts You Didn't Know" — generic patterns kill CTR.
- Description: 2 sentences + 5 hashtags (no emoji).
- comment (reply-magnet, 8-20 words): hot take / forced choice / what-if / personal challenge / confession bait / fill-in-the-blank. Vary across topics. 0-1 emoji max. NEVER "Which fact surprised you most?" or "Tag a friend".

Output STRICT JSON: {{
  "title_en": "...",
  "description_en": "... #shorts #space #astronomy #cosmos #{single.lower().replace(' ', '')}",
  "hook": "{display} • Trivia",
  "intro": "Did you know {display} does this?",
  "facts": ["fact1 (36-46 words)", "fact2", "fact3", "fact4"],
  "outro": "Follow for more space facts.",
  "comment": "..."
}}"""

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=META_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.9,
        max_tokens=1100,
    )
    d = json.loads(resp.choices[0].message.content)

    facts = d.get("facts") or []
    if not isinstance(facts, list) or len(facts) < 3:
        raise RuntimeError(f"GPT facts 부족: {facts}")
    facts = [str(f).strip() for f in facts[:5] if str(f).strip()]

    title_en = (d.get("title_en") or f"Wait — {display} Does This?!").strip()[:95]
    if _has_bad(title_en, _BAD_TITLE_EN):
        title_en = f"Wait — {display} Does This?!"
    _generic = ["amazing facts about", "wild facts about", "interesting facts about",
                "fascinating facts about", "facts you didn't know"]
    if any(g in title_en.lower() for g in _generic):
        log.warning(f"legacy expand: generic title → CTR-friendly fallback: {title_en}")
        title_en = f"Wait — {display} Does This?!"

    comment = (d.get("comment") or "").strip()
    if not comment or len(comment) < 10:
        comment = f"What's the wildest thing you knew about {display}?"
    comment = comment[:280]

    return {
        "title_en": title_en,
        "description_en": (d.get("description_en") or f"4 cosmic facts about {display}.")[:4900],
        "intro": (d.get("intro") or f"Did you know {display} does this?").strip(),
        "facts": facts,
        "outro": (d.get("outro") or "Follow for more space facts.").strip(),
        "hook": (d.get("hook") or f"{display} • Trivia").strip()[:40],
        "comment": comment,
    }


def _legacy_meta(animal_key: str) -> dict:
    """fallback: ANIMALS 사전(=우주 토픽) + 기본 fact."""
    cfg = ANIMALS.get(animal_key) or list(ANIMALS.values())[0]
    display = cfg["display"]
    single = cfg["single"]
    return {
        "animal_key": animal_key,
        "display": display,
        "single": single,
        "category": cfg.get("category", "phenomenon"),
        "pexels_keywords": cfg["pexels_keywords"],
        "title_en": f"4 Cosmic Facts About {display}",
        "description_en": f"4 surprising facts about {display}. \n\n#shorts #space #astronomy #cosmos #didyouknow",
        "hook": f"{display} • Trivia",
        "intro": f"Did you know {display} does this?",
        "facts": [
            f"{display} hides phenomena that defy what scientists thought possible.",
            f"Most people have no idea how massive or extreme {display} actually is in the cosmos.",
            f"There is a fascinating thing about {display} that even most astronomy fans never get to see.",
            f"Researchers have only recently begun documenting how {display} truly behaves under extreme physics.",
        ],
        "outro": "Follow for more space facts.",
        "comment": f"What's the wildest thing you knew about {display}?",
        "tags": ["shorts", "space", "astronomy", "cosmos", "trivia",
                 "did you know", animal_key.replace("_", " "), single, "nasa"],
    }


def make_meta(animal_key: str) -> dict:
    """legacy compat: ANIMALS 사전 키 받아 기본 메타 + GPT 보강."""
    cfg = ANIMALS[animal_key]
    display = cfg["display"]
    single = cfg["single"]

    fallback = {
        "title_en": f"4 Cosmic Facts About {display} You Didn't Know",
        "description_en": (
            f"4 surprising facts about {display} in 60 seconds. "
            f"\n\n#shorts #space #astronomy #cosmos #didyouknow"
        ),
        "intro": f"Did you know {display} does this?",
        "facts": [
            f"{display} surprise astronomers with extreme physics we don't see anywhere on Earth.",
            f"Most folks have no idea what {display} actually contains or how massive it really is.",
            f"This one fact about {display} might change how you look at the night sky.",
            f"The latest measurements about {display} reveal something most science books still get wrong.",
        ],
        "outro": "Follow for more space facts.",
        "hook": f"{display} • Trivia",
        "tags": [
            "shorts", "space", "astronomy", "cosmos", "trivia",
            "did you know", animal_key.replace("_", " "), single, "nasa",
        ],
    }

    if not OPENAI_API_KEY:
        return fallback

    prompt = f"""You are a viral YouTube Shorts scriptwriter for "Space Trivia 60s".
Benchmark: PBS Space Time, Kurzgesagt, NASA, Astrum — punchy hook, surprising fact, CTA.

Generate a ~55-second script about {display} ({single}). Structure:
- 0~3s: hook line (must START with "Did you know" / "Wait until you see" / "This is why" — proven viral templates)
- 3~50s: four RICH facts, each ~12.5s spoken (about 36-46 words each)
- 50~55s: outro CTA

HARD RULES:
- Each fact MUST be EXACTLY 36-46 words. Count the words.
- Each fact = ONE long sentence. Pack a specific number + concrete comparison/extra detail.
- Always include a specific number/measurement. Always add a comparison or surprising consequence.
- Plain spoken English, no jargon, no emoji.
- Title: catchy, max 90 chars, includes "{display}". NEVER use "compilation", "best of", "top 10".
- Description: 2 sentences hook + 5 hashtags (no emoji).
- Hook (on-screen overlay text): 2-4 words, e.g. "{display} • Trivia".
- Intro: 5~9 words, MUST start with "Did you know" / "Wait until you see" / "This is why" / "Watch this".
- Outro: 4~7 words CTA, MUST be one of: "Follow for more space facts." / "Follow for daily cosmic facts." / "Comment your favorite space mystery."

Output STRICT JSON only:
{{
  "title_en": "...",
  "description_en": "...",
  "hook": "...",
  "intro": "...",
  "facts": ["fact1 (36-46 words)", "fact2", "fact3", "fact4"],
  "outro": "..."
}}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=META_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.9,
            max_tokens=900,
        )
        d = json.loads(resp.choices[0].message.content)
    except Exception as e:
        log.warning(f"GPT 메타 실패 fallback: {e}")
        return fallback

    title_en = (d.get("title_en") or fallback["title_en"])[:95]
    if _has_bad(title_en, _BAD_TITLE_EN):
        log.warning(f"bad word in title fallback: {title_en}")
        title_en = fallback["title_en"]

    facts = d.get("facts") or []
    if not isinstance(facts, list) or len(facts) < 3:
        log.warning(f"facts 부족 fallback: {facts}")
        facts = fallback["facts"]
    facts = [str(f).strip() for f in facts[:4]]

    return {
        "title_en": title_en,
        "description_en": (d.get("description_en") or fallback["description_en"])[:4900],
        "intro": (d.get("intro") or fallback["intro"]).strip(),
        "facts": facts,
        "outro": (d.get("outro") or fallback["outro"]).strip(),
        "hook": (d.get("hook") or fallback["hook"]).strip()[:40],
        "tags": fallback["tags"],
    }
