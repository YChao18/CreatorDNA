"""Audio-bound timing normalization and deterministic ASS authoring; no character-rate timing."""
import math
import re
import unicodedata
from pathlib import Path
from typing import Protocol

from .contracts import validate
from .errors import CreatorDNAError
from .storage import digest_file, read_json, write_text
from .tts_audio import wave_metadata


class AlignmentProvider(Protocol):
    """Optional local forced aligner. Returns subtitle-timing with actual audio hash."""
    def align(self, audio: Path, text: str) -> dict: ...


def spoken(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in text if not c.isspace() and unicodedata.category(c)[0] not in {"P", "Z"})


def check_cues(cues, text, duration):
    previous = 0
    ids = set()
    for cue in cues:
        if not isinstance(cue.get("text"), str) or not cue["text"].strip():
            raise CreatorDNAError("字幕正文必须为非空字符串。")
        start, end = cue["start"], cue["end"]
        if any(isinstance(v, bool) or not math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration + 1e-6:
            raise CreatorDNAError("字幕时间无效或超出实际音频。")
        if start < previous - 1e-6 or cue["cue_id"] in ids:
            raise CreatorDNAError("字幕时间重叠、逆序或 ID 重复。")
        if not spoken(cue["text"]):
            raise CreatorDNAError("字幕不能只有标点。")
        ids.add(cue["cue_id"])
        previous = end
    if spoken("".join(c["text"] for c in cues)) != spoken(text):
        raise CreatorDNAError("时间戳文本与口播文本不一致；请核对实际音频或重新强制对齐。")


def safe_relative(folder, name):
    path = (folder / name).resolve()
    if path == folder or folder not in path.parents or not path.is_file():
        raise CreatorDNAError("TTS 音频路径缺失或越出清单目录。")
    return path


def speech_input(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    validate(manifest, "tts-manifest")
    folder = manifest_path.parent
    exported = safe_relative(folder, manifest["audio"])
    if digest_file(exported) != manifest["sha256"]:
        raise CreatorDNAError("TTS 成品音频哈希不匹配。")
    audio = safe_relative(folder, manifest["canonical_audio"])
    duration = wave_metadata(audio)["duration_seconds"]
    if abs(duration - manifest["canonical_duration_seconds"]) > 1e-6:
        raise CreatorDNAError("TTS PCM 时长与清单不一致。")
    previous, parts = 0, []
    for seg in manifest["segments"]:
        start, end = seg["start_seconds"], seg["end_seconds"]
        if not 0 <= start < end <= duration + 1e-6 or start < previous - 1e-6:
            raise CreatorDNAError("TTS 分段边界非法。")
        if manifest["text"][seg["text_start"]:seg["text_end"]] != seg["text"]:
            raise CreatorDNAError("TTS 分段文本偏移无效。")
        segment_audio = safe_relative(folder, seg["audio"])
        if seg.get("sha256") and digest_file(segment_audio) != seg["sha256"]:
            raise CreatorDNAError("TTS 分段音频哈希不匹配。")
        if abs(wave_metadata(segment_audio)["duration_seconds"] - (end-start)) > 1e-6:
            raise CreatorDNAError("TTS 分段时间与实测 PCM 不一致。")
        previous = end
        parts.append(seg["text"])
    if spoken("".join(parts)) != spoken(manifest["text"]):
        raise CreatorDNAError("TTS 分段未覆盖完整正文。")
    # Legacy V3.0-03 does not hash canonical_audio. Verify its actual PCM against
    # the individually hashed segments and measured inserted silence instead.
    import wave
    with wave.open(str(audio), "rb") as canonical:
        rate, position = canonical.getframerate(), 0
        for seg in manifest["segments"]:
            first, last = round(seg["start_seconds"]*rate), round(seg["end_seconds"]*rate)
            if any(abs(v*rate-round(v*rate)) > 1e-4 for v in (seg["start_seconds"],seg["end_seconds"])):
                raise CreatorDNAError("TTS 边界不在实际 PCM 帧上。")
            remaining = first-position
            while remaining > 0:
                size = min(remaining, 65536)
                if any(canonical.readframes(size)):
                    raise CreatorDNAError("TTS 段间停顿不是清单声明的静音。")
                remaining -= size
            with wave.open(str(safe_relative(folder, seg["audio"])), "rb") as chunk:
                if chunk.getframerate() != rate:
                    raise CreatorDNAError("TTS 分段采样率与 canonical_audio 不一致。")
                remaining = last-first
                while remaining > 0:
                    size = min(remaining, 65536)
                    if canonical.readframes(size) != chunk.readframes(size):
                        raise CreatorDNAError("TTS canonical_audio 与分段 PCM 内容不一致。")
                    remaining -= size
            position = last
        while True:
            tail = canonical.readframes(65536)
            if not tail:
                break
            if any(tail):
                raise CreatorDNAError("TTS 未声明的尾部音频内容。")
    return audio, duration, manifest["text"], manifest


def provider_cues(manifest, max_chars):
    cues = []
    for seg in manifest["segments"]:
        raw = seg["provider_timestamps"]
        if raw is not None:
            if (seg.get("provider_timestamps_unit") != "milliseconds" or
                    seg.get("timestamps_relative_to") != "segment_audio"):
                raise CreatorDNAError("服务商时间戳单位/时间基准不明确；请导入强制对齐结果。")
            # MiniMax sentence subtitle format. Unknown response shapes fail closed.
            records = raw if isinstance(raw, list) else raw.get("subtitles")
            if not isinstance(records, list) or not records:
                raise CreatorDNAError("不支持的服务商时间戳格式；请导入标准 subtitle-timing。")
            local = []
            for r in records:
                if not isinstance(r, dict) or "text" not in r:
                    raise CreatorDNAError("服务商字幕缺少正文。")
                keys = ("time_begin", "time_end") if {"time_begin", "time_end"} <= r.keys() else ("start_time", "end_time")
                if not set(keys) <= r.keys():
                    raise CreatorDNAError("服务商字幕缺少 time_begin/time_end 或 start_time/end_time。")
                try:
                    if any(isinstance(r[k], bool) for k in keys):
                        raise ValueError()
                    start, end = float(r[keys[0]])/1000, float(r[keys[1]])/1000
                except (ValueError, TypeError):
                    raise CreatorDNAError("服务商字幕时间不是数值。") from None
                local.append({"cue_id": f"cue-{len(cues)+len(local):04d}", "start": start, "end": end,
                              "text": r["text"], "timing_basis": "provider_sentence_milliseconds"})
            check_cues(local, seg["text"], seg["end_seconds"]-seg["start_seconds"])
            cues.extend({**c, "start": c["start"]+seg["start_seconds"], "end": c["end"]+seg["start_seconds"]} for c in local)
        else:
            sentences = [s for s in re.split(r"(?<=[。！？!?；;])\s*", seg["text"].strip()) if spoken(s)]
            if len(sentences) != 1 or len(seg["text"].strip()) > max_chars:
                raise CreatorDNAError("缺少句级时间戳，长段落不能按字数估时；请使用 --timing 导入音频强制对齐结果。")
            cues.append({"cue_id": f"cue-{len(cues):04d}", "start": seg["start_seconds"], "end": seg["end_seconds"],
                         "text": seg["text"].strip(), "timing_basis": "measured_single_sentence_segment_coarse"})
    return cues


def load_timing(audio, text, duration, timing_path=None, manifest=None, aligner=None, capacity=36):
    if timing_path or aligner:
        data = read_json(timing_path) if timing_path else aligner.align(audio, text)
        validate(data, "subtitle-timing")
        if data["audio_sha256"] != digest_file(audio) or spoken(data["text"]) != spoken(text):
            raise CreatorDNAError("对齐结果不属于当前音频/正文。")
        cues = data["cues"]
    elif manifest:
        cues = provider_cues(manifest, capacity)
    else:
        raise CreatorDNAError("音频必须提供真实时间戳，不支持按字数估算。")
    check_cues(cues, text, duration)
    return cues


def sentence_cues(units, style):
    """Group fine timestamps at punctuation/line capacity; never invent internal boundaries."""
    capacity = style["max_chars_per_line"] * style["max_lines"]
    result, group = [], []

    def flush():
        if group:
            result.append({"cue_id": f"cue-{len(result):04d}", "start": group[0]["start"], "end": group[-1]["end"],
                           "text": "".join(c["text"] for c in group),
                           "timing_basis": ";".join(sorted({c["timing_basis"] for c in group}))})
            group.clear()

    for unit in units:
        if len(unit["text"]) > capacity:
            raise CreatorDNAError("单个时间戳区间文本超出字幕容量；请提供更细对齐或调整每行字数/行数。")
        if group and (sum(len(c["text"]) for c in group)+len(unit["text"]) > capacity or unit["start"]-group[-1]["end"] > .35):
            flush()
        group.append(unit)
        if re.search(r"[。！？!?；;][\s\"'”’]*$", unit["text"]):
            flush()
    flush()
    return result


def wrap_text(text, limit):
    """Prefer English word/Chinese punctuation boundaries without changing spoken text."""
    lines = []
    while len(text) > limit:
        split = limit
        space = text.rfind(" ", 0, limit+1)
        if space > limit//2:
            split = space+1
        elif text[split:split+1] in "，。！？、；：,.!?;:":
            split = max(1, split-1)
        lines.append(text[:split])
        text = text[split:]
    if text:
        lines.append(text)
    return lines


def ass_time(seconds):
    centis = round(seconds*100)
    return f"{centis//360000}:{centis//6000%60:02d}:{centis//100%60:02d}.{centis%100:02d}"


def ass_color(rgb):
    return "&H00" + rgb[5:7] + rgb[3:5] + rgb[1:3]


def escaped(text):
    # Prevent imported text from becoming ASS override tags or control escapes.
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", " ").replace("\r", " ")


def highlighted(text, keywords, style):
    keywords = sorted({k for k in keywords if k and k in text}, key=lambda k: (-len(k), k))
    if not keywords:
        return escaped(text)
    pattern = "(" + "|".join(re.escape(k) for k in keywords) + ")"
    return "".join(("{\\c" + ass_color(style["highlight_color"]) + "&}" + escaped(part) +
                     "{\\c" + ass_color(style["color"]) + "&}") if part in keywords else escaped(part)
                   for part in re.split(pattern, text) if part)


def highlighted_lines(lines, keywords, style):
    text = "".join(lines)
    keywords = sorted({k for k in keywords if k and k in text}, key=lambda k: (-len(k), k))
    spans = [(m.start(), m.end()) for m in re.finditer("|".join(re.escape(k) for k in keywords), text)] if keywords else []
    offset, rendered = 0, []
    for line in lines:
        parts, position = [], offset
        end = offset+len(line)
        for start_h, end_h in spans:
            a, b = max(offset,start_h), min(end,end_h)
            if a < b:
                parts.append(escaped(text[position:a]))
                parts.append("{\\c"+ass_color(style["highlight_color"])+"&}"+escaped(text[a:b])+"{\\c"+ass_color(style["color"])+"&}")
                position = b
        parts.append(escaped(text[position:end]))
        rendered.append("".join(parts))
        offset = end
    return "\\N".join(rendered)


def render_ass(cues, output, style):
    alignment = {"bottom": 2, "top": 8, "center": 5}[style["position"]]
    if any(c in style["font"] for c in ",\n\r"):
        raise CreatorDNAError("ASS 字体名不能包含逗号或换行。")
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {output['width']}\nPlayResY: {output['height']}\n"
              "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Default,{style['font']},{style['font_size']},{ass_color(style['color'])},"
              f"{ass_color(style['highlight_color'])},{ass_color(style['outline_color'])},&H80000000,0,0,0,0,100,100,0,0,1,"
              f"{style['outline']},{style['shadow']},{alignment},{round(style['margin_x'])},{round(style['margin_x'])},{round(style['margin_y'])},1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events, previous_end = [], 0
    for cue in cues:
        lines = wrap_text(cue["text"], style["max_chars_per_line"])
        if len(lines) > style["max_lines"]:
            raise CreatorDNAError("字幕换行超过配置；需更细时间戳或扩大字幕容量。")
        start, end = round(cue["start"]*100), round(cue["end"]*100)
        if start < previous_end or start >= end:
            raise CreatorDNAError("ASS 厘秒精度导致字幕重叠/零时长，请合并过短时间戳。")
        previous_end = end
        body = highlighted_lines(lines, cue.get("keywords", []), style)
        events.append(f"Dialogue: 0,{ass_time(cue['start'])},{ass_time(cue['end'])},Default,,0,0,0,,{body}")
    return header + "\n".join(events) + "\n"


def write_ass(path, cues, output, style):
    write_text(path, render_ass(cues, output, style))
