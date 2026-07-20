"""GPT가 우주 토픽 + Pexels 키워드 + 영문 hook/intro/facts/outro 일괄 생성.

ANIMALS 사전(=우주 토픽 사전)은 fallback용 (legacy). 기본은 GPT 동적 픽.

Note: 변수명/함수명은 animals/animal_key 그대로 유지 (main.py 호환). 의미상 우주 토픽.
"""
import json
import logging
import random
import re

from config import ANIMALS, META_MODEL, OPENAI_API_KEY

try:  # 모델 3분할 (2026-07-10). 구버전 config 호환을 위해 폴백.
    from config import SCRIPT_MODEL, FACTCHECK_MODEL
except ImportError:  # pragma: no cover
    SCRIPT_MODEL = FACTCHECK_MODEL = META_MODEL

log = logging.getLogger("spacebot.topics")

# === Angle 로테이션 (우주 도메인) — 같은 topic 재등장 시/전역 다양성 확보 ===
ANGLES = [
    "scale_comparison", "formation_history", "extreme_conditions", "exploration_missions",
    "future_events", "myths_debunked", "records", "weird_physics",
    "discovery_story", "what_if",
]


def pick_animal(rng: random.Random = None, exclude: list = None) -> str:
    """legacy fallback (사전에서 픽). pick_random_animal() 우선.

    2026-05-07 fix: exclude가 ANIMALS dict 전체를 덮으면 pool이 빈 리스트가 되어
    random.choice([]) IndexError 발생 → 빈 pool일 때 exclude 무시하고 전체에서 픽.
    """
    rng = rng or random.Random()
    pool = [k for k in ANIMALS.keys() if not exclude or k not in exclude]
    if not pool:
        pool = list(ANIMALS.keys())
    return rng.choice(pool)


# 컴필레이션 단어 차단 (단일 영상이므로)
_BAD_TITLE_EN = ["compilation", "best of", "top 10", "top 20", " mix", " list"]
_VALID_CATEGORIES = {"planet", "star", "galaxy", "moon", "phenomenon",
                     "spacecraft", "object", "constellation", "mission"}

# 다른 우주 토픽 단어 — comment에서 본 토픽 외 다른 토픽이 등장하면 off-topic 의심.
# 예: Saturn 영상인데 comment에 "Jupiter" 들어감 → 사용자 사고 (2026-05-06).
_OTHER_TOPIC_WORDS = {
    "saturn", "jupiter", "mars", "venus", "mercury", "neptune", "uranus", "pluto",
    "earth", "moon", "sun", "titan", "europa", "ganymede", "enceladus",
    "andromeda", "milky", "milkyway",
    "comet", "asteroid", "nebula", "supernova", "pulsar", "blackhole",
    "hubble", "voyager", "iss", "apollo",
}


def _has_bad(text: str, words) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


# 진부 양산 opener — 과거 이력에 없어도(신규 채널/prev_titles 누락) 무조건 차단.
# title_template_repeats는 '과거 반복'만 보므로 첫 발생·이력 장애 시 이 opener가 샌다(codex 지적 2026-07-11).
_BANNED_OPENERS = ("you won't believe", "you wont believe", "wait until you see",
                   "wait til you see", "did you know", "you'll never guess",
                   "youll never guess", "you won't guess")


def _starts_with_banned_opener(title) -> bool:
    t = (title or "").strip().lstrip("\"'“”‘’ ")
    t = t.replace("’", "'").replace("‘", "'").lower()  # 스마트 아포스트로피 → ASCII 정규화
    return any(t.startswith(b) for b in _BANNED_OPENERS)


def _title_needs_fix(title, prev_titles, subjects) -> bool:
    """제목이 (1) 금지 opener로 시작하거나 (2) 과거 템플릿 지문과 겹치면 True.
    (1)은 prev_titles 유무와 무관 — deterministic 최종 방어."""
    if _starts_with_banned_opener(title):
        return True
    if prev_titles:
        from pipeline import history as _hist
        if _hist.title_template_repeats(title, prev_titles, subjects=subjects):
            return True
    return False


# === 2026-07-05 업그레이드 공통 로직 (동물봇/우주봇 동일 코드) ===
FORMATS = ["classic_trivia", "myth_vs_fact", "top_records", "how_it_works"]

# facts·intro(내레이션)에서 금지하는 클리셰/과장. title 훅 과장("You Won't Believe..")은 허용.
BANNED_PHRASES = [
    "more interesting than people think", "way more interesting", "more than people think",
    "scientists were shocked", "scientists were stunned", "scientists are stunned",
    "will blow your mind", "blow your mind", "mind-blowing", "mind blowing",
    "you won't believe your eyes", "defy what scientists thought",
    "hides phenomena that defy", "hides cosmic secrets", "number will shock you",
    "doctors hate", "you won't believe how",
]


def _has_banned(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in BANNED_PHRASES)


def _sweep_deterministic(display: str, facts: list, flagged=()) -> list:
    """금지문구/최종 false fact를 안전 템플릿으로 교체하는 결정적 최종 방어.

    (여행봇 이식 2026-07-17 — 변동정보 regex는 여행 전용이라 제외, banned+flagged만.)
    GPT 팩트체크 호출이 실패해도 이 sweep은 항상 적용된다."""
    out = list(facts)
    for i in range(len(out)):
        if i in flagged or _has_banned(out[i]):
            log.warning(f"factcheck: fact{i+1} false/금지문구 → 안전 fact 교체: {out[i][:60]!r}")
            out[i] = _safe_fact(display, i)
    return out


def _count_numbers(texts) -> int:
    return sum(len(re.findall(r"\d[\d,\.]*", t or "")) for t in texts)


def _validate_fact_keywords(raw, single: str, generic: list, n: int = 4) -> list:
    """per-fact 배경 키워드 n개 정규화 — 각 키워드에 topic명(single) 포함 보장, 부족분 generic 폴백."""
    single_l = (single or "").lower().strip()
    gk = [g for g in (generic or []) if isinstance(g, str) and g.strip()] or [single or "nature"]
    raw = raw if isinstance(raw, list) else []
    out = []
    for i in range(n):
        kw = raw[i].strip() if i < len(raw) and isinstance(raw[i], str) and raw[i].strip() else ""
        if not kw:
            kw = gk[i % len(gk)]
        if single_l and single_l not in kw.lower():
            kw = f"{single} {kw}".strip()
        out.append(kw[:80])
    return out


def _safe_fact(display: str, idx: int) -> str:
    """팩트체크 최종 실패 시 교체할 안전(보수적·참) fact 템플릿."""
    templates = [
        f"{display} have been studied by researchers for many years, and patient observation keeps revealing careful details that rarely make it into short popular summaries.",
        f"Much of what we reliably know about {display} comes from decades of measurement, and specialists still refine those figures as better instruments and methods arrive.",
        f"{display} remain an active area of research, so reference sources are updated regularly as fresh, carefully verified observations gradually replace older estimates.",
        f"Scientists describe {display} using values that get revised over time, which is exactly why cautious, up-to-date sources matter more than confident one-line claims.",
    ]
    return templates[idx % len(templates)]


def adversarial_factcheck(display, single, hook, facts, source_text, max_rounds=2):
    """적대적 팩트체크 패스 (생성 → 팩트체크 단계). temp 0 회의적 페르소나로 hook+facts 판정.

    각 항목을 supported / plausible / false_or_exaggerated로 판정하고 SOURCE와 대조.
    false_or_exaggerated 항목만 SOURCE 근거로 교정 재생성(최대 max_rounds회) 후,
    최종 판정 1회로 '그래도 false로 남는' fact만 안전 fact로 교체 → 과잉 교체 방지 + 허위 자막 차단.

    2026-07-10 강화 — 판정 기준을 SOURCE 유무로 분기한다.
    - SOURCE 있음: **SOURCE에 없는 구체 수치·최상급·순위는 false로 판정**한다.
      (기존 "애매하면 plausible"이 환각 수치의 통과 경로였다. 토성 고리 소멸 100M vs 300M년
       모순이 이 구멍으로 나갔다.) false여도 교정 라운드에서 SOURCE 값으로 고쳐 쓰므로
      발행이 막히지 않는다. 끝까지 남은 것만 안전 fact로 교체.
    - SOURCE 없음: 근거가 없으니 과잉 탈락을 피해 기존 보수 모드 유지(확신할 때만 false).
      단 SOURCE 없는 토픽은 애초에 pick 단계에서 재픽되므로 드문 경로다.

    검증 모델은 FACTCHECK_MODEL — 생성(SCRIPT_MODEL)과 다른 계열이라 자기합치 편향이 준다.
    반환: (hook, facts). OPENAI 키 없으면 원본 그대로.
    """
    if not OPENAI_API_KEY:
        return hook, facts
    facts = [str(f).strip() for f in (facts or []) if str(f).strip()]
    if not facts:
        return hook, facts
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    src = (source_text or "").strip()
    src_block = src if src else (
        "(No source text available — rely ONLY on well-established, uncontroversial "
        "scientific consensus and be extra conservative.)"
    )
    # 판정 기준: SOURCE가 있으면 엄격(근거 없는 수치 = false), 없으면 보수(확신할 때만 false).
    if src:
        rubric = """- "supported": the claim — INCLUDING every number in it — is directly verifiable in SOURCE.
- "plausible": a QUALITATIVE claim (no specific number, no superlative, no ranking) that is uncontroversial textbook science and is not contradicted by SOURCE.
- "false_or_exaggerated": mark whenever ANY of these hold:
  (a) the claim contradicts SOURCE or scientific consensus;
  (b) it states a SPECIFIC NUMBER, measurement, count, age, speed, or percentage that does NOT appear in SOURCE and that you cannot verify — an unverifiable precise number is the single most common way this channel has published false claims, so treat it as false, NOT plausible;
  (c) it asserts a superlative or ranking ("the largest", "the only", "the smartest") that SOURCE does not state;
  (d) it repeats a debunked myth;
  (e) it uses empty hype ("scientists were shocked", "will blow your mind", "more interesting than people think").
A believable-sounding number you cannot find in SOURCE is FALSE, not plausible. Do not give it the benefit of the doubt."""
    else:
        rubric = """- "supported": uncontroversial established consensus.
- "plausible": reasonable mainstream science. USE THIS (not false) whenever a claim is believable — you have no SOURCE to check against, so do not over-reject.
- "false_or_exaggerated": mark ONLY when you are CONFIDENT the claim is wrong — it contradicts consensus, cites a clearly wrong or outdated number, repeats a debunked myth, states a disputed ranking or superlative as settled fact, or uses empty hype ("scientists were shocked", "will blow your mind"). When in doubt here, choose "plausible"."""

    def _judge(cur_hook, cur_facts):
        """GPT 1회: 각 항목 판정 + false 항목 교정본 반환. (verdicts, hook, facts)."""
        item_lines = [f"hook: {cur_hook}"] + [f"fact{i+1}: {f}" for i, f in enumerate(cur_facts)]
        verdict_keys = '"hook": "...", ' + ", ".join(f'"fact{i+1}": "..."' for i in range(len(cur_facts)))
        facts_slot = ", ".join(['"<fact 28-33 words>"'] * len(cur_facts))
        prompt = f"""You are a SKEPTICAL science fact-checker for a 60-second YouTube Shorts about {display} ({single}).
Viewers actively fact-check and leave angry comments when a claim is wrong, so catch every FALSE or EXAGGERATED statement before it airs.

SOURCE TEXT (primary reference, from Wikipedia):
{src_block}

Judge EACH item below as exactly one of:
{rubric}

For EVERY item judged "false_or_exaggerated", REWRITE it to be accurate and supported while staying engaging:
- Replace any unverifiable number with a value you can point to in SOURCE. If SOURCE has no number for that claim, drop the number and make a true qualitative statement instead.
- Volatile counts that change with new discoveries (moon counts, confirmed exoplanets, population estimates, species counts, record holders): use conservative phrasing like "more than N" or "at least N" with the SOURCE value; never assert a precise disputed count as absolute.
- Do NOT invent numbers you are unsure of; a true qualitative statement beats a false precise one.
- Each fact MUST stay 28-33 words. The hook MUST be 12 words or fewer, punchy, and NEVER start with "Did you know".
- Keep every "supported"/"plausible" item EXACTLY as written.

ITEMS:
{chr(10).join(item_lines)}

Output STRICT JSON:
{{"verdicts": {{{verdict_keys}}}, "hook": "<corrected or unchanged>", "facts": [{facts_slot}]}}"""
        resp = client.chat.completions.create(
            model=FACTCHECK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=1200,
        )
        d = json.loads(resp.choices[0].message.content)
        verdicts = d.get("verdicts") or {}
        nh = (d.get("hook") or "").strip() or cur_hook
        nf = d.get("facts") or []
        if isinstance(nf, list) and len(nf) == len(cur_facts):
            nf = [str(x).strip() or cur_facts[i] for i, x in enumerate(nf)]
        else:
            nf = list(cur_facts)
        return verdicts, nh, nf

    cur_hook = (hook or "").strip()
    cur_facts = list(facts)
    final_flagged = []
    # 교정 라운드 max_rounds회 → 마지막(rnd==max_rounds)은 판정 전용(교정 미반영, 잔존 false 확정)
    for rnd in range(max_rounds + 1):
        try:
            verdicts, nh, nf = _judge(cur_hook, cur_facts)
        except Exception as e:
            log.warning(f"adversarial factcheck 호출 실패 (round {rnd+1}) — 현재 버전 유지: {e}")
            # GPT 판정이 불가해도 결정적 백스톱(금지문구 sweep)은 적용하고 반환
            return cur_hook, _sweep_deterministic(display, cur_facts)
        flagged = [i for i in range(len(cur_facts))
                   if str(verdicts.get(f"fact{i+1}", "")).lower().startswith("false")]
        hook_flagged = str(verdicts.get("hook", "")).lower().startswith("false")
        log.info(f"factcheck round {rnd+1}: hook_false={hook_flagged} facts_false={flagged or 'none'}")
        if not flagged and not hook_flagged:
            final_flagged = []
            break
        if rnd >= max_rounds:
            # 판정 전용 라운드: 교정 반영 안 하고 잔존 false만 확정 (hook 교정만 반영)
            final_flagged = flagged
            if hook_flagged:
                cur_hook = nh
            break
        # 교정 라운드: 교정본 반영 후 다음 라운드 재판정
        cur_hook, cur_facts = nh, nf
    # 잔존 false + 금지문구 fact는 안전 fact로 교체 (허위 자막 차단)
    cur_facts = _sweep_deterministic(display, cur_facts, final_flagged)
    return cur_hook, cur_facts


# 도메인 맥락 (동음이의 위키 grounding 방지 — config.CONTENT_DOMAIN로 분기)
_DOMAIN_CONTEXT = {
    "space": "space astronomy planet moon star galaxy cosmos nebula NASA spacecraft universe orbit solar",
    "animal": "animal wildlife species mammal bird fish reptile amphibian insect nature",
}


def _content_domain() -> str:
    try:
        from config import CONTENT_DOMAIN
        return (CONTENT_DOMAIN or "").lower().strip()
    except Exception:
        return ""


def _keywords_for_facts(display, single, facts):
    """최종 facts에 맞춘 per-fact 배경 검색 키워드 재생성 (각 topic명 포함). 실패 시 None."""
    if not OPENAI_API_KEY or not facts:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    n = len(facts)
    joined = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
    prompt = f"""For each fact below about {display} ({single}), give ONE short stock-video search keyword (2-4 words) describing the best background visual for THAT fact. EVERY keyword MUST contain "{single}". Keep the SAME order.

{joined}

Output STRICT JSON: {{"fact_keywords": [{', '.join(['"..."'] * n)}]}}"""
    try:
        resp = client.chat.completions.create(
            model=META_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=300,
        )
        kws = json.loads(resp.choices[0].message.content).get("fact_keywords")
        if isinstance(kws, list) and len(kws) >= n:
            return _validate_fact_keywords(kws, single, [], n=n)
    except Exception as e:
        log.warning(f"fact_keywords 재생성 실패 — 기존 유지: {e}")
    return None


def _finalize_fact_keywords(meta: dict, generic: list = None) -> dict:
    """(F) 모든 fact 변경(differentiate/factcheck/consistency) 이후 마지막에 fact_keywords 확정.

    meta['_kw_facts'](키워드 도출 당시 facts 스냅샷)와 현재 facts가 다르면 재도출.
    재도출 실패 시 최소 topic명 포함만 보장. 스냅샷 키는 pop.
    """
    facts = meta.get("facts") or []
    snap = meta.pop("_kw_facts", None)
    single = meta.get("single", "")
    display = meta.get("display", "")
    if snap is not None and list(snap) == list(facts):
        return meta  # 변화 없음 → 기존 키워드 유지
    new_kw = _keywords_for_facts(display, single, facts)
    if new_kw:
        meta["fact_keywords"] = new_kw
        log.info("fact_keywords 확정 (최종 facts 기준 재도출)")
    else:
        meta["fact_keywords"] = _validate_fact_keywords(
            meta.get("fact_keywords"), single, generic or [], n=(len(facts) or 4))
    return meta


def _strip_hook_deterministic(hook, max_words=12):
    """API 없이 결정적으로 hook 정리: 'Did you know (that)' 시작 제거 + 첫 글자 대문자 + 단어수 컷."""
    h = (hook or "").strip()
    h = re.sub(r"^\s*did you know\s*(that\s+)?", "", h, flags=re.I).strip()
    h = re.sub(r"^\s*(wait until you see|watch this|this is why)\b[,:]?\s*", "", h, flags=re.I).strip()
    if h:
        h = h[0].upper() + h[1:]
    words = h.split()
    if len(words) > max_words:
        words = words[:max_words]
    out = " ".join(words)
    return out or (hook or "").strip()


def _enforce_hook_length(display, single, hook, max_words=12):
    """(5) hook(=intro 나레이션) 코드 강제: ①≤max_words ②'Did you know' 시작 금지 — **분리 검사**.
    문제 있으면 GPT rewrite 시도 → 실패/무키 시 결정적 제거(_strip_hook_deterministic)."""
    h = (hook or "").strip()
    too_long = len(h.split()) > max_words
    banned_start = h.lower().startswith("did you know")
    if not too_long and not banned_start:
        return h
    if OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            prompt = (f"Rewrite this opening hook for a 60-second Shorts about {display} to "
                      f"{max_words} words or FEWER. Keep the key number or shocking comparison. "
                      f"It MUST NOT start with \"Did you know\" (or \"Wait until you see\"/\"Watch this\"). "
                      f"Return ONLY the hook line, no quotes.\n\nHook: {h}")
            resp = client.chat.completions.create(
                model=META_MODEL, messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=40)
            out = (resp.choices[0].message.content or "").strip().strip('"').splitlines()[0].strip()
            if out and len(out.split()) <= max_words and not out.lower().startswith("did you know"):
                log.info(f"hook 교정: len={len(h.split())}→{len(out.split())}, banned_start={banned_start}")
                return out
        except Exception as e:
            log.warning(f"hook GPT 교정 실패 → 결정적 제거: {e}")
    return _strip_hook_deterministic(h, max_words)


def _retitle_unique(display: str, single: str, bad_title: str, prev_titles: list,
                    tries: int = 3) -> str:
    """제목이 최근 템플릿의 복제본일 때 구조가 다른 제목으로 재생성.

    prev_titles는 (title, topic_key) 튜플 리스트다 — 지문 계산 시 각 제목의 주제어를 뺀다.
    GPT가 새 제목도 같은 템플릿을 쓰면 최대 tries회 재시도하고, 끝내 실패하면
    결정적(deterministic) 대체 제목을 만든다 — 절대 양산 제목을 그대로 두지 않는다.
    """
    from pipeline import history as _hist
    subj = (single, display)
    prev_str = [(t[0] if isinstance(t, (tuple, list)) else t) for t in prev_titles]
    # 후보 검사용: 기존 prev + (bad_title, 이번 주제) 를 튜플로
    check_prev = list(prev_titles) + [(bad_title, single)]
    if OPENAI_API_KEY:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        banned = "\n".join("- " + t for t in prev_str[:10])
        for _ in range(tries):
            try:
                r = client.chat.completions.create(
                    model=SCRIPT_MODEL,
                    messages=[{"role": "user", "content": f"""Write ONE YouTube Shorts title about {display} ({single}).

It must NOT reuse the sentence structure of any of these recent titles (only the subject was swapped in them — that is exactly what to avoid):
{banned}
Also rejected for reusing a template: "{bad_title}"

Rules: max 70 characters, include "{single}" or "{display}", create a curiosity gap, no emoji, do not start with "You Won't Believe" or "Wait Until You See" or "Did You Know". Use a sentence structure that none of the titles above use.

Output STRICT JSON: {{"title": "..."}}"""}],
                    response_format={"type": "json_object"},
                    temperature=1.0,
                    max_tokens=120,
                )
                t = str(json.loads(r.choices[0].message.content).get("title", "")).strip()[:95]
                if t and not _starts_with_banned_opener(t) and not _hist.title_template_repeats(t, check_prev, subjects=subj):
                    return t
            except Exception as e:
                log.warning(f"제목 재생성 실패: {e}")
                break
    # 결정적 폴백 — 구조가 서로 다른 4종. 최근 지문과 안 겹치는 첫 번째 채택.
    for cand in (f"{display}: The Part Nobody Mentions",
                 f"What {display} Actually Do When No One Looks",
                 f"The {single.title()} Fact That Changed My Mind",
                 f"Nobody Talks About This {single.title()} Habit"):
        if not _starts_with_banned_opener(cand) and not _hist.title_template_repeats(cand, check_prev, subjects=subj):
            return cand[:95]
    return f"{display}: The Part Nobody Mentions"[:95]


def fetch_source_for(display: str, single: str, category: str = "") -> str:
    """토픽의 위키 SOURCE 확보. 실패 시 "" (호출 측이 재픽 판단)."""
    context = _DOMAIN_CONTEXT.get(_content_domain(), "")
    try:
        from pipeline import grounding
        return grounding.fetch_source(display, single, context=context, category=category)
    except Exception as e:
        log.warning(f"grounding fetch 예외: {e}")
        return ""


def reground_facts(meta: dict, source_text: str) -> dict:
    """SOURCE를 근거로 facts/hook을 다시 쓴다 (생성 → 검증 사이의 새 단계).

    기존 파이프라인은 GPT가 근거 없이 '충격적인 숫자'를 먼저 창작하고 나중에 팩트체크로
    걸러내는 구조라, 팩트체커가 놓치면 환각 수치가 그대로 발행됐다. 여기서 SOURCE를 앞에
    두고 "SOURCE에서 인용 가능한 사실만" 쓰게 해 창작 여지를 없앤다.

    SOURCE가 없거나 호출 실패면 meta 원본 유지(발행 계속) — 뒤의 팩트체크가 여전히 방어한다.
    """
    src = (source_text or "").strip()
    if not OPENAI_API_KEY or not src:
        return meta
    facts = [f for f in (meta.get("facts") or []) if str(f).strip()]
    if not facts:
        return meta
    display, single = meta.get("display", ""), meta.get("single", "")
    n = len(facts)
    draft = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
    prompt = f"""You are writing the script for a 60-second YouTube Shorts about {display} ({single}).

SOURCE TEXT (Wikipedia — the ONLY place you may take facts and numbers from):
{src}

Below is a DRAFT written WITHOUT the source. It may contain invented numbers.
DRAFT:
{draft}

Rewrite it into {n} final facts obeying ALL of these:
- Every fact must be supported by SOURCE. **Every number, measurement, count, age, speed and percentage MUST appear in SOURCE.** If the draft has a number that is not in SOURCE, either replace it with the correct value from SOURCE or remove the number and state the true qualitative fact.
- Do NOT invent superlatives ("the largest", "the only", "the smartest") or exclusivity claims ("only found in X", "the sole species") unless SOURCE states them in those words. If SOURCE hedges ("mostly", "predominantly"), you must hedge too.
- Keep the facts genuinely interesting: pick the most surprising things SOURCE actually says. Prefer a true surprising fact over a fabricated shocking one.
- **Each fact MUST be 28-33 words. Count the words before you answer.** If a SOURCE-backed fact is short, expand it with more true detail FROM SOURCE (context, comparison, consequence) — never pad with invented information.
- All numeric claims across the {n} facts MUST agree with each other and with SOURCE.
- For counts that change with new discoveries, use "more than N" / "at least N" with SOURCE's value.
- No hype phrases ("will blow your mind", "scientists were shocked", "more interesting than people think").
- hook: 12 words or fewer, a concrete SOURCE-backed number or comparison, never starts with "Did you know", and subject to the same no-superlative / no-exclusivity rule.

Output STRICT JSON: {{"hook": "...", "facts": [{', '.join(['"fact (28-33 words)"'] * n)}]}}"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        def _call(extra: str = ""):
            r = client.chat.completions.create(
                model=SCRIPT_MODEL,
                messages=[{"role": "user", "content": prompt + extra}],
                response_format={"type": "json_object"},
                temperature=0.4,  # 근거 재작성이라 창작보다 충실도 우선
                max_tokens=1200,
            )
            d = json.loads(r.choices[0].message.content)
            return d, [str(f).strip() for f in (d.get("facts") or []) if str(f).strip()]

        d, nf = _call()
        # 길이 보정: reground가 SOURCE에 충실해지면서 짧아지는 경향이 있다(실측 25~26단어).
        # 짧으면 TTS가 줄어 영상이 50초 밑으로 떨어지므로 SOURCE 범위 안에서 1회 확장 요청.
        if len(nf) == n and any(len(f.split()) < 27 for f in nf):
            short = [i + 1 for i, f in enumerate(nf) if len(f.split()) < 27]
            log.info(f"reground: fact {short} 단어수 미달 → SOURCE 범위 내 확장 재요청")
            try:
                d2, nf2 = _call(
                    "\n\nYour previous answer had facts shorter than 28 words: "
                    f"{', '.join('fact' + str(i) for i in short)}. "
                    "Rewrite ALL facts so each is 28-33 words, adding only true detail found in SOURCE.")
                if len(nf2) == n and sum(len(f.split()) for f in nf2) > sum(len(f.split()) for f in nf):
                    d, nf = d2, nf2
            except Exception as e:
                log.warning(f"reground 확장 재요청 실패 — 1차 결과 사용: {e}")
        if len(nf) == n:
            meta["facts"] = nf
            if d.get("hook"):
                meta["intro"] = str(d["hook"]).strip()
            log.info(f"reground: SOURCE 기반으로 {n} facts 재작성 완료 "
                     f"(단어수 {[len(f.split()) for f in nf]})")
        else:
            log.warning(f"reground: facts 개수 불일치({len(nf)}/{n}) → 원본 유지")
    except Exception as e:
        log.warning(f"reground 실패 — 원본 유지: {e}")
    return meta


def _enforce_global_fact_novelty(meta: dict, prev_facts: list, source_text: str) -> dict:
    """최종 facts를 과거 전역 facts와 코드로 대조 → 겹치면 SOURCE로 그 fact만 재작성.

    프롬프트에 "반복하지 마라"만 넣던 것을 실제 검사로 승격(codex 지적). 재작성이 또
    겹치거나 SOURCE/키가 없으면 원본 유지(발행 0 방지 — 겹치는 fact가 나가는 게
    발행 스킵보다 낫다). 재작성 성공분만 교체한다.
    """
    if not prev_facts:
        return meta
    from pipeline import history as _hist
    facts = [f for f in (meta.get("facts") or []) if str(f).strip()]
    if not facts:
        return meta
    dup_idx = _hist.facts_repeat_globally(facts, prev_facts)
    if not dup_idx:
        return meta
    log.warning(f"전역 fact 중복 감지: 인덱스 {dup_idx} → SOURCE로 재작성 시도")
    src = (source_text or "").strip()
    if not OPENAI_API_KEY or not src:
        log.info("전역 fact 중복: SOURCE/키 없음 → 원본 유지(발행 우선)")
        return meta
    display, single = meta.get("display", ""), meta.get("single", "")
    avoid = "\n".join("- " + f for f in prev_facts[:20])
    keep = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts) if i not in dup_idx)
    redo = "\n".join(f"{i+1}. {facts[i]}" for i in dup_idx)
    prompt = f"""Some facts in a {display} ({single}) script repeat facts already used on this channel.

SOURCE TEXT (Wikipedia — take all facts/numbers ONLY from here):
{src}

ALREADY-USED facts on the channel — the rewrites MUST NOT overlap with any of these:
{avoid}

FACTS TO KEEP (do not change, for context):
{keep or '(none)'}

REWRITE ONLY these facts to cover DIFFERENT true aspects from SOURCE, not overlapping the used facts above:
{redo}

Rules: each rewrite 28-33 words, supported by SOURCE, every number must appear in SOURCE, vary openers, no hype.
Output STRICT JSON with the SAME 1-based indices you were asked to rewrite:
{{"rewrites": {{{', '.join(f'"{i+1}": "..."' for i in dup_idx)}}}}}"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        r = client.chat.completions.create(
            model=SCRIPT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.5,
            max_tokens=900,
        )
        rw = (json.loads(r.choices[0].message.content) or {}).get("rewrites") or {}
        new_facts = list(facts)
        replaced = 0
        for i in dup_idx:
            cand = str(rw.get(str(i + 1), "")).strip()
            # 재작성이 여전히 겹치면 버린다(원본 유지). 새로 지어낸 중복은 넣지 않는다.
            if cand and not _hist.facts_too_similar([cand], prev_facts):
                new_facts[i] = cand
                replaced += 1
        if replaced:
            meta["facts"] = new_facts
            log.info(f"전역 fact 중복 재작성: {replaced}/{len(dup_idx)}건 교체")
        else:
            log.info("전역 fact 중복 재작성 실패(여전히 겹침) → 원본 유지")
    except Exception as e:
        log.warning(f"전역 fact 중복 재작성 예외 — 원본 유지: {e}")
    return meta


def _apply_grounded_factcheck(meta: dict, source_text: str = None) -> dict:
    """SOURCE(위키, 도메인 맥락+category) 확보 → 적대적 팩트체크 → meta.intro/facts 갱신.
    hook(intro)은 ≤12단어 코드 강제. fact_keywords 확정은 파이프라인 최종 _finalize_fact_keywords에서(F).
    source_text가 주어지면 재fetch하지 않는다(pick 단계에서 이미 확보). 실패 시 원본 유지."""
    if not OPENAI_API_KEY:
        return meta
    display = meta.get("display", "")
    single = meta.get("single", "")
    src = source_text if source_text is not None else fetch_source_for(
        display, single, meta.get("category", ""))
    if not src:
        log.info("grounding: on-topic SOURCE 없음 → 보수 모드(established consensus) 팩트체크 진행")
    hook, facts = adversarial_factcheck(display, single, meta.get("intro", ""),
                                        meta.get("facts", []), src)
    hook = _enforce_hook_length(display, single, hook)  # (5) ≤12단어 강제
    if hook:
        meta["intro"] = hook
    if facts:
        meta["facts"] = facts
    return meta


def _differentiate_content(meta, prev_entries, avoid_formats=None):
    """재등장 topic: 과거 facts/angle 주입해 완전히 새로운 내용으로 재생성 + 겹침 1회 재검사."""
    if not OPENAI_API_KEY or not prev_entries:
        return meta
    from pipeline import history as _hist
    display = meta.get("display", "")
    single = meta.get("single", "")
    past_facts, past_angles = [], []
    for e in prev_entries:
        past_facts.extend([f for f in (e.get("facts") or []) if f])
        if e.get("angle"):
            past_angles.append(e["angle"])
    past_facts = past_facts[-16:]
    if not past_facts:
        return meta
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    def _regen():
        avoid_fmt = ", ".join(avoid_formats or []) or "none"
        n = len(meta.get("facts") or []) or 4
        prompt = f"""This topic ({display}) was covered before on the channel. Write a COMPLETELY NEW 60-second script that does NOT repeat any previously used fact or angle.

PREVIOUSLY COVERED — DO NOT REPEAT ANY OF THESE (use entirely different facts and a different angle):
{chr(10).join('- ' + f for f in past_facts)}
Already-used angles for this topic: {', '.join(past_angles) or 'none'}
Recently used formats to avoid: {avoid_fmt}

REQUIREMENTS:
- {n} NEW facts, each 28-33 words, each about a DIFFERENT aspect than the previously covered ones above. Across the facts include at least two concrete numbers and at least one vivid comparison.
- Hook (intro narration): 12 words or fewer, a concrete number or shocking comparison, NEVER start with "Did you know".
- Do NOT use empty hype ("will blow your mind", "scientists were shocked", "more interesting than people think").
- Conservative phrasing for volatile counts: "more than N" / "at least N".
- fact_keywords: {n} background search keywords, one per fact IN ORDER, each containing "{single}".
- angle: short label for the fresh angle (different from already-used ones). format: one of {FORMATS}.
- title (<=70 chars, includes "{single}", curiosity gap, no emoji), description (2 sentences + 3-5 hashtags), comment (reply-magnet, 8-20 words, about {single} ONLY).

Output STRICT JSON: {{"intro":"...","facts":[{', '.join(['"..."'] * n)}],"fact_keywords":[{', '.join(['"..."'] * n)}],"angle":"...","format":"...","title_en":"...","description_en":"...","comment":"..."}}"""
        resp = client.chat.completions.create(
            model=SCRIPT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.95,
            max_tokens=1200,
        )
        return json.loads(resp.choices[0].message.content)

    try:
        d = _regen()
        new_facts = [str(f).strip() for f in (d.get("facts") or []) if str(f).strip()]
        if len(new_facts) >= 3 and _hist.facts_too_similar(new_facts, past_facts):
            log.info("재등장 차별화: 1차 결과 과거와 겹침 → 1회 재생성")
            d2 = _regen()
            nf2 = [str(f).strip() for f in (d2.get("facts") or []) if str(f).strip()]
            if len(nf2) >= 3:
                d, new_facts = d2, nf2
        if len(new_facts) >= 3:
            meta["facts"] = new_facts
            if d.get("intro"):
                meta["intro"] = str(d["intro"]).strip()
            if d.get("title_en"):
                meta["title_en"] = str(d["title_en"]).strip()[:95]
            if d.get("comment"):
                meta["comment"] = str(d["comment"]).strip()[:280]
            if d.get("description_en"):
                meta["description_en"] = str(d["description_en"]).strip()[:4900]
            if d.get("angle"):
                meta["angle"] = str(d["angle"]).strip()
            if d.get("format"):
                meta["format"] = str(d["format"]).strip()
            meta["fact_keywords"] = _validate_fact_keywords(
                d.get("fact_keywords"), single, meta.get("pexels_keywords") or [], n=len(new_facts))
            meta["_kw_facts"] = list(new_facts)  # (F) 키워드 도출 시점 facts 스냅샷
            log.info(f"재등장 차별화 완료: {len(new_facts)} facts 새로 생성 (angle={meta.get('angle')})")
    except Exception as e:
        log.warning(f"재등장 차별화 실패 — 원본 유지: {e}")
    return meta


def _topic_tokens(text: str) -> set:
    """소문자 단어 토큰 집합 (영문만)."""
    import re as _re
    return {w for w in _re.findall(r"[a-z]+", text.lower()) if len(w) >= 3}


def verify_consistency(meta: dict) -> dict:
    """GPT 1회로 facts/comment 내부 일관성 검증/수정.

    Catches:
    - facts 내 숫자 모순 (Saturn 위성 82 vs 79 — 2026-05-06 사용자 신고)
    - comment가 다른 토픽 (Saturn 영상에 Jupiter 댓글 — 2026-05-06 사용자 신고)
    - title/description/comment 토픽 불일치

    실패 시 원본 meta 유지 (검증 실패해도 발행 자체는 진행).
    """
    if not OPENAI_API_KEY:
        return meta

    display = meta.get("display", "").strip()
    single = meta.get("single", "").strip()
    facts = list(meta.get("facts", []) or [])
    comment = meta.get("comment", "").strip()
    title = meta.get("title_en", "").strip()

    if not facts or not display:
        return meta

    # 1) Python 사전 체크: comment에 다른 우주 토픽 단어가 본 토픽 단어 없이 등장 → off-topic 의심
    own_tokens = _topic_tokens(f"{display} {single}")
    comment_tokens = _topic_tokens(comment)
    other_in_comment = (comment_tokens & _OTHER_TOPIC_WORDS) - own_tokens
    has_own_in_comment = bool(comment_tokens & own_tokens) or display.lower() in comment.lower() or single.lower() in comment.lower()
    suspect_offtopic_comment = bool(other_in_comment) or not has_own_in_comment

    review_prompt = f"""You are a script consistency editor for YouTube Shorts about {display} ({single}).

Review this script for three CRITICAL issues:
1. NUMERIC CONTRADICTIONS between facts (e.g. fact 1 says "82 moons" + fact 2 says "79 moons" for the same metric)
2. FACTUALLY FALSE claims — any statement that contradicts the 2024 scientific consensus (wrong figure, debunked myth, invented detail)
3. COMMENT OFF-TOPIC (comment mentions a planet/object that is NOT {display})

Title: {title}
Facts:
{chr(10).join(f"{i+1}. {f}" for i, f in enumerate(facts))}
Comment: {comment}

INSTRUCTIONS:
- If 2+ facts cite different numbers for the SAME metric (e.g. moons of {display}, age in years, distance in km, percentage),
  pick ONE accurate value (use latest 2024 consensus) and rewrite ALL contradicting facts to use THAT value.
  Keep facts at 28-33 words. Only modify the contradicting numeric claim, keep the rest of the sentence.
- If any fact is factually FALSE per 2024 scientific consensus, correct it to an accurate, equally
  surprising fact (keep 28-33 words). Do NOT invent numbers you are not confident about.
- If comment mentions any planet/object other than {display}, rewrite the comment to be about {display} ONLY,
  in 8-20 words, using one of: hot take / forced choice within {display} / what-if / personal challenge / confession bait / fill-in-the-blank.
- If everything is fine, return original facts and comment unchanged.

Output STRICT JSON: {{"facts": ["...", "...", "...", "..."], "comment": "...", "fixed": ["short list of issues fixed, empty if none"]}}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=FACTCHECK_MODEL,
            messages=[{"role": "user", "content": review_prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=900,
        )
        d = json.loads(resp.choices[0].message.content)
        new_facts = d.get("facts") or []
        new_comment = (d.get("comment") or "").strip()
        fixed = d.get("fixed") or []
        if isinstance(new_facts, list) and len(new_facts) == len(facts):
            new_facts = [str(f).strip() for f in new_facts if str(f).strip()]
            if len(new_facts) == len(facts):
                meta["facts"] = new_facts
        if new_comment and 5 < len(new_comment) <= 280:
            meta["comment"] = new_comment
        if fixed:
            log.info(f"consistency check fixed {len(fixed)}건: {fixed}")
        elif suspect_offtopic_comment:
            log.warning(f"comment 사전 의심됐지만 GPT는 수정 없다고 판단 (other_in_comment={other_in_comment}, has_own={has_own_in_comment})")
    except Exception as e:
        log.warning(f"consistency check 실패 — 원본 유지: {e}")

    return meta


def pick_random_animal(exclude_keys: list = None, history: dict = None,
                       avoid_formats: list = None) -> dict:
    """GPT가 동적으로 우주 토픽 1개 + 메타데이터 일괄 생성.

    풀: well-known 50% + 덜 알려진 우주 사실 50%.
    실패 시 사전에서 fallback.

    history: content_history dict (재등장 topic 차별화 + per-topic angle 회피용).
    avoid_formats: 전역 직전 N편 format (연속 방지).

    반환: {animal_key, display, single, category, pexels_keywords, fact_keywords,
           title_en, description_en, hook, intro, facts, outro, tags, comment, angle, format}
    """
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY 없음 — fallback 사전 사용")
        key = pick_animal(exclude=exclude_keys or [])
        return _legacy_meta(key)

    exclude_str = ", ".join(exclude_keys[-45:]) if exclude_keys else "none"
    avoid_fmt_str = ", ".join(avoid_formats or []) or "none"

    # 전역 중복 차단 (2026-07-10). 기존엔 '같은 topic 재등장' 시에만 과거 facts를 봤기 때문에
    # topic이 달라도 같은 사실을 반복했고, 제목은 대상만 갈아끼운 양산 템플릿이 반복됐다.
    prev_facts, prev_titles = [], []
    if history:
        try:
            from pipeline import history as _hist
            prev_facts = _hist.recent_facts(history, 120, limit=30)
            prev_titles = _hist.recent_titled_topics(history, 12)  # (title, topic_key)
        except Exception as e:
            log.warning(f"전역 이력 조회 실패(무시): {e}")
    prev_title_strs = [t[0] for t in prev_titles]
    facts_block = ("\n\nALREADY-COVERED FACTS on this channel — do NOT repeat any of these, "
                   "even for a different topic:\n"
                   + "\n".join("- " + f for f in prev_facts)) if prev_facts else ""
    titles_block = ("\n\nRECENT TITLES on this channel. Your title MUST NOT reuse these sentence "
                    "templates with a different subject swapped in (YouTube demonetizes "
                    "template-mass-produced content). Invent a genuinely different structure:\n"
                    + "\n".join("- " + t for t in prev_title_strs)) if prev_title_strs else ""

    prompt = f"""You are a viral space scriptwriter for YouTube Shorts channel "Space Trivia 60s".
Pick ONE surprising space topic RIGHT NOW and write a 55-second script (1-minute Shorts).

CRITICAL CONSTRAINT: pexels_keywords MUST find PORTRAIT video on Pexels. So you must pick topics that are
WELL-PHOTOGRAPHED on stock footage sites. Avoid extremely obscure phenomena with no usable visuals.

GOOD SPACE TOPIC POOL (these have abundant Pexels portrait video — planet/galaxy/space/cosmos/stars).
The channel has over-covered the planets and the Sun. **Strongly prefer topics from the lower groups
you have not used yet** — variety is now more valuable than familiarity:
- Planets: Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto (dwarf planet)
- Moons: Earth's Moon, Europa, Titan, Io, Enceladus, Phobos, Deimos, Ganymede, Callisto, Triton, Miranda, Iapetus, Mimas, Charon
- Stars/Phenomena: the Sun, supernova, neutron star, pulsar, black hole, white dwarf, red giant, magnetar, brown dwarf, binary star, red dwarf, blue supergiant, solar flare, solar wind, aurora, sunspot, stellar nursery, star cluster, globular cluster
- Galaxies: Milky Way, Andromeda, spiral galaxy, elliptical galaxy, irregular galaxy, quasar, active galactic nucleus, galaxy cluster, Magellanic Clouds, Sagittarius A*
- Cosmos: Big Bang, dark matter, dark energy, cosmic microwave background, gravitational waves, cosmic rays, redshift, event horizon, wormhole (theoretical), time dilation, Fermi paradox, Drake equation, Great Attractor, cosmic web, vacuum of space
- Objects: asteroids, comets, meteors, meteorites, nebulae (Orion, Eagle, Crab, Carina, Helix, Ring), exoplanets, hot Jupiters, rogue planets, Kuiper Belt, Oort Cloud, asteroid belt, Ceres, Vesta, Halley's Comet, Trappist-1, Proxima Centauri, Alpha Centauri, Betelgeuse, Sirius, Polaris, Vega, Kepler-186f
- Spacecraft/Missions: ISS, Hubble, James Webb, Voyager 1, Voyager 2, Apollo 11, Apollo 13, SpaceX Starship, Mars rovers (Curiosity, Perseverance), Cassini, New Horizons, Juno, Parker Solar Probe, Chandrayaan, Artemis, Sputnik, Space Shuttle
- Constellations: Orion, Big Dipper, Cassiopeia, Southern Cross, Pleiades, Ursa Major, Scorpius, Cygnus
- Human spaceflight: spacesuits, zero gravity effects on the body, space debris, astronaut training, life support, Moon landing sites

AVOID picking these topics (recently used): {exclude_str}{facts_block}{titles_block}

REQUIREMENTS:
- The fact MUST be SURPRISING / counter-intuitive. **Use a specific number ONLY when you are certain it is real** — a true, verifiable fact beats an invented mind-blowing number. Every number you write will be checked against Wikipedia and rewritten if it is not there.
- **Each fact MUST be 28-33 words. NOT LESS THAN 28. NOT MORE THAN 33.** Count the words. Three short facts = REJECT and rewrite.
- Each fact = ONE rich sentence packed with a specific number + comparison/context + surprising consequence. Spoken at TTS rate +12% this gives ~12s per fact.
- Provide EXACTLY 4 facts (not 3). Total spoken length target: intro ~3s + 4 facts × ~10s + outro ~3s ≈ 46s (실TTS는 보통 +5~10s 늘어남, 영상 60s 안에 안전).
- **INTERNAL CONSISTENCY (CRITICAL — viewers WILL catch contradictions and call you out)**:
  - All numeric claims across the 4 facts MUST agree. If fact 1 cites a moon count, fact 2/3/4 must NOT cite a different number for the same metric. Pick ONE number per metric and use it consistently.
  - If you cite "X% of [thing]" or "Y times [thing]", any other fact about the SAME metric MUST use the SAME value.
  - Use current scientific consensus values. Do not mix old and new numbers. Never hardcode a moon/exoplanet count from memory — those change with new discoveries and are verified against the source text later.
  - title and description MUST be about the same topic as the facts (no Jupiter title on a Saturn script).
- **Hook (intro narration) — CRITICAL**: 12 words or FEWER. Lead with a specific number or a shocking comparison. NEVER start with "Did you know". Examples: "A day on Venus lasts longer than its year." / "Light from the Sun takes eight minutes to reach us."
- **FACT QUALITY**: across the 4 facts, include AT LEAST two concrete numbers/measurements AND at least one vivid comparison ("wide enough to swallow 1,300 Earths", "hotter than the Sun's surface").
- **BANNED PHRASES (never use in intro or facts)**: "more interesting than people think", "scientists were shocked", "will blow your mind", "hides phenomena that defy", "hides cosmic secrets". Write concrete, specific sentences instead.
- **CONSERVATIVE on volatile numbers**: for counts that change with new discoveries (moon counts, confirmed exoplanets), write "more than N confirmed" or "at least N" with current 2024 consensus values (e.g. Saturn has more than 140 confirmed moons). Do NOT state disputed figures as settled fact.
- Outro CTA MUST be one of: "Follow for more space facts." / "Follow for daily cosmic facts." / "Comment your favorite space mystery."
- Each fact MUST start differently (vary openers).
- **angle**: choose ONE angle for this video from {ANGLES}. Build all 4 facts around that single angle.
- **format**: choose ONE from {FORMATS}. AVOID these recently-used formats: {avoid_fmt_str}.
- pexels_keywords: 5 keywords. **EVERY keyword MUST be space/cosmos related** (e.g., for Saturn: "saturn rings", "saturn telescope", "planet rings space", "gas giant", "ringed planet"). NEVER use generic earthly terms.
- **fact_keywords**: EXACTLY 4 background-video search keywords, ONE per fact IN ORDER, each containing the topic name and describing that fact's visual (e.g. fact about the rings -> "saturn rings"; fact about storms -> "saturn atmosphere clouds").
- category: one of {sorted(_VALID_CATEGORIES)}.
- **Title (CRITICAL — single most important CTR factor)**: max 70 chars, MUST include the topic name + a CURIOSITY GAP hook. NEVER use "compilation", "best of", "top 10", "Amazing Facts About X" (too generic, kills CTR).
  Use ONE of these viral patterns (vary across topics):
  (a) Shocking number: "Saturn's Rings Will Vanish in 100 Million Years"
  (b) Wait what: "Wait, One of Jupiter's Moons Hides an Ocean"
  (c) Counterintuitive: "The Mars Detail Most People Get Wrong"
  (d) Conditional twist: "If the Sun Vanished, We'd Have 8 Minutes To Live"
  (e) The truth: "The Truth About Why Black Holes Aren't Holes"
  (f) Stop scrolling: "Stop Scrolling — A Star Just Exploded Last Night"
  (g) Question hook: "How Loud Is the Sun? You'd Be Shocked"
  Keep it punchy. Lowercase or Title Case both fine. NO emoji in title. Hype is allowed in the title, but it must NOT state anything factually false.
  Vary the pattern from the last few videos (do not reuse the same template repeatedly).
- Description: 2 sentences + 3-5 hashtags (no emoji).
- Hook (on-screen overlay text): 2-4 words like "Saturn • Trivia" or "Black Hole Facts".
- **comment** (auto-pinned reply-magnet comment under the video): ONE line, 8-20 words.
  GOAL: provoke viewers to actually reply.
  **CRITICAL — TOPIC LOCK**: The comment MUST be about THE EXACT TOPIC YOU JUST PICKED.
  If you picked Saturn → comment is about Saturn ONLY. Do NOT mention Jupiter, Mars, Earth, the Moon,
  or any OTHER cosmic object. Do NOT contrast against another planet (no "Saturn or Jupiter?"
  type comments — that drives viewers to the other topic, not yours). Pick ONE reply-magnet pattern below
  and write a fresh line that ONLY references the picked topic. The patterns below show STYLE — replace
  the example topic with whatever you actually picked.
  (a) Hot take / fight me — STYLE: "Hot take: [topic] is [provocative claim]. Fight me 🔥"
  (b) Forced choice WITHIN the same topic — STYLE: "Would you rather visit [topic feature A] or [topic feature B]?"
  (c) Reincarnation / what-if — STYLE: "If I could spend one day on/at/with [topic], I'd [action]. You?"
  (d) Personal challenge — STYLE: "Bet you can't name 3 things about [topic] without Googling. Try it."
  (e) Confession bait — STYLE: "Confession: [topic] still blows my mind every time. Anyone else?"
  (f) Fill-in-the-blank — STYLE: "If [topic] were a [role], they'd be the one who ___"
  Use 0-1 emoji max. **NEVER** these dead patterns: "Which fact surprised you most?", "Tag a friend who...", "Did you know any of these?", "Like if you learned something" — those get zero replies.

Output STRICT JSON only:
{{
  "animal_key": "snake_case_id (e.g. saturn, black_hole)",
  "display": "Saturn",
  "single": "Saturn",
  "category": "planet",
  "pexels_keywords": ["saturn rings", "planet rings space", "gas giant", "saturn telescope", "ringed planet"],
  "fact_keywords": ["saturn rings", "saturn atmosphere", "saturn moons", "saturn telescope"],
  "angle": "one_of_the_angle_labels",
  "format": "one_of_the_format_labels",
  "title_en": "...",
  "description_en": "... #shorts #space #astronomy #saturn",
  "hook": "Saturn • Trivia",
  "intro": "Saturn is so light it would float in water.",
  "facts": ["fact1 (28-33 words)", "fact2 (28-33 words)", "fact3 (28-33 words)", "fact4 (28-33 words)"],
  "outro": "Follow for more space facts.",
  "comment": "Saturn is the only planet less dense than water — would it really float?",
  "tags": ["shorts","space","astronomy","cosmos","trivia","did you know","saturn","planet","nasa"]
}}"""

    # 2026-05-07 fix (동물봇 9연속 pangolin 사고 동기화): GPT가 prompt example/recent topic에
    # bias되어 같은 토픽 반복 → 코드 reject + retry로 강제 다양화.
    exclude_set = {k.lower().strip() for k in (exclude_keys or [])}

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    d = None
    last_pick = None
    source_text = ""
    # 재시도 시 거부 목록을 누적한다. 직전 1건만 알려주면 모델이 같은 인기 토픽으로 계속
    # 돌아온다 (실측: 4회 연속 saturn 픽). 누적 + 미사용 후보 양성 제시로 밀어낸다.
    rejected = []
    for attempt in range(4):
        try_prompt = prompt
        if attempt > 0 and rejected:
            unused = [k for k in ANIMALS.keys() if k.lower() not in exclude_set][:12]
            hint = (f"\nPick one of these unused topics instead: {', '.join(unused)}.\n"
                    if unused else "\n")
            try_prompt = (
                f"STOP. You already picked these and they are ALL REJECTED (already used on the "
                f"channel): {', '.join(rejected)}.\n"
                f"Do NOT pick any of them again. Pick a COMPLETELY DIFFERENT topic that is not in "
                f"the exclude list below.{hint}\n"
                + try_prompt
            )
        try:
            resp = client.chat.completions.create(
                model=SCRIPT_MODEL,
                messages=[{"role": "user", "content": try_prompt}],
                response_format={"type": "json_object"},
                temperature=1.0,
                max_tokens=1200,
            )
            cand = json.loads(resp.choices[0].message.content)
        except Exception as e:
            log.warning(f"GPT 우주 토픽 픽 실패 → legacy fallback: {e}")
            key = pick_animal(exclude=exclude_keys or [])
            return _legacy_meta(key)

        cand_key = (cand.get("animal_key") or "").strip().lower().replace(" ", "_")
        cand_single = (cand.get("single") or "").strip().lower()
        cand_display = (cand.get("display") or "").strip().lower()
        is_excluded = (
            cand_key in exclude_set
            or cand_single in exclude_set
            or cand_display in exclude_set
            or any(e and (e in cand_key or e in cand_single) for e in exclude_set if e)
        )
        if is_excluded:
            last_pick = cand_key or cand_single
            if last_pick and last_pick not in rejected:
                rejected.append(last_pick)
            log.warning(f"[picker try {attempt+1}] GPT가 exclude된 '{last_pick}' 픽 → 재요청")
            continue
        # 근거 선행: 위키 SOURCE를 못 구하는 토픽은 사실 검증이 불가능하다 → 다른 토픽으로 재픽.
        # 마지막 시도에서는 SOURCE 없이도 진행해 발행 0을 피한다(보수 모드 팩트체크가 방어).
        src = fetch_source_for(cand.get("display", ""), cand.get("single", ""),
                               cand.get("category", ""))
        if not src and attempt < 3:
            last_pick = cand_key or cand_single
            if last_pick and last_pick not in rejected:
                rejected.append(last_pick)
            log.warning(f"[picker try {attempt+1}] '{last_pick}' 위키 SOURCE 확보 실패 → 다른 토픽 재픽")
            continue
        source_text = src
        d = cand
        break

    if d is None:
        log.warning(f"GPT가 4회 모두 exclude된 토픽만 픽 → legacy fallback + expand")
        key = pick_animal(exclude=exclude_keys or [])
        legacy = _legacy_meta(key)
        # 2026-05-08 fix: 기존엔 placeholder facts ("Saturn hides phenomena that defy...")
        # 그대로 발행 직전까지 갔음. expand로 진짜 28-33단어 facts 강제 생성.
        try:
            rich = expand_legacy_to_rich(legacy)
            legacy.update(rich)
            log.info(f"picker fallback → expand 완료 ({len(legacy['facts'])} facts)")
        except Exception as e:
            log.warning(f"picker fallback expand 실패 → placeholder facts 유지: {e}")
        # legacy 경로는 정상 경로(아래 959~)의 최종 방어를 건너뛴다.
        # 2026-07-11 사고: 이 경로로 "You Won't Believe What ... Does at Night" 진부
        # 양산 제목이 무검사 발행됨. 정상 경로와 동일한 최종 검사를 여기서도 적용한다.
        try:
            _lsrc = fetch_source_for(legacy.get("display", ""), legacy.get("single", ""),
                                     legacy.get("category", ""))
            _res = _enforce_global_fact_novelty(legacy, prev_facts, _lsrc)
            if isinstance(_res, dict) and _res.get("facts"):
                legacy = _res
        except Exception as e:
            log.warning(f"legacy 전역 fact 중복 검사 실패(무시): {e}")
        try:
            if _title_needs_fix(legacy.get("title_en", ""), prev_titles,
                                (legacy.get("single", ""), legacy.get("display", ""))):
                legacy["title_en"] = _retitle_unique(legacy.get("display", ""),
                                                     legacy.get("single", ""),
                                                     legacy.get("title_en", ""), prev_titles)
                log.warning(f"제목 양산/금지opener(legacy) 차단 → {legacy['title_en']!r}")
        except Exception as e:
            log.warning(f"제목 검사(legacy) 실패(무시): {e}")
        return legacy

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
    # 템플릿 양산 차단 (2026-07-10): 최근 제목과 앞 3단어 지문이 같으면 대상만 바꾼 복제본이다.
    # 실제 사고: "You Won't Believe What Foxes/Comets/Jupiter/the Big Bang Does at Night".
    # 유튜브 2025-07 inauthentic content 정책의 수익화 리스크라 코드로 강제한다.
    try:
        if _title_needs_fix(title_en, prev_titles, (single, display)):
            alt = _retitle_unique(display, single, title_en, prev_titles)
            log.warning(f"제목 양산/금지opener 차단: {title_en!r} → {alt!r}")
            title_en = alt
    except Exception as e:
        log.warning(f"제목 검사 실패(무시): {e}")

    facts = d.get("facts") or []
    if not isinstance(facts, list) or len(facts) < 3:
        facts = [_safe_fact(display, i) for i in range(4)]
    facts = [str(f).strip() for f in facts[:5] if str(f).strip()]
    if len(facts) < 3:
        facts = facts + [_safe_fact(display, i) for i in range(3 - len(facts))]
    if any(len(f.split()) < 26 or len(f.split()) > 33 for f in facts):
        log.warning(f"facts out of range ({[len(f.split()) for f in facts]}), rewriting to 28-33 words each...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            n = len(facts)
            joined = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
            expand_prompt = f"""Rewrite each fact to EXACTLY 28-33 words. Add a specific number, a comparison or context, and a surprising consequence. Keep them TRUE. Vary the sentence openers.

Original {n} facts about {display}:
{joined}

Output STRICT JSON: {{"facts": [{', '.join(['"fact (28-33 words)"'] * n)}]}}"""
            r2 = client.chat.completions.create(
                model=SCRIPT_MODEL,
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

    # angle / format 파싱 + 정규화 (로테이션용)
    angle = str(d.get("angle") or "").strip().lower().replace(" ", "_")
    fmt = str(d.get("format") or "").strip().lower().replace(" ", "_")
    avoid = {f.lower() for f in (avoid_formats or [])}
    # 유효하지 않거나 '최근 사용 format'이면 회피 목록 밖에서 강제 재선택
    # (GPT가 valid하지만 직전 format을 반복하면 연속 중복이 되므로 코드로 차단)
    if fmt not in FORMATS or fmt in avoid:
        choices = [f for f in FORMATS if f not in avoid] or FORMATS
        fmt = random.choice(choices)
    fact_kw = _validate_fact_keywords(d.get("fact_keywords"), single, pexels_kw, n=len(facts))

    meta = {
        "animal_key": animal_key,
        "display": display,
        "single": single,
        "category": category,
        "pexels_keywords": pexels_kw,
        "fact_keywords": fact_kw,
        "_kw_facts": list(facts),  # (F) 키워드 도출 시점 facts 스냅샷
        "angle": angle,
        "format": fmt,
        "title_en": title_en,
        "description_en": (d.get("description_en") or f"4 cosmic facts about {display}.")[:4900],
        "hook": (d.get("hook") or f"{display} • Trivia")[:40],
        "intro": (d.get("intro") or f"{display} behaves in ways that surprise most people.").strip(),
        "facts": facts,
        "outro": (d.get("outro") or "Follow for more space facts.").strip(),
        "comment": comment,
        "tags": d.get("tags") or ["shorts", "space", "astronomy", "cosmos", "trivia",
                                  "did you know", animal_key.replace("_", " "), single, "nasa"],
    }

    # 재등장 topic 차별화 (과거 이력 있으면 완전히 새로운 내용으로 재생성)
    prev = []
    if history:
        try:
            from pipeline import history as _hist
            prev = _hist.topic_entries(history, animal_key)
        except Exception:
            prev = []
    if prev:
        log.info(f"'{animal_key}' 과거 이력 {len(prev)}건 → 재등장 차별화 시도")
        meta = _differentiate_content(meta, prev, avoid_formats)

    # 근거 우선 재작성 → 적대적 팩트체크(허위 자막 차단) → 내부 일관성 검증.
    # SOURCE는 pick 단계에서 이미 확보했으므로 재fetch하지 않는다.
    meta = reground_facts(meta, source_text)
    meta = _apply_grounded_factcheck(meta, source_text)
    meta = verify_consistency(meta)
    # 전역 fact 중복 코드 강제 (codex 지적 2026-07-10): 프롬프트 주입만으론 GPT가 무시할 때
    # 못 막는다. 최종 facts를 과거 전역 facts와 실제로 대조해 겹치면 SOURCE로 1회 재작성한다.
    meta = _enforce_global_fact_novelty(meta, prev_facts, source_text)
    # (F) 모든 fact 변경 끝난 뒤 fact_keywords 최종 확정 (배경 fetch가 최종 facts와 정합)
    meta = _finalize_fact_keywords(meta, pexels_kw)
    # 제목 템플릿 최종 검사 — _differentiate_content가 title을 덮어쓰므로 여기서 한 번 더 본다.
    try:
        if _title_needs_fix(meta.get("title_en", ""), prev_titles, (single, display)):
            meta["title_en"] = _retitle_unique(display, single, meta["title_en"], prev_titles)
            log.warning(f"제목 양산/금지opener(최종) 차단 → {meta['title_en']!r}")
    except Exception as e:
        log.warning(f"제목 최종 검사 실패(무시): {e}")
    return meta


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
- 4 facts, each EXACTLY 28-33 words. Across the 4 facts include AT LEAST two concrete numbers AND at least one vivid comparison.
- Each fact opens DIFFERENTLY. NEVER use empty hype: "more interesting than people think", "scientists were shocked", "will blow your mind", "hides cosmic secrets".
- **CONSERVATIVE on volatile numbers**: for counts that change with new discoveries (moon counts, confirmed exoplanets), write "more than N confirmed" or "at least N" with current 2024 consensus values; do NOT state disputed figures as settled fact.
- **INTERNAL CONSISTENCY (CRITICAL)**: All numeric claims across the 4 facts MUST agree. If fact 1 says "{display} has more than 140 moons", fact 2/3/4 must NOT cite a different number. Use latest 2024 scientific consensus values. No contradictions between facts, title, and comment.
- Intro (hook narration): 12 words or FEWER, lead with a specific number or shocking comparison, NEVER start with "Did you know".
- Outro: 4-7 words CTA, one of: "Follow for more space facts." / "Follow for daily cosmic facts." / "Comment your favorite space mystery."
- Hook (overlay text): 2-4 words like "{display} • Trivia".
- **angle**: one of {ANGLES}. **format**: one of {FORMATS}.
- **fact_keywords**: 4 background-video search keywords, ONE per fact IN ORDER, each containing "{single}".
- **Title (CRITICAL CTR — pick ONE viral pattern, max 70 chars, must include "{single}" or "{display}", NO emoji)**:
  (a) Shocking number: "{display} Has ___ ?! Here's Why"
  (b) Wait what: "Wait, {display} ___ A Year"
  (c) Counterintuitive: "The {single} Detail Most People Get Wrong"
  (d) Conditional twist: "If {display} ___, We'd Be in Trouble"
  (e) Stop scrolling: "Stop Scrolling — {display} Just ___"
  (f) Question hook: "How Does {display} Survive ___?"
  NEVER use "Amazing Facts About", "Wild Facts About", "Facts You Didn't Know" — generic patterns kill CTR.
  Do NOT start the title with "You Won't Believe", "Wait Until You See", or "Did You Know" — these are overused template openers that hurt reach. Match the verb to the subject ("Comets DO / orbit", "A Comet DOES / orbits"). Hype ok, but no false claim.
- Description: 2 sentences + 3-5 hashtags (no emoji).
- **comment** (reply-magnet, 8-20 words): hot take / forced choice WITHIN {display} only / what-if / personal challenge / confession bait / fill-in-the-blank.
  **TOPIC LOCK**: comment MUST be about {display} ONLY. NEVER mention any OTHER cosmic object (no "Saturn or Jupiter?" type cross-topic comments). 0-1 emoji max. NEVER "Which fact surprised you most?" or "Tag a friend".

Output STRICT JSON: {{
  "title_en": "...",
  "description_en": "... #shorts #space #astronomy #{single.lower().replace(' ', '')}",
  "hook": "{display} • Trivia",
  "intro": "{display} behaves in a way that surprises most people.",
  "facts": ["fact1 (28-33 words)", "fact2", "fact3", "fact4"],
  "fact_keywords": ["{single} visual1", "{single} visual2", "{single} visual3", "{single} visual4"],
  "angle": "one_of_the_angle_labels",
  "format": "one_of_the_format_labels",
  "outro": "Follow for more space facts.",
  "comment": "..."
}}"""

    # Rate limit 429 회피: 최대 3회 retry + exponential backoff (1s → 5s → 15s)
    import time as _time
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    last_err = None
    d = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=SCRIPT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.9,
                max_tokens=1100,
            )
            d = json.loads(resp.choices[0].message.content)
            break
        except Exception as e:
            last_err = e
            es = str(e).lower()
            if "rate_limit" in es or "429" in es or "tpm" in es:
                wait = [5, 15, 30][attempt]
                log.warning(f"expand_legacy_to_rich rate limit → {wait}s 후 재시도 (attempt {attempt+1}/3)")
                _time.sleep(wait)
            else:
                raise
    if d is None:
        raise last_err or RuntimeError("expand_legacy_to_rich GPT 호출 3회 모두 실패")

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

    angle = str(d.get("angle") or "").strip().lower().replace(" ", "_")
    fmt = str(d.get("format") or "").strip().lower().replace(" ", "_")
    if fmt not in FORMATS:
        fmt = random.choice(FORMATS)
    fact_kw = _validate_fact_keywords(d.get("fact_keywords"), single,
                                      meta.get("pexels_keywords") or [], n=len(facts))

    rich = {
        "title_en": title_en,
        "description_en": (d.get("description_en") or f"4 cosmic facts about {display}.")[:4900],
        "intro": (d.get("intro") or f"{display} behaves in ways that surprise most people.").strip(),
        "facts": facts,
        "fact_keywords": fact_kw,
        "angle": angle,
        "format": fmt,
        "outro": (d.get("outro") or "Follow for more space facts.").strip(),
        "hook": (d.get("hook") or f"{display} • Trivia").strip()[:40],
        "comment": comment,
    }
    # 사실 근거(위키) + 적대적 팩트체크 → 내부 일관성 검증 → (F) fact_keywords 최종 확정
    review_input = {**rich, "display": display, "single": single, "category": meta.get("category", ""),
                    "_kw_facts": list(facts)}
    # fallback 경로도 SOURCE 기반 재작성을 태운다. 예전엔 이 경로만 reground를 건너뛰어
    # 훅이 "behaves in a way that surprises most people" 같은 공허한 문장으로 나갔다.
    _src = fetch_source_for(display, single, meta.get("category", ""))
    review_input = reground_facts(review_input, _src)
    review_input = _apply_grounded_factcheck(review_input, _src)
    review_input = verify_consistency(review_input)
    review_input = _finalize_fact_keywords(review_input, meta.get("pexels_keywords") or [])
    rich["intro"] = review_input.get("intro", rich["intro"])
    rich["facts"] = review_input.get("facts", rich["facts"])
    rich["fact_keywords"] = review_input.get("fact_keywords", rich["fact_keywords"])
    rich["comment"] = review_input.get("comment", rich["comment"])
    return rich


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
        "fact_keywords": _validate_fact_keywords([], single, cfg["pexels_keywords"], n=4),
        "angle": "",
        "format": random.choice(FORMATS),
        "title_en": f"4 Cosmic Facts About {display}",
        "description_en": f"4 surprising facts about {display}. \n\n#shorts #space #astronomy #cosmos",
        "hook": f"{display} • Trivia",
        "intro": f"{display} behaves in ways most people never expect.",
        "facts": [
            f"{display} shows extreme physics that researchers are still working to fully explain and measure.",
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
- 3~50s: four RICH facts, each ~10s spoken (TTS 실속도 고려 안전선) (about 28-33 words each)
- 50~55s: outro CTA

HARD RULES:
- Each fact MUST be EXACTLY 28-33 words. Count the words.
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
  "facts": ["fact1 (28-33 words)", "fact2", "fact3", "fact4"],
  "outro": "..."
}}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=SCRIPT_MODEL,
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
