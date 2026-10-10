"""Asset indexing/reporting, separate from local measurement and future AI providers."""

from collections import Counter, defaultdict
from pathlib import Path

from .contracts import validate
from .errors import CreatorDNAError
from .storage import default_id, now, validate_id, write_json, write_text
from .video import LocalVideoAnalyzer, scan_videos, video_source


REVIEW_FIELDS = ["person_visibility", "composition", "fixed_scene", "face_clarity", "mouth_visibility",
                 "head_motion", "expression", "body_action", "subtitle_position_style_occlusion",
                 "b_roll_product_screenshot_background", "action_continuity", "shot_matching",
                 "repeated_action_risk", "lip_sync_suitability"]


def classify(segment, frames, groups):
    samples = [f for f in frames if segment["start"] <= f["time"] < segment["end"]]
    detected = [f for f in samples if f["faces"] is not None]
    single = [f for f in detected if len(f["faces"]) == 1]
    visible = sum(bool(f["faces"]) for f in detected) / len(detected) if detected else None
    poor = bool(samples) and all(f["brightness"] < 8 or f["brightness"] > 247 for f in samples)
    if poor or segment["duration"] < 0.35:
        suggested, advice = ["E"], "采样曝光极端或片段过短，优先检查是否应排除。"
    elif detected and len(single) / len(detected) >= 0.5:
        suggested, advice = ["A", "B"], "检测到正面人脸；先核验口部无遮挡、真实面部清晰度和头部运动，再选择口型或无声动作复用。"
    else:
        suggested, advice = ["B", "C", "D", "E"], "视觉类别未知；检查人物、辅助画面、背景可提取性及是否需要排除。未检出人脸不等于没有人物。"
    issues = []
    if not samples:
        issues.append("此候选没有采样帧，无法给出视觉测量。")
    if not detected:
        issues.append("没有可用人脸检测，人物可见性未知。")
    if poor:
        issues.append("全部采样帧亮度接近黑/白；可能是合法纯背景，不能自动判定不可用。")
    if any(len(f["faces"] or []) > 1 for f in samples):
        issues.append("部分采样帧检测到多张人脸，需确认复用对象。")
    group_ids = sorted({g for f in samples for g in groups.get(f["frame_id"], [])})
    if group_ids:
        issues.append("存在相似采样画面，需核验重复利用风险；不能据此认定重复动作或相同场景。")
    primary = [max(f["faces"], key=lambda b: b["width"]*b["height"]) for f in detected if f["faces"]]
    return {**segment, "scene_type": "unknown", "category": None, "suggested_categories": suggested,
            "status": "pending_review", "confidence": None,
            "person_visibility": "unknown", "sampled_face_detection_ratio": visible,
            "face_area_ratio_mean": sum(b["width"]*b["height"] for b in primary)/len(primary) if primary else None,
            "face_center_mean": {"x": sum(b["x"]+b["width"]/2 for b in primary)/len(primary),
                                 "y": sum(b["y"]+b["height"]/2 for b in primary)/len(primary)} if primary else None,
            "reuse_advice": advice, "quality_issues": issues,
            "evidence_frame_ids": [f["frame_id"] for f in samples],
            "similar_visual_group_ids": group_ids,
            "basis": "本地采样帧、人脸矩形、亮度和时长启发式；无语义模型、无口部/表情检测，置信度未知。",
            "manual_checks": {field: {"status": "pending_review", "value": None} for field in REVIEW_FIELDS}}


def visual_groups(sources):
    """Conservative thumbnail similarity candidates, never semantic scene IDs."""
    groups = []
    for source in sources:
        for frame in source.get("frames", []):
            match = next((g for g in groups if sum(abs(a-b) for a, b in
                         zip(g["signature"], frame["image_signature"])) / 256 <= 4), None)
            if match is None:
                match = {"group_id": f"visual-{len(groups):05d}", "signature": frame["image_signature"], "frames": []}
                groups.append(match)
            match["frames"].append({"source_id": source["source_id"], "frame_id": frame["frame_id"], "time": frame["time"]})
    return [{"group_id": g["group_id"], "frames": g["frames"], "status": "pending_review",
             "basis": "16×16 灰度缩略图平均绝对差 ≤ 4/255；仅为相似画面候选，可能漏检或误检。"}
            for g in groups if len(g["frames"]) > 1]


def output_templates(sources):
    groups = defaultdict(list)
    for source in sources:
        if source["status"] == "analyzed":
            m = source["metadata"]
            key = (round(m["display_width"], 2), round(m["display_height"], 2),
                   m["display_aspect_ratio"], m["avg_frame_rate"] or m["nominal_frame_rate"])
            groups[key].append(source)
    templates = []
    for i, (key, members) in enumerate(sorted(groups.items(), key=lambda item: str(item[0]))):
        w, h, dar, fps = key
        templates.append({"template_id": f"template-{i:03d}", "width": max(2, round(w/2)*2),
                          "height": max(2, round(h/2)*2), "display_aspect_ratio": dar,
                          "frame_rate": fps, "source_ids": [s["source_id"] for s in members],
                          "total_duration": sum(s["metadata"]["duration"] for s in members),
                          "pixel_aspect_ratio": "1", "rotation_degrees": 0,
                          "status": "recommended_pending_review",
                          "basis": "按实际显示尺寸/比例和帧率分组；建议正方形像素、烘焙旋转，尺寸取偶数。VFR 需后续独立验证。"})
    for template in templates:
        template["preferred_by_duration"] = template["template_id"] == max(templates, key=lambda t: t["total_duration"])["template_id"]
    return templates


def validate_library(library):
    validate(library, "video-library")
    all_frames = {f["frame_id"] for s in library["sources"] for f in s.get("frames", [])}
    if len({s["source_id"] for s in library["sources"]}) != len(library["sources"]):
        raise CreatorDNAError("资产库来源 ID 重复。")
    for source in library["sources"]:
        if source["status"] != "analyzed":
            continue
        duration = source["metadata"]["duration"]
        local_frames = {f["frame_id"] for f in source["frames"]}
        if any(not 0 <= f["time"] < duration for f in source["frames"]):
            raise CreatorDNAError("关键帧时间超出源视频。")
        previous = 0.0
        shot_map = {s["shot_id"]: s for s in source["shots"]}
        for shot in source["shots"]:
            if abs(shot["start"] - previous) > 1e-6 or not shot["start"] < shot["end"] <= duration:
                raise CreatorDNAError("镜头索引必须连续覆盖视频且不能越界。")
            previous = shot["end"]
        if abs(previous-duration) > 1e-6:
            raise CreatorDNAError("镜头没有覆盖完整视频。")
        for candidate in source["candidates"]:
            shot = shot_map.get(candidate["shot_id"])
            if (not shot or not shot["start"] <= candidate["start"] < candidate["end"] <= shot["end"]
                    or abs(candidate["duration"] - (candidate["end"]-candidate["start"])) > 1e-6
                    or not set(candidate["evidence_frame_ids"]) <= local_frames):
                raise CreatorDNAError("候选片段时间或证据引用无效。")
    if any(f["frame_id"] not in all_frames for g in library["similar_visual_groups"] for f in g["frames"]):
        raise CreatorDNAError("相似画面组引用了不存在的证据帧。")


def report_markdown(library):
    summary = library["summary"]
    lines = ["# CreatorDNA 源视频分析与素材资产库", "",
             f"创作者：{library['creator_id']}；运行：{library['run_id']}", "",
             "本报告仅使用本地工具。所有复用分类、固定场景和视觉语义均待人工审核；候选不等于可直接用于生产。", "",
             "## 原素材的主要视频风格", "",
             f"发现 {summary['video_count']} 个视频，成功 {summary['analyzed_count']}，失败 {summary['failed_count']}；"
             f"成功素材总时长 {summary['total_duration']:.3f} 秒。",
             f"格式分布（包括异常文件）：{summary['format_distribution']}。", "",
             "下表为自动测量的技术特征。人物口播、产品展示等内容风格、人物出镜时长比例、固定场景数量尚不能可靠确认。", "",
             "| 来源 | 编码尺寸 | 显示比例 / 旋转 / SAR | 帧率 | 时长 | 镜头候选数 | 人脸采样检出比例 |", "|---|---|---|---|---|---|---|"]
    for source in library["sources"]:
        name = source["relative_path"].replace("|", "\\|").replace("\n", " ")
        if source["status"] == "failed":
            lines += [f"| {name} | 分析失败 | — | — | — | — | — |"]
            continue
        m = source["metadata"]
        detectable = [f for f in source["frames"] if f["faces"] is not None]
        face_ratio = f"{sum(bool(f['faces']) for f in detectable)/len(detectable):.1%}" if detectable else "未知"
        lines += [f"| {name} | {m['width']}×{m['height']} | {m['display_aspect_ratio'] or m['display_aspect_ratio_value']} / "
                  f"{m['rotation_degrees']}° / {m['sample_aspect_ratio']} | {m['avg_frame_rate'] or m['nominal_frame_rate']} | "
                  f"{m['duration']:.3f}s | {len(source['shots'])} | {face_ratio} |"]
    lines += ["", "## 推荐输出模板", "",
              "不同素材分组保留，按总时长给出首选组；不会假设尺寸/比例/帧率一致。模板只是后续技术验证建议。", ""]
    for t in library["output_templates"]:
        lines += [f"- {t['template_id']}：{t['width']}×{t['height']}，{t['frame_rate']} fps，"
                  f"覆盖 {len(t['source_ids'])} 个来源，{t['total_duration']:.3f}s"
                  + ("，按时长首选。" if t["preferred_by_duration"] else "。")]
    lines += ["", "## 适合复用的人像、背景和 B-roll 候选", "",
              "A=口型同步人像，B=无声动作/过渡，C=B-roll，D=背景/场景，E=不适合复用。"
              "只有建议类别，已确认 category 均为空，置信度未知。人脸框面积不等于人物出镜比例。", "",
              "| 来源 / 候选 ID | 起止秒 | 建议类别 | 复用建议 / 质量问题 |", "|---|---|---|---|"]
    for source in library["sources"]:
        for c in source.get("candidates", []):
            text = (c["reuse_advice"] + " " + " ".join(c["quality_issues"])).replace("|", "\\|")
            lines += [f"| {source['relative_path'].replace('|', ' / ')} / {c['candidate_id']} | "
                      f"{c['start']:.3f}–{c['end']:.3f} | {','.join(c['suggested_categories'])}，待审核 | {text} |"]
    lines += ["", "## 主要固定场景、构图与重复动作风险", "",
              f"固定场景数量：未知。相似采样画面组：{len(library['similar_visual_groups'])}；这些是灰度缩略图相似候选，"
              "不能视为固定场景统计或重复动作识别。",
              "人脸中心和面积可在 JSON 查看；身体构图、头部运动、表情和动作连续性仍需播放原片确认。"
              "frame_change_proxy 是两张采样帧的像素差，不是头部运动测量。", ""]
    if summary["duplicate_source_groups"]:
        lines += [f"- 检测到完全相同的文件内容：{summary['duplicate_source_groups']}；不可当作独立素材。"]
    for group in library["similar_visual_groups"]:
        refs = ", ".join(f"{f['source_id']}@{f['time']:.3f}s" for f in group["frames"][:12])
        lines += [f"- {group['group_id']}：{refs}" + ("（更多引用见 JSON）" if len(group["frames"]) > 12 else "")]
    lines += ["", "## 关键帧与联系图", ""]
    for source in library["sources"]:
        for contact in source.get("contacts", []):
            lines += [f"- {source['relative_path'].replace('|', ' / ')}：[联系图]({contact})"]
    lines += ["", "## 人工检查清单", "",
              "- 对照联系图并播放原片，确认镜头边界、主要固定场景及身体构图；动作/闪光可能导致切镜误报，渐变可能漏检。",
              "- 逐个人像候选核验嘴部无遮挡、牙齿/口型清晰度、侧脸角度、头部运动、表情与手势连续性。",
              "- 确认字幕位置、字体/颜色/描边和人物遮挡；字幕流数量不代表烧录字幕是否存在。",
              "- 将产品、截图、B-roll、背景人工分类；背景提取须检查人物遮挡和是否存在可恢复区域。",
              "- 相似画面不证明相同场景；重复动作和前后镜头匹配须观看候选及其上下文。",
              "- 阅读 review-template.json，按候选 ID 记录审核类别、场景及证据；该文件是审核工作表，自动资产清单保留原始测量。", "",
              "## 人像复用技术建议与下一步验证", "",
              "1. 从 A 建议中人工选取短、正面、清晰、嘴部无遮挡且头动小的片段，先在授权本地环境验证源视频口型重定向。",
              "2. 保留真实人物、机位和场景，对比口部局部重绘与整脸驱动的身份保持、牙齿、边缘闪烁和时间一致性；本阶段未接入模型。",
              "3. B 类优先用于无声过渡；C 类遮蔽必要剪辑点；D 类只在背景真实性与可提取性确认后复用。",
              "4. 对少量片段验证时长扩展、动作重复、头部/镜头匹配，再独立设计声音、字幕和剪辑模块。任何第三方人像/声音上传均需另行明确授权。", "",
              "## 分析局限和异常", "",
              "- 局部采样可能漏掉瞬间遮挡；人脸检测存在误报/漏报，不识别人脸身份，不代表创作者本人。",
              "- 人脸清晰度为缩放采样帧上的 Laplacian 方差代理，不提供跨素材可比的质量通过阈值。",
              "- 平均/标称帧率不同仅是 VFR 提示，不是完整逐帧时间间隔检测。无音频时音频数组为空。",
              "- 本阶段不提供 TTS、口型同步、字幕 OCR、背景抠像或视频合成。"]
    for source in library["sources"]:
        if source["status"] == "failed":
            lines += [f"- {source['relative_path']}：{source['error'].replace(chr(10), ' ')[:500]}"]
        elif source["sampling_limited"]:
            lines += [f"- {source['relative_path']}：达到采样上限，已降采样；未覆盖片段仍保持未知。"]
    return "\n".join(lines) + "\n"


class VideoAssetWorkflow:
    def __init__(self, workspace, analyzer=None, progress_callback=None):
        self.workspace = workspace
        self.analyzer = analyzer or LocalVideoAnalyzer()
        self.progress_callback = progress_callback

    def report(self, message, current_object=None, completed=None, total=None, unit=None, basis="local analysis status"):
        if self.progress_callback:
            self.progress_callback(message, current_object, completed, total, unit, basis)

    def analyze(self, input_folder, creator_id=None, recursive=False, *, run_directory=None, publish_latest=True):
        folder, paths = scan_videos(input_folder, self.workspace.root, recursive)
        creator_id = validate_id(creator_id) if creator_id else default_id(folder)
        if hasattr(self.analyzer, "dependencies"):
            self.analyzer.dependencies()
        with self.workspace.lock(creator_id):
            if run_directory is None:
                run = self.workspace.new_run(creator_id, "video-analysis")
            else:
                # Task workers supply a short, unique attempt-local directory on Windows.
                run = Path(run_directory).resolve()
                if self.workspace.root not in run.parents or run.exists() or publish_latest:
                    raise CreatorDNAError("独立分析目录须为 workspace 内的新目录，且不能发布 latest。")
                run.mkdir(parents=True)
                write_json(run / "run.json", {"schema_version": "1.0", "task": "video-analysis",
                           "creator_id": creator_id, "status": "running", "started_at": now()})
            library = {"schema_version": "3.0-01", "creator_id": creator_id, "run_id": run.name,
                       "created_at": now(), "input_folder": str(folder), "status": "running",
                       "analysis_provider": "local", "sources": [], "output_templates": [],
                       "similar_visual_groups": [], "summary": {}}
            config_keys = ["scene_threshold", "sample_interval", "max_frames", "segment_seconds", "timeout", "face_detection"]
            write_json(run / "analysis-config.json", {k: getattr(self.analyzer, k, None) for k in config_keys})
            try:
                for index, path in enumerate(paths):
                    self.report(f"分析素材 {index+1}/{len(paths)}", path.relative_to(folder).as_posix(),
                                index, len(paths), "sources", "processed sources, including failed sources")
                    entry = {"relative_path": path.relative_to(folder).as_posix(), "path": str(path),
                             "source_id": "", "sha256": None, "status": "failed", "error": None}
                    try:
                        source = video_source(path, folder)
                        entry.update(source_id=source.source_id, sha256=source.sha256)
                        data = self.analyzer.analyze(source, run / "evidence" / source.source_id)
                        entry.update(data, status="analyzed")
                        prefix = f"evidence/{source.source_id}/"
                        for frame in entry["frames"]:
                            frame["path"] = prefix + frame["path"]
                        entry["contacts"] = [prefix+p for p in entry["contacts"]]
                    except Exception as exc:
                        entry.update(status="failed", error=str(exc))
                        # No partly computed asset is advertised as analyzed.
                        entry = {k: entry[k] for k in ("relative_path", "path", "source_id", "sha256", "status", "error")}
                    if not entry["source_id"]:
                        import hashlib
                        entry["source_id"] = "s-" + hashlib.sha256(entry["relative_path"].encode()).hexdigest()[:16]
                    library["sources"].append(entry)
                    write_json(run / "partial-library.json", library)
                    self.report(f"素材处理结束：{entry['status']}", entry["relative_path"], index+1,
                                len(paths), "sources", "processed sources, including failed sources")
                self.report("汇总、校验资产库并写入报告", basis="aggregation and validation; no measurable total")
                library["similar_visual_groups"] = visual_groups(library["sources"])
                frame_groups = defaultdict(list)
                for group in library["similar_visual_groups"]:
                    for frame in group["frames"]:
                        frame_groups[frame["frame_id"]].append(group["group_id"])
                for source in library["sources"]:
                    if source["status"] == "analyzed":
                        source["candidates"] = [classify(s, source["frames"], frame_groups) for s in source.pop("segments")]
                library["output_templates"] = output_templates(library["sources"])
                hashes = defaultdict(list)
                for source in library["sources"]:
                    if source["sha256"]:
                        hashes[source["sha256"]].append(source["source_id"])
                good = [s for s in library["sources"] if s["status"] == "analyzed"]
                library["summary"] = {"video_count": len(paths), "analyzed_count": len(good),
                    "failed_count": len(paths)-len(good), "total_duration": sum(s["metadata"]["duration"] for s in good),
                    "format_distribution": dict(Counter(p.suffix.lower() for p in paths)),
                    "duplicate_source_groups": [ids for ids in hashes.values() if len(ids) > 1],
                    "candidate_count": sum(len(s["candidates"]) for s in good), "fixed_scene_count": None}
                status = "completed" if len(good) == len(paths) else "partial" if good else "failed"
                library["status"] = status
                validate_library(library)
                write_json(run / "library.json", library)
                write_text(run / "report.md", report_markdown(library))
                write_json(run / "review-template.json", {"schema_version": "3.0-01", "run_id": run.name,
                    "note": "人工审核工作表；编辑此文件不会覆盖原始自动测量，也不会自动将候选变为生产可用。",
                    "candidates": [{"source_id": s["source_id"], "candidate_id": c["candidate_id"],
                                    "start": c["start"], "end": c["end"], "category": None, "scene_id": None,
                                    "reviewer": None, "evidence": None, "checks": c["manual_checks"]}
                                   for s in good for c in s["candidates"]]})
                # Only a validated, usable run replaces the latest library pointer.
                if good and publish_latest:
                    write_json(self.workspace.creator(creator_id) / "video-assets/latest.json",
                               {"schema_version": "3.0-01", "run_id": run.name,
                                "library": f"runs/{run.name}/library.json", "status": status})
                self.workspace.finish(run, status, summary=library["summary"])
                return {"creator_id": creator_id, "run_id": run.name, "run": str(run), "status": status,
                        "library": str(run / "library.json"), "report": str(run / "report.md"),
                        "review": str(run / "review-template.json"), "summary": library["summary"]}
            except (Exception, KeyboardInterrupt) as exc:
                self.workspace.finish(run, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                                      error=str(exc) or "用户中断")
                raise
