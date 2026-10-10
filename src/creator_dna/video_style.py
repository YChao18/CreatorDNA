"""Conservative visual profile from V3.0-01 measurements and explicit human evidence."""
from fractions import Fraction
from statistics import median

from .contracts import validate
from .errors import CreatorDNAError
from .storage import digest_file, read_json
from .video_assets import validate_library


FIELDS = ("transitions", "subtitle_style", "subtitle_position", "font", "colors", "outline_shadow",
          "keyword_highlight", "background_usage", "opening", "ending", "layout")


def output_from_template(template):
    w, h = template["width"], template["height"]
    fps = template["frame_rate"]
    try:
        if not 0 < float(Fraction(fps)) <= 240:
            raise ValueError()
    except (ValueError, ZeroDivisionError, TypeError):
        raise CreatorDNAError("参考模板缺少有效帧率，请先修订模板。") from None
    return {"width": w, "height": h, "frame_rate": fps, "pixel_aspect_ratio": "1",
            "orientation": "portrait" if h > w else "landscape" if w > h else "square"}


def build_profile(library_path, template_id=None, review=None):
    library = read_json(library_path)
    validate_library(library)
    templates = library["output_templates"]
    template = next((t for t in templates if t["template_id"] == template_id), None) if template_id else next(
        (t for t in templates if t["preferred_by_duration"]), None)
    if not template:
        raise CreatorDNAError("未找到选定输出模板。")
    sources = {s["source_id"]: s for s in library["sources"] if s["status"] == "analyzed"
               and s["source_id"] in template["source_ids"]}
    if not sources:
        raise CreatorDNAError("输出模板没有已分析的来源。")
    from pathlib import Path
    for source in sources.values():
        if not Path(source["path"]).is_file() or digest_file(source["path"]) != source["sha256"]:
            raise CreatorDNAError("视觉分析来源缺失或已变化，请重新运行 V3.0-01 分析。")
    total = sum(s["metadata"]["duration"] for s in sources.values())
    durations = sorted(shot["duration"] for s in sources.values() for shot in s["shots"])
    observations = {k: {"status": "unknown", "value": None, "evidence": []} for k in FIELDS}
    amounts = {k: 0.0 for k in ("portrait", "b_roll", "background")}
    reviewer = None

    def evidence(e):
        source = sources.get(e["source_id"])
        if not source or not 0 <= e["start"] < e["end"] <= source["metadata"]["duration"]:
            raise CreatorDNAError("视觉审核证据来源或时间无效。")
        frames = {f["frame_id"]: f for f in source["frames"]}
        if any(fid not in frames or not e["start"] <= frames[fid]["time"] < e["end"] for fid in e["frame_ids"]):
            raise CreatorDNAError("视觉审核帧证据不存在或不在指定区间。")

    if review is not None:
        validate(review, "video-style-review")
        reviewer = review["reviewer"]
        intervals = {}
        for interval in review["intervals"]:
            evidence(interval)
            intervals.setdefault(interval["source_id"], []).append(interval)
            amounts[interval["role"]] += interval["end"] - interval["start"]
        for items in intervals.values():
            end = 0
            for item in sorted(items, key=lambda i: i["start"]):
                if item["start"] < end:
                    raise CreatorDNAError("视觉审核区间重叠，不能重复统计出镜占比。")
                end = item["end"]
        for name, obs in review["observations"].items():
            if obs["status"] == "manual":
                if not obs["value"] or not obs["evidence"]:
                    raise CreatorDNAError("人工风格结论须有描述和原视频证据。")
                for e in obs["evidence"]:
                    evidence(e)
            elif obs["value"] is not None or obs["evidence"]:
                raise CreatorDNAError("未知视觉风格不得填写推测值。")
            observations[name] = obs
    reviewed = sum(amounts.values())
    profile = {"schema_version": "3.0-05", "creator_id": library["creator_id"],
               "library_sha256": digest_file(library_path), "template_id": template["template_id"],
               "output": output_from_template(template), "sample_count": len(sources),
               "shot_durations": {"count": len(durations), "median": median(durations),
                  "p10": durations[round((len(durations)-1)*.1)], "p90": durations[round((len(durations)-1)*.9)],
                  "basis": "FFmpeg 像素切镜候选；不是语义场景或已确认剪辑点。"},
               "screen_time": {"total": total, "reviewed": reviewed, "coverage": reviewed/total,
                  **{k+"_ratio": amounts[k]/total if reviewed else None for k in amounts},
                  "unknown_ratio": max(0, 1-reviewed/total),
                  "basis": "人工标注区间占选定模板总时长；未检出人脸不能推定 B-roll。"},
               "observations": observations, "reviewer": reviewer,
               "limitations": ["少量采样不推断完整视觉风格；人工结论仅覆盖所列证据。",
                                "字幕 OCR、字体识别、语义切镜未自动接入。生产默认样式是可编辑参数，非作者风格结论。"]}
    validate(profile, "video-style-profile")
    return profile


def review_template():
    return {"reviewer": "请填写审核人", "intervals": [], "observations": {
        k: {"status": "unknown", "value": None, "evidence": []} for k in FIELDS}}
