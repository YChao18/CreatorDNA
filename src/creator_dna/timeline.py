"""Renderer-facing timeline validation. Layers may overlap each other, not themselves."""
import math
from fractions import Fraction
from pathlib import Path

from .contracts import validate
from .errors import CreatorDNAError
from .production_assets import USABLE_RIGHTS, probe, validate_assets, check_portrait_sources
from .storage import digest_file
from .subtitles import check_cues, render_ass, wrap_text
from .tts_audio import wave_metadata


DEFAULT_CONFIG = {
    "schema_version": "3.0-05", "output": None,
    "layout": {"fit": "contain", "anchor": "center", "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
               "crop": None, "pad_color": "#000000"},
    "subtitle_style": {"font": "Microsoft YaHei", "font_size": 40, "color": "#FFFFFF",
                       "highlight_color": "#FFD400", "outline_color": "#000000", "outline": 2, "shadow": 1,
                       "position": "bottom", "margin_x": 48, "margin_y": 96,
                       "max_chars_per_line": 15, "max_lines": 2},
    "background_asset_id": None,
}


def finite(data):
    if isinstance(data, float) and not math.isfinite(data):
        raise CreatorDNAError("配置含非有限数值。")
    if isinstance(data, dict):
        for value in data.values():
            finite(value)
    elif isinstance(data, list):
        for value in data:
            finite(value)


def check_output(output):
    try:
        fps = float(Fraction(output["frame_rate"]))
        if not 0 < fps <= 240:
            raise ValueError()
    except (ValueError, ZeroDivisionError, TypeError):
        raise CreatorDNAError("输出帧率无效。") from None
    if output["width"] % 2 or output["height"] % 2:
        raise CreatorDNAError("输出尺寸必须为偶数，供下一阶段编码使用。")
    expected = "portrait" if output["height"] > output["width"] else "landscape" if output["width"] > output["height"] else "square"
    if expected != output["orientation"]:
        raise CreatorDNAError("显示方向与尺寸不一致。")


def check_layout(layout):
    for name in ("rect", "crop"):
        r = layout[name]
        if r is not None and (r["x"]+r["width"] > 1+1e-6 or r["y"]+r["height"] > 1+1e-6):
            raise CreatorDNAError("布局/裁切矩形须位于归一化 0–1 画面内。")


def check_style(style, output):
    if any(c in style["font"] for c in ",\r\n"):
        raise CreatorDNAError("字幕字体名称无效。")
    width = output["width"] - 2*style["margin_x"]
    height = output["height"] - 2*style["margin_y"]
    if min(width, height) <= 0 or style["max_chars_per_line"]*style["font_size"] + 2*(style["outline"]+style["shadow"]) > width:
        raise CreatorDNAError("字幕字号/每行字数超过水平安全区域。")
    if style["max_lines"]*style["font_size"]*1.3 + 2*(style["outline"]+style["shadow"]) > height:
        raise CreatorDNAError("字幕行数/字号超过垂直安全区域。")


def validate_config(config, output):
    finite(config)
    validate(config, "production-config")
    check_output(output)
    check_layout(config["layout"])
    check_style(config["subtitle_style"], output)


def validate_timeline(timeline, base=None, check_files=True, ffprobe="ffprobe", check_ass_consistency=True):
    """Return a full diagnostic list; ready is derived and never trusted from edited JSON."""
    issues = []
    try:
        finite(timeline)
        validate(timeline, "video-timeline")
        check_output(timeline["output"])
        check_style(timeline["subtitles"]["style"], timeline["output"])
        validate_assets({"schema_version": "3.0-05", "assets": timeline["assets"]})
    except CreatorDNAError as exc:
        return {"valid": False, "issues": [str(exc)], "ready_for_render": False}
    base = Path(base or ".").resolve()
    assets = {a["asset_id"]: a for a in timeline["assets"]}
    try:
        selected_ids = {c["asset_id"] for c in timeline["foreground"] if c["role"] == "portrait"}
        check_portrait_sources([a for k,a in assets.items() if k in selected_ids])
    except CreatorDNAError as exc:
        issues.append(str(exc))
    duration = timeline["duration"]
    source_ranges = {}
    file_checks = {}

    def file_ok(record, media_type=None):
        path = (base / record["path"]).resolve()
        key = (str(path), record["sha256"], media_type)
        if key in file_checks:
            return file_checks[key]
        if not path.is_file():
            issues.append(f"素材缺失：{path}")
            file_checks[key] = None
            return None
        if digest_file(path) != record["sha256"]:
            issues.append(f"文件哈希已变化：{path}")
        measured = None
        if media_type:
            try:
                measured = probe(path, media_type, ffprobe)
                if measured[0:2] != (record["width"], record["height"]) or (media_type == "video" and abs(measured[2]-record["duration"]) > .05):
                    issues.append(f"素材实测元数据与配置不符：{record['asset_id']}")
            except (CreatorDNAError, OSError, ValueError) as exc:
                issues.append(str(exc))
        file_checks[key] = measured
        return measured

    if abs(timeline["audio"]["end"]-duration) > 1e-6:
        issues.append("主音频没有覆盖总时长。")
    if check_files:
        file_ok(timeline["audio"])
        try:
            if abs(wave_metadata((base / timeline["audio"]["path"]).resolve())["duration_seconds"]-duration) > 1e-6:
                issues.append("主音频实测时长不匹配。")
        except CreatorDNAError as exc:
            issues.append(str(exc))
        file_ok(timeline["subtitles"])
        for a in assets.values():
            file_ok(a, a["media_type"])
    clip_ids = set()
    for layer in ("foreground", "background"):
        previous = 0
        for index, clip in enumerate(sorted(timeline[layer], key=lambda c: c["start"])):
            start, end = clip["start"], clip["end"]
            if clip["clip_id"] in clip_ids:
                issues.append("镜头 ID 重复："+clip["clip_id"])
            clip_ids.add(clip["clip_id"])
            if not 0 <= start < end <= duration+1e-6:
                issues.append("镜头时间范围非法："+clip["clip_id"])
            if start < previous-1e-6:
                issues.append(f"{layer} 意外重叠：{clip['clip_id']}")
            if layer == "foreground" and start > previous+1e-6:
                issues.append(f"前景轨缺口：{previous}–{start}")
            previous = max(previous, end)
            if layer == "background" and clip["role"] != "background" or layer == "foreground" and clip["role"] == "background":
                issues.append("镜头角色与轨道不一致。")
            t = clip["transition"]
            if (t["type"] == "cut" and t["duration"] != 0) or (t["type"] == "fade" and not 0 < t["duration"] <= end-start):
                issues.append("转场时长配置无效。")
            try:
                check_layout(clip["layout"])
            except CreatorDNAError as exc:
                issues.append(str(exc))
            a = assets.get(clip["asset_id"])
            if not a:
                issues.append("镜头缺少素材："+clip["clip_id"])
                continue
            if a["rights"] not in USABLE_RIGHTS:
                issues.append("素材未经授权："+a["asset_id"])
            if (clip["role"] == "portrait") != (a["kind"] == "portrait") or (clip["role"] == "background") != (a["kind"] == "background"):
                issues.append("素材类别与镜头角色不匹配。")
            if a["media_type"] == "video":
                astart, aend = clip["source_start"], clip["source_end"]
                if aend is None or not a["source_start"] <= astart < aend <= a["source_end"]+1e-6 or abs((aend-astart)-(end-start)) > 1e-6:
                    issues.append("镜头越出素材区间或时长不匹配："+clip["clip_id"])
                if clip["role"] == "portrait":
                    if a.get("portrait_provenance") and (clip["layout"]["fit"] != "contain" or clip["layout"]["crop"]):
                        issues.append("有来源记录的人像必须完整等比放入画面，禁止裁掉上半身。")
                    if a["audio_sha256"] != timeline["audio"]["sha256"]:
                        issues.append("人像素材不属于当前口播音频。")
                    if abs(astart-a["source_start"]-(start-a["audio_offset"])) > 1e-6:
                        issues.append("人像与主音频时间偏移不匹配。")
                    if aend is not None:
                        ranges = source_ranges.setdefault(a["sha256"], [])
                        if any(max(astart,s) < min(aend,e)-1e-6 for s,e in ranges):
                            issues.append("人像素材区间重复，存在动作循环风险。")
                        ranges.append((astart,aend))
            elif clip["source_start"] != 0 or clip["source_end"] is not None:
                issues.append("图片镜头不能使用视频源时间。")
        if layer == "foreground" and abs(previous-duration) > 1e-6:
            issues.append("前景轨未完整覆盖视频。")
    cues = timeline["subtitles"]["cues"]
    try:
        check_cues(cues, timeline["audio"]["text"], duration)
        style = timeline["subtitles"]["style"]
        for cue in cues:
            if len(wrap_text(cue["text"], style["max_chars_per_line"])) > style["max_lines"]:
                raise CreatorDNAError("字幕文本超过行数限制。")
            if any(k not in cue["text"] for k in cue["keywords"]):
                raise CreatorDNAError("字幕高亮关键词不在当前文本中。")
        rendered = render_ass(cues, timeline["output"], style)
        if check_files and check_ass_consistency:
            ass_path = (base/timeline["subtitles"]["path"]).resolve()
            if ass_path.is_file() and ass_path.read_text(encoding="utf-8-sig") != rendered:
                raise CreatorDNAError("ASS 与编辑后的字幕/输出配置不一致；请运行 production rebuild。")
    except (CreatorDNAError, OSError, UnicodeError) as exc:
        issues.append(str(exc))
    if any(m["blocking"] for m in timeline["missing_assets"]):
        issues.append("存在阻塞渲染的缺失素材。")
    ready = not issues
    if timeline["ready_for_render"] and not ready:
        issues.append("ready_for_render 标记与实际校验不一致。")
    return {"valid": not issues, "issues": issues, "ready_for_render": ready}
