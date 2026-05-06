"""1분 동물 트리비아 영상 합성.

- 배경: Pexels portrait 영상 3개 concatenate (각 fact별)
- 오디오: TTS 라인 5개 (intro + fact x3 + outro) concatenate
- 그래픽:
  - 상단: hook ("Otters • Trivia") 전체 표시
  - 중하단: 현재 라인 자막 (intro/fact/outro) 시간차 표시
  - 하단: 채널 브랜딩
- 길이: TTS 합 + 패딩 → 최대 59.5s (Shorts 안전선)
"""
import logging
import os
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    afx,
    concatenate_audioclips,
    concatenate_videoclips,
    vfx,
)

from config import (
    BASE_DIR,
    BGM_VOLUME_DB,
    CAPTION_BASE_SIZE,
    CAPTION_HIGHLIGHT_NUMBERS,
    CAPTION_PHRASE_SIZE,
    CAPTION_Y_RATIO,
    FONT_PATH,
    VIDEO_BITRATE,
    VIDEO_DURATION_SEC,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_PRESET,
    VIDEO_THREADS,
    VIDEO_WIDTH,
)

log = logging.getLogger("spacebot.composer")

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F6FF"
    "\U0001F700-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F02F"
    "\U0001F0A0-\U0001F0FF"
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def _find_font(bold: bool = True):
    candidates = [
        FONT_PATH,
        # Linux (GitHub Actions ubuntu)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    for f in candidates:
        if f and os.path.exists(f):
            return f
    return None


def _resize_loop_to_duration(clip, duration):
    if clip.duration >= duration:
        return clip.subclip(0, duration)
    loops = int(duration // clip.duration) + 1
    return concatenate_videoclips([clip] * loops).subclip(0, duration)


def _fit_portrait(clip):
    w, h = clip.size
    target_ratio = VIDEO_WIDTH / VIDEO_HEIGHT
    src_ratio = w / h
    if src_ratio > target_ratio:
        new_w = int(h * target_ratio)
        x_off = (w - new_w) // 2
        clip = clip.crop(x1=x_off, x2=x_off + new_w)
    elif src_ratio < target_ratio:
        new_h = int(w / target_ratio)
        y_off = (h - new_h) // 2
        clip = clip.crop(y1=y_off, y2=y_off + new_h)
    return clip.resize((VIDEO_WIDTH, VIDEO_HEIGHT))


def _ken_burns(clip, duration, start_zoom=1.05, end_zoom=1.15):
    return clip.resize(lambda t: start_zoom + (end_zoom - start_zoom) * (t / duration))


def _vignette_image():
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-w // 4, -h // 6, w + w // 4, h + h // 6], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=int(min(w, h) * 0.18)))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste((0, 0, 0, 70), (0, 0, w, h))
    edge = Image.new("RGBA", (w, h), (0, 0, 0, 170))
    inv_mask = Image.eval(mask, lambda v: 255 - v)
    out.paste(edge, (0, 0), inv_mask)
    return out


def _wrap_text(draw, text, font, max_width):
    if draw.textlength(text, font=font) <= max_width:
        return [text]
    parts = text.split()
    if len(parts) <= 1:
        out, cur = [], ""
        for ch in text:
            test = cur + ch
            if draw.textlength(test, font=font) <= max_width:
                cur = test
            else:
                if cur:
                    out.append(cur)
                cur = ch
        if cur:
            out.append(cur)
        return out
    lines, cur = [], ""
    for w in parts:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _make_text_image(text: str, font_size: int, max_width: int,
                     fg=(255, 255, 255, 250), shadow=True,
                     stroke_color=(0, 0, 0, 220), stroke_width=2) -> np.ndarray:
    text = _strip_emoji(text)
    if not text:
        return np.zeros((1, 1, 4), dtype=np.uint8)

    font_path = _find_font(bold=True)
    if font_path:
        font = ImageFont.truetype(font_path, font_size)
    else:
        font = ImageFont.load_default()

    tmp_img = Image.new("RGBA", (max_width, 100), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp_img)
    lines = _wrap_text(tmp_draw, text, font, max_width - 80)
    line_h = font_size + 10
    total_h = line_h * len(lines) + 60

    img = Image.new("RGBA", (max_width, total_h), (0, 0, 0, 0))

    if shadow:
        shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow_layer)
        y = 30
        for line in lines:
            w = tmp_draw.textlength(line, font=font)
            x = (max_width - w) // 2
            sd.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 180))
            y += line_h
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=5))
        img = Image.alpha_composite(img, shadow_layer)

    text_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)
    y = 30
    for line in lines:
        w = tmp_draw.textlength(line, font=font)
        x = (max_width - w) // 2
        if stroke_width > 0:
            for dx in range(-stroke_width, stroke_width + 1):
                for dy in range(-stroke_width, stroke_width + 1):
                    if dx == 0 and dy == 0:
                        continue
                    td.text((x + dx, y + dy), line, font=font, fill=stroke_color)
        td.text((x, y), line, font=font, fill=fg)
        y += line_h

    img = Image.alpha_composite(img, text_layer)
    return np.array(img)


def _fit_caption_image(text: str, base_size: int, max_width: int, max_lines: int = 3) -> np.ndarray:
    """자막 폰트 자동 축소: max_lines 안에 들어갈 때까지. 최소 사이즈 64로 가독성 보장."""
    size = base_size
    while size >= 64:
        font_path = _find_font()
        if font_path:
            font = ImageFont.truetype(font_path, size)
        else:
            font = ImageFont.load_default()
        tmp = Image.new("RGBA", (max_width, 100), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        lines = _wrap_text(td, text, font, max_width - 60)
        if len(lines) <= max_lines:
            break
        size -= 4
    return _make_text_image(text, size, max_width, fg=(255, 255, 255, 255), stroke_width=3)


def _make_static_clip(arr: np.ndarray, duration: float, position, fade_in=0.3, fade_out=0.3):
    if arr.shape[0] <= 1 or arr.shape[1] <= 1:
        return None
    clip = ImageClip(arr, transparent=True).set_duration(duration).set_position(position)
    if fade_in > 0:
        clip = clip.crossfadein(fade_in)
    if fade_out > 0:
        clip = clip.crossfadeout(fade_out)
    return clip


_NUMBER_RE = re.compile(r"\d")


def _phrase_color(text: str):
    """숫자 포함 phrase는 옐로우, 아니면 흰색."""
    if CAPTION_HIGHLIGHT_NUMBERS and _NUMBER_RE.search(text):
        return (255, 220, 0, 255)  # 옐로우
    return (255, 255, 255, 255)


def _make_phrase_clip(phrase_text: str, start: float, end: float, base_size=None):
    """짧은 phrase (3-4단어) 자막. 짧은 시간이라 fade도 빠르게."""
    base_size = base_size or CAPTION_BASE_SIZE
    if end <= start:
        return None
    fg = _phrase_color(phrase_text)
    # 짧은 phrase라 max_lines=2면 충분
    size = base_size
    while size >= 56:
        font_path = _find_font()
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        tmp = Image.new("RGBA", (VIDEO_WIDTH - 60, 100), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        lines = _wrap_text(td, phrase_text, font, VIDEO_WIDTH - 80)
        if len(lines) <= 2:
            break
        size -= 4
    arr = _make_text_image(phrase_text, size, VIDEO_WIDTH - 80, fg=fg, stroke_width=3)
    if arr.shape[0] <= 1:
        return None
    clip = ImageClip(arr, transparent=True).set_duration(end - start).set_start(start)
    y = int(VIDEO_HEIGHT * CAPTION_Y_RATIO)
    clip = clip.set_position(("center", y))
    clip = clip.crossfadein(0.08).crossfadeout(0.08)
    return clip


def _split_into_phrases(words: list, line_start: float, line_end: float,
                       phrase_size: int = None) -> list:
    """word_cues를 3-4단어 phrase로 묶기. 절대 시간 기준 (line_start 더해서)."""
    phrase_size = phrase_size or CAPTION_PHRASE_SIZE
    if not words:
        return []
    phrases = []
    cursor = 0
    while cursor < len(words):
        chunk = words[cursor:cursor + phrase_size]
        if not chunk:
            break
        text = " ".join(w["text"] for w in chunk)
        start = line_start + chunk[0]["start"]
        end_w = line_start + chunk[-1]["end"]
        phrases.append({"text": text, "start": start, "end": end_w})
        cursor += phrase_size

    # 끊김 없도록 끝 시간을 다음 phrase 시작으로 (마지막은 line_end로)
    for i in range(len(phrases) - 1):
        phrases[i]["end"] = phrases[i + 1]["start"]
    if phrases:
        phrases[-1]["end"] = line_end
    return phrases


def _make_caption_window(text: str, start: float, end: float, base_size=80):
    """words 없을 때 fallback: 라인 통째로 자막."""
    return [_make_phrase_clip(text, start, end, base_size)]


def _make_hook_clip(text: str, duration: float):
    """상단 hook 박스 — 전체 영상 동안 표시."""
    arr = _make_text_image(text, font_size=78, max_width=VIDEO_WIDTH - 200)
    if arr.shape[0] <= 1:
        return None
    clip = ImageClip(arr, transparent=True).set_duration(duration)
    y = int(VIDEO_HEIGHT * 0.10)
    clip = clip.set_position(("center", y))
    clip = clip.crossfadein(0.5)
    return clip


def _make_brand_clip(duration: float, text="@AnimalTrivia60s"):
    arr = _make_text_image(text, font_size=44, max_width=VIDEO_WIDTH - 100,
                           fg=(255, 255, 255, 200))
    if arr.shape[0] <= 1:
        return None
    clip = ImageClip(arr, transparent=True).set_duration(duration)
    y = int(VIDEO_HEIGHT * 0.92)
    clip = clip.set_position(("center", y))
    return clip


def _make_jolt_flash(duration: float = 0.5):
    """영상 시작 시각 jolt — 짧은 화이트 플래시로 스크롤 멈춤 유도."""
    arr = np.full((VIDEO_HEIGHT, VIDEO_WIDTH, 4), [255, 255, 255, 255], dtype=np.uint8)
    clip = ImageClip(arr, transparent=True).set_duration(duration).set_start(0)
    clip = clip.crossfadeout(duration)  # alpha 100→0 fade
    return clip


def _make_cta_card_clip(start: float, duration: float):
    """마지막 4-5초 CTA — 큰 'FOLLOW + LIKE' 카드 (옐로우, 두꺼운 외곽선)."""
    if duration <= 0:
        return []
    layers = []
    # 큰 메인 텍스트
    big_arr = _make_text_image(
        "FOLLOW + LIKE",
        font_size=140,
        max_width=VIDEO_WIDTH - 60,
        fg=(255, 220, 0, 255),
        stroke_color=(0, 0, 0, 255),
        stroke_width=6,
    )
    big_clip = ImageClip(big_arr, transparent=True).set_duration(duration).set_start(start)
    big_clip = big_clip.set_position(("center", int(VIDEO_HEIGHT * 0.42)))
    big_clip = big_clip.crossfadein(0.3)
    layers.append(big_clip)

    # 보조 텍스트
    small_arr = _make_text_image(
        "for more animal facts",
        font_size=64,
        max_width=VIDEO_WIDTH - 80,
        fg=(255, 255, 255, 240),
        stroke_color=(0, 0, 0, 220),
        stroke_width=3,
    )
    small_clip = ImageClip(small_arr, transparent=True).set_duration(duration).set_start(start)
    small_clip = small_clip.set_position(("center", int(VIDEO_HEIGHT * 0.62)))
    small_clip = small_clip.crossfadein(0.4)
    layers.append(small_clip)
    return layers


def _make_vignette_clip(duration: float):
    vig = _vignette_image()
    arr = np.array(vig)
    return ImageClip(arr, transparent=True).set_duration(duration).set_position((0, 0))


def _pick_bgm():
    """assets/bgm/*.mp3 풀에서 랜덤 1개. 없으면 None."""
    import random
    bgm_dir = os.path.join(BASE_DIR, "assets", "bgm")
    if not os.path.isdir(bgm_dir):
        return None
    files = [
        os.path.join(bgm_dir, f) for f in os.listdir(bgm_dir)
        if f.lower().endswith((".mp3", ".m4a", ".wav", ".ogg"))
    ]
    if not files:
        return None
    return random.choice(files)


def _build_background(bg_paths: list, segment_durations: list, total_dur: float):
    """배경 영상들을 segment_durations 길이로 잘라 concatenate.

    bg_paths가 부족하면 처음 영상 재사용. 각 세그먼트는 fit_portrait + Ken Burns.
    """
    n_seg = len(segment_durations)
    paths = list(bg_paths)
    while len(paths) < n_seg:
        paths.append(paths[0])

    clips = []
    for i, dur in enumerate(segment_durations):
        bg = VideoFileClip(paths[i]).without_audio()
        bg = _fit_portrait(bg)
        bg = _resize_loop_to_duration(bg, dur)
        bg = _ken_burns(bg, dur, start_zoom=1.04, end_zoom=1.12)
        bg = bg.resize(width=VIDEO_WIDTH).set_position(("center", "center"))
        clips.append(bg)
    final_bg = concatenate_videoclips(clips, method="compose")
    final_bg = final_bg.subclip(0, total_dur)
    final_bg = final_bg.crossfadein(0.4).crossfadeout(0.6)
    return final_bg


def compose(
    bg_paths: list,
    tts_lines: list,
    hook: str,
    output_path: str,
    pad_between: float = 0.35,
    end_pad: float = 0.6,
    bgm_path: str = None,
) -> str:
    """tts_lines: [{"text", "path", "duration"}] (intro + fact x3 + outro 순서)
    bg_paths: Pexels mp4 경로 리스트 (3개 권장)

    리턴: output_path
    """
    # 1) 오디오 timeline 계산
    timeline = []
    cursor = 0.0
    audio_clips = []
    for i, line in enumerate(tts_lines):
        ac = AudioFileClip(line["path"])
        start = cursor
        ac = ac.set_start(start)
        audio_clips.append(ac)
        end = start + line["duration"]
        timeline.append({
            "text": line["text"],
            "start": start,
            "end": end,
            "words": line.get("words", []),
        })
        cursor = end + pad_between

    raw_total = cursor - pad_between + end_pad
    total_dur = min(raw_total, VIDEO_DURATION_SEC - 0.3)  # Shorts 안전선
    log.info(f"timeline: total={total_dur:.2f}s lines={len(timeline)}")

    # 라인이 잘리는 경우, 마지막 라인 끝을 total_dur로 클램프
    for w in timeline:
        if w["end"] > total_dur:
            w["end"] = total_dur

    # 2) 오디오 합성 — TTS 트랙 + (선택) BGM
    voice_audio = CompositeAudioClip(audio_clips).set_duration(total_dur)
    voice_audio = voice_audio.fx(afx.audio_fadein, 0.3).fx(afx.audio_fadeout, 0.5)

    # 명시 인자 우선, 없으면 로컬 풀에서 픽
    chosen_bgm = bgm_path or _pick_bgm()
    if chosen_bgm:
        try:
            bgm = AudioFileClip(chosen_bgm)
            if bgm.duration < total_dur:
                from moviepy.audio.fx.all import audio_loop
                bgm = audio_loop(bgm, duration=total_dur)
            else:
                bgm = bgm.subclip(0, total_dur)
            bgm = bgm.volumex(10 ** (BGM_VOLUME_DB / 20.0))
            bgm = bgm.fx(afx.audio_fadein, 0.5).fx(afx.audio_fadeout, 1.0)
            full_audio = CompositeAudioClip([bgm, voice_audio])
            log.info(f"bgm mix: {os.path.basename(chosen_bgm)} ({BGM_VOLUME_DB}dB)")
        except Exception as e:
            log.warning(f"bgm 적용 실패 → voice only: {e}")
            full_audio = voice_audio
    else:
        full_audio = voice_audio

    # 3) 배경 — 3개 영상을 fact 구간에 맞춰 자르기
    # 단순화: total_dur을 3등분 (intro/outro는 첫·마지막 영상에 포함)
    n_bg = max(1, min(len(bg_paths), 3))
    seg_durs = [total_dur / n_bg] * n_bg
    bg_clip = _build_background(bg_paths[:n_bg], seg_durs, total_dur)

    # 4) 비네트 + hook + brand + 자막
    layers = [bg_clip, _make_vignette_clip(total_dur)]

    # 시각 jolt (첫 0.5초 화이트 플래시 — 스크롤 멈춤 유도)
    layers.append(_make_jolt_flash(0.5))

    hook_clip = _make_hook_clip(hook, total_dur)
    if hook_clip is not None:
        layers.append(hook_clip)

    # CTA 카드 시각 (마지막 4초) — 자막이 여기 침범하면 안 됨
    cta_dur = min(4.0, total_dur * 0.18)  # 영상 18% 또는 4초 중 작은 값
    cta_start = max(0.0, total_dur - cta_dur)
    cap_cutoff = max(0.0, cta_start - 0.2)  # 자막은 CTA 시작 0.2s 전에 사라짐

    # phrase 단위 자막 (TikTok karaoke 스타일)
    # 마지막 라인(outro)은 CTA 카드와 겹치니 자막에서 제외.
    # 그리고 모든 자막 종료시간은 cap_cutoff 이하로 클램프 — 마지막 fact가 길어
    # CTA에 침범하는 케이스 방지 (2026-05-05 사용자 신고: 마지막 자막 + CTA 중복).
    for i, w in enumerate(timeline):
        if i == len(timeline) - 1:
            continue
        # 자막 시작이 이미 cap_cutoff를 넘었으면 자막 자체 건너뜀
        if w["start"] >= cap_cutoff:
            continue
        line_start = w["start"]
        line_end = min(w["end"], cap_cutoff)
        words = w.get("words") or []
        if words:
            # phrases도 cap_cutoff 이하로 잘라냄
            phrases = _split_into_phrases(words, line_start, line_end)
            for ph in phrases:
                ph_end = min(ph["end"], cap_cutoff)
                if ph_end <= ph["start"]:
                    continue
                clip = _make_phrase_clip(ph["text"], ph["start"], ph_end)
                if clip is not None:
                    layers.append(clip)
        else:
            cap = _make_caption_window(w["text"], line_start, line_end, base_size=CAPTION_BASE_SIZE)
            if cap:
                layers.extend([c for c in cap if c is not None])

    brand = _make_brand_clip(total_dur)
    if brand is not None:
        layers.append(brand)

    layers.extend(_make_cta_card_clip(cta_start, cta_dur))

    final = CompositeVideoClip(layers, size=(VIDEO_WIDTH, VIDEO_HEIGHT)).set_duration(total_dur)
    final = final.set_audio(full_audio)

    final.write_videofile(
        output_path,
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate=VIDEO_BITRATE,
        preset=VIDEO_PRESET,
        threads=VIDEO_THREADS,
        verbose=False,
        logger=None,
    )
    final.close()
    bg_clip.close()
    for ac in audio_clips:
        ac.close()
    return output_path
