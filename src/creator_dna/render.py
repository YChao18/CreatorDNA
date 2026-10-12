"""Deterministic CFR compositor. No speech, portrait or semantic inference here."""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path

from .errors import CreatorDNAError
from .storage import digest_file, read_json, write_json, write_text
from .subtitles import write_ass
from .task_executors import run_ffmpeg
from .task_worker import StageFailure
from .timeline import validate_timeline


DEFAULTS = {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "segment_seconds": 20,
            "crf": 18, "preset": "medium", "threads": 2, "audio_mix": []}


def settings(value=None):
    result = {**DEFAULTS, **(value or {})}
    if set(result) != set(DEFAULTS):
        raise CreatorDNAError("未知渲染配置字段。")
    for key, lo, hi in (("segment_seconds", .1, 120), ("crf", 0, 51), ("threads", 1, 64)):
        n = result[key]
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not lo <= n <= hi:
            raise CreatorDNAError(f"无效渲染参数：{key}")
    if not isinstance(result["threads"], int) or not isinstance(result["crf"], int):
        raise CreatorDNAError("threads/crf 必须为整数。")
    if result["preset"] not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
        raise CreatorDNAError("无效 x264 preset。")
    for key in ("ffmpeg", "ffprobe"):
        if not isinstance(result[key], str) or not shutil.which(result[key]):
            raise CreatorDNAError(f"找不到 {key}：{result[key]}")
        result[key] = shutil.which(result[key])
    if not isinstance(result["audio_mix"], list):
        raise CreatorDNAError("audio_mix 必须为音轨列表。")
    for track in result["audio_mix"]:
        if not isinstance(track, dict) or set(track) != {"path", "sha256", "start", "end", "source_start", "volume", "rights", "rights_note"}:
            raise CreatorDNAError("附加音轨字段不完整。")
        from .production_assets import USABLE_RIGHTS
        if track["rights"] not in USABLE_RIGHTS or not isinstance(track["rights_note"], str) or not track["rights_note"].strip():
            raise CreatorDNAError("附加音轨缺少授权依据。")
        for key in ("start", "end", "source_start", "volume"):
            if isinstance(track[key], bool) or not isinstance(track[key], (int, float)) or not math.isfinite(track[key]) or track[key] < 0:
                raise CreatorDNAError("附加音轨时间/音量无效。")
        if track["end"] <= track["start"] or track["volume"] > 2:
            raise CreatorDNAError("附加音轨时间范围/音量无效。")
        source = Path(track["path"]).resolve()
        if not source.is_file() or digest_file(source) != track["sha256"]:
            raise CreatorDNAError("附加音轨文件缺失或哈希改变。")
        measured = probe(source, result["ffprobe"])
        if not any(s["codec_type"] == "audio" for s in measured["streams"]) or track["source_start"]+track["end"]-track["start"] > float(measured["format"]["duration"])+.001:
            raise CreatorDNAError("附加音轨不足或缺少音频流。")
        track["path"] = str(source)
    return result


def frame_segments(timeline, seconds):
    """Choose active clips at frame midpoints on ONE global CFR grid."""
    fps = float(Fraction(timeline["output"]["frame_rate"]))
    count = max(1, math.ceil(timeline["duration"]*fps-1e-9))
    edges = {0, count}
    for clip in timeline["foreground"]+timeline["background"]:
        edges.update(max(0, min(count, math.ceil(t*fps-.5-1e-9))) for t in (clip["start"], clip["end"]))
    ordered = sorted(edges)
    stride = max(1, int(seconds*fps))
    result = []
    for first, last in zip(ordered, ordered[1:]):
        for start in range(first, last, stride):
            end = min(last, start+stride)
            midpoint = min((start+.5)/fps, timeline["duration"]-1e-9)
            active = lambda layer: next((c for c in timeline[layer] if c["start"] <= midpoint < c["end"]), None)
            result.append({"first": start, "last": end, "start": start/fps,
                           "duration": (end-start)/fps, "foreground": active("foreground"), "background": active("background")})
    if any(s["foreground"] is None for s in result):
        raise CreatorDNAError("输出帧存在前景时间轴缺口。")
    return result


def geometry(layout, output, preserve_alpha=False):
    w, h = output["width"], output["height"]
    r = layout["rect"]
    # Integer rectangles stay inside canvas; scale preserves display aspect.
    x, y = round(r["x"]*w), round(r["y"]*h)
    rw, rh = min(w-x, max(1, round(r["width"]*w))), min(h-y, max(1, round(r["height"]*h)))
    filters = ["scale=w='max(1,round(iw*sar))':h=ih", "setsar=1", "format=rgba"]
    c = layout["crop"]
    if c:
        filters.append(f"crop=w=iw*{c['width']}:h=ih*{c['height']}:x=iw*{c['x']}:y=ih*{c['y']}")
    anchor = layout["anchor"]
    offset = "0" if anchor == "left" else "iw-ow" if anchor == "right" else "(iw-ow)/2"
    pad = "0" if anchor == "left" else "ow-iw" if anchor == "right" else "(ow-iw)/2"
    if layout["fit"] == "cover":
        filters.extend([f"scale={rw}:{rh}:force_original_aspect_ratio=increase", f"crop={rw}:{rh}:x={offset}:y=(ih-oh)/2"])
    else:
        color = "0x00000000" if preserve_alpha else layout["pad_color"]
        # Force RGBA after scaling too: otherwise overlay can negotiate 4:2:0
        # upstream and round an odd inset width below the scaled input width.
        filters.extend([f"scale={rw}:{rh}:force_original_aspect_ratio=decrease", "format=rgba",
                        f"pad={rw}:{rh}:x={pad}:y=(oh-ih)/2:color={color}"])
    return filters, x, y


def artifact(ctx, stage_id, name):
    return next(Path(a["path"]) for a in ctx.inputs[stage_id]["artifacts"] if Path(a["path"]).name == name)


def render_stage(ctx, params):
    cfg = settings(params.get("render"))
    path = Path(params["timeline"]) if params.get("timeline") else artifact(ctx, "composition", "timeline.json")
    timeline = read_json(path)
    base = path.resolve().parent
    report = validate_timeline(timeline, base, ffprobe=cfg["ffprobe"])
    write_json(ctx.output_dir / "input-validation.json", report)
    if not report["ready_for_render"]:
        raise StageFailure("时间轴不可渲染："+"；".join(report["issues"]), "InvalidTimeline")
    segments = frame_segments(timeline, cfg["segment_seconds"])
    assets = {a["asset_id"]: a for a in timeline["assets"]}
    cache = Path(params["workspace"]) / "render-cache"
    cache.mkdir(parents=True, exist_ok=True)
    output, fps = timeline["output"], timeline["output"]["frame_rate"]
    paths, records = [], []
    for index, segment in enumerate(segments):
        ctx.check_cancelled()
        cues = [c for c in timeline["subtitles"]["cues"] if c["end"] > segment["start"] and c["start"] < segment["start"]+segment["duration"]]
        identity = {"renderer": "3.0-06.4", "segment": segment, "output": output, "settings": {k:v for k,v in cfg.items() if k != "audio_mix"},
                    "assets": [assets[c["asset_id"]]["sha256"] for c in (segment["background"], segment["foreground"]) if c],
                    "cues": cues, "style": timeline["subtitles"]["style"]}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cached, receipt = cache / (key+".mp4"), cache / (key+".json")
        reused = receipt.is_file() and cached.is_file() and read_json(receipt).get("sha256") == digest_file(cached)
        ctx.report(f"片段 {index+1}/{len(segments)}；已完成 {index} 段；{'复用缓存' if reused else '开始渲染'}",
                   cached.name, index, len(segments), "segments", "verified rendered segments")
        if not reused:
            lock = cache / (key+".lock")
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise StageFailure("该片段缓存正在写入或有中断锁；停止任务后 video clean。", "RenderCacheBusy", True) from None
            os.close(fd)
            temp = cache / (key+"."+uuid.uuid4().hex+".tmp.mp4")
            try:
                ass = ctx.attempt_dir / f"segment-{index:05d}.ass"
                write_ass(ass, cues, output, timeline["subtitles"]["style"])
                args = [cfg["ffmpeg"], "-v", "error", "-n", "-filter_complex_threads", "1",
                        "-f", "lavfi", "-i", f"color=c={segment['foreground']['layout']['pad_color']}:s={output['width']}x{output['height']}:r={fps}:d={segment['duration']}"]
                filters, previous, n = [], "0:v", 1
                for clip in (segment["background"], segment["foreground"]):
                    if not clip:
                        continue
                    a = assets[clip["asset_id"]]
                    source = (base / a["path"]).resolve()
                    if a["media_type"] == "image":
                        from PIL import Image, ImageOps
                        normalized = ctx.attempt_dir / f"image-{index}-{n}.png"
                        with Image.open(source) as im:
                            ImageOps.exif_transpose(im).save(normalized)
                        args += ["-loop", "1", "-framerate", fps, "-i", str(normalized)]
                    else:
                        offset = max(clip["source_start"], clip["source_start"]+segment["start"]-clip["start"])
                        args += ["-ss", str(offset), "-i", str(source)]
                    geo, x, y = geometry(clip["layout"], output, preserve_alpha=clip["role"] == "portrait" and bool(a.get("portrait_provenance")))
                    # CFR endpoint resampling may need one final frame after fps
                    # rounding; the validated source interval still owns coverage.
                    geo = ["setpts=PTS-STARTPTS", f"fps={fps}", f"tpad=stop_mode=clone:stop_duration={1/float(Fraction(fps))}", *geo]
                    if clip["transition"]["type"] == "fade":
                        geo += [f"setpts=PTS+{segment['start']}/TB", f"fade=t=in:st={clip['start']}:d={clip['transition']['duration']}:color={clip['layout']['pad_color']}", f"setpts=PTS-{segment['start']}/TB"]
                    filters += [f"[{n}:v]"+",".join(geo)+f"[layer{n}]",
                                f"[{previous}][layer{n}]overlay=x={x}:y={y}:shortest=1:eof_action=endall[canvas{n}]"]
                    previous = f"canvas{n}"
                    n += 1
                filters.append(f"[{previous}]setpts=PTS+{segment['start']}/TB,ass=filename={ass.name},setpts=PTS-{segment['start']}/TB,format=yuv420p,setsar=1[out]")
                args += ["-filter_complex", ";".join(filters), "-map", "[out]", "-an", "-frames:v", str(segment["last"]-segment["first"]),
                         "-r", fps, "-c:v", "libx264", "-crf", str(cfg["crf"]), "-preset", cfg["preset"], "-threads", str(cfg["threads"]), str(temp)]
                run_ffmpeg(ctx, args, segment["duration"], cwd=ctx.attempt_dir, log_name=f"segment-{index:05d}")
                meta = probe(temp, cfg["ffprobe"], count=True)
                video = next(s for s in meta["streams"] if s["codec_type"] == "video")
                if int(video["nb_read_frames"]) != segment["last"]-segment["first"]:
                    raise StageFailure("渲染片段帧数不足；拒绝填充、循环或留下时间轴缺口", "SegmentFramesMismatch")
                os.replace(temp, cached)
                write_json(receipt, {"sha256": digest_file(cached), "identity": identity})
            finally:
                temp.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)
        paths.append(cached)
        records.append({"index": index, "key": key, "reused": reused, "frames": segment["last"]-segment["first"], "start": segment["start"]})
        ctx.report(f"已完成片段 {index+1}/{len(segments)}", cached.name, index+1, len(segments), "segments", "verified rendered segments")
        write_json(ctx.output_dir / "segments.json", records)
    # Local safe ASCII names avoid concat quoting arbitrary source paths.
    for i, p in enumerate(paths):
        target = ctx.attempt_dir / f"part-{i:05d}.mp4"
        try:
            os.link(p, target)
        except OSError:
            shutil.copyfile(p, target)
    concat = ctx.attempt_dir / "concat.txt"
    write_text(concat, "".join(f"file 'part-{i:05d}.mp4'\n" for i in range(len(paths))))
    candidate = ctx.output_dir / "candidate.mp4"
    mux = [cfg["ffmpeg"], "-v", "error", "-n", "-f", "concat", "-safe", "1", "-i", concat.name,
           "-i", str((base/timeline["audio"]["path"]).resolve())]
    audio_filters = [f"[1:a]atrim=start={timeline['audio']['source_start']}:duration={timeline['duration']},asetpts=PTS-STARTPTS[main]"]
    labels = ["[main]"]
    for i, track in enumerate(cfg["audio_mix"], 2):
        if track["end"] > timeline["duration"]:
            raise CreatorDNAError("附加音轨超出时间轴。")
        mux += ["-i", track["path"]]
        audio_filters.append(f"[{i}:a:0]atrim=start={track['source_start']}:duration={track['end']-track['start']},asetpts=PTS-STARTPTS,volume={track['volume']},adelay={round(track['start']*1000)}:all=1[mix{i}]")
        labels.append(f"[mix{i}]")
    audio_filters.append("".join(labels)+f"amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1[audio]")
    mux += ["-filter_complex", ";".join(audio_filters), "-map", "0:v:0", "-map", "[audio]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-t", str(timeline["duration"]), "-movflags", "+faststart", str(candidate)]
    run_ffmpeg(ctx, mux,
               timeline["duration"], cwd=ctx.attempt_dir, log_name="mux")
    # Check inputs again to reject edits during rendering.
    after = validate_timeline(timeline, base, ffprobe=cfg["ffprobe"])
    settings(cfg)  # Recheck additional audio hashes after mux.
    if not after["ready_for_render"] or read_json(path) != timeline:
        raise StageFailure("渲染期间输入发生变化。", "InputChanged")
    write_json(ctx.output_dir / "render.json", {"timeline": str(path.resolve()), "timeline_sha256": digest_file(path),
               "video_sha256": digest_file(candidate), "segments": len(segments), "reused_segments": sum(r["reused"] for r in records)})
    for p in ctx.attempt_dir.glob("part-*.mp4"):
        p.unlink()
    return {"candidate": "candidate.mp4", "timeline": str(path.resolve()), "segments": len(segments), "human_review": "pending"}


def probe(path, ffprobe, count=False):
    command = [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json"]
    if count:
        command += ["-count_frames"]
    result = subprocess.run([*command, str(path)], capture_output=True, timeout=180, check=True)
    return json.loads(result.stdout)


def verify_stage(ctx, params):
    cfg = settings(params.get("render"))
    candidate = artifact(ctx, "render", "candidate.mp4")
    receipt = read_json(artifact(ctx, "render", "render.json"))
    path = Path(receipt["timeline"])
    timeline = read_json(path)
    report = {"schema_version": "3.0-06", "automatic_checks": "failed", "human_review": "pending", "issues": [],
              "human_checks": ["人物外貌稳定", "声音自然", "口型同步", "重复动作", "B-roll 语义", "字幕清晰和字体", "接近参考视频风格"]}
    issues = report["issues"]
    if digest_file(candidate) != receipt["video_sha256"] or digest_file(path) != receipt["timeline_sha256"]:
        issues.append("渲染输出或时间轴哈希改变")
    validation = validate_timeline(timeline, path.parent, ffprobe=cfg["ffprobe"])
    issues.extend(validation["issues"])
    try:
        meta = probe(candidate, cfg["ffprobe"], count=True)
        v = [s for s in meta["streams"] if s["codec_type"] == "video"]
        a = [s for s in meta["streams"] if s["codec_type"] == "audio"]
        if len(v) != 1 or len(a) != 1:
            raise ValueError("需要恰好一个视频轨和一个音频轨")
        v, a = v[0], a[0]
        fps = float(Fraction(timeline["output"]["frame_rate"]))
        expected = timeline["duration"]
        if (v["width"], v["height"]) != (timeline["output"]["width"], timeline["output"]["height"]):
            issues.append("输出分辨率/画幅错误")
        if abs(float(Fraction(v["avg_frame_rate"]))-fps) > .001 or v.get("sample_aspect_ratio") != "1:1":
            issues.append("输出帧率/像素比例错误")
        if any(abs(float(s.get("rotation", 0))) > .01 for s in v.get("side_data_list", [])) or float(v.get("tags", {}).get("rotate", 0)) != 0:
            issues.append("输出存在意外旋转")
        if (abs(float(v["duration"])-expected) > 1/fps+.03 or abs(float(a["duration"])-expected) > .06 or
                abs(float(v.get("start_time", 0))) > .05 or abs(float(a.get("start_time", 0))) > .05 or
                abs(int(v["nb_read_frames"])-math.ceil(expected*fps-1e-9)) > 1):
            issues.append("时长、帧数或音视频起始时间不符")
        run_ffmpeg(ctx, [cfg["ffmpeg"], "-v", "info", "-xerror", "-i", str(candidate), "-map", "0:v:0", "-map", "0:a:0",
                        "-vf", "blackdetect=d=0.25:pix_th=0.10:pic_th=0.98", "-f", "null", "-"], expected, log_name="decode-blackdetect")
        text = (ctx.attempt_dir/"decode-blackdetect.stderr.log").read_text(encoding="utf-8", errors="replace")
        intervals = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", text)]
        fades = [(c["start"], c["start"]+c["transition"]["duration"]) for c in timeline["foreground"] if c["transition"]["type"] == "fade"]
        if any(not any(s >= fs-1/fps and e <= fe+1/fps for fs, fe in fades) for s, e in intervals):
            issues.append("检测到超过 0.25 秒的非转场近黑画面；需人工确认后修订素材")
        report.update(decode_ok=True, streams=meta["streams"], black_intervals=intervals, duration=float(meta["format"]["duration"]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, CreatorDNAError) as exc:
        issues.append(str(exc))
    if not issues:
        report["automatic_checks"] = "passed"
    write_json(ctx.output_dir / "verification.json", report)
    write_text(ctx.output_dir / "human-review.md", "# 成片人工验收（pending）\n\n自动检查不证明风格、声音或口型质量。\n\n"+
               "\n".join("- [ ] "+c+"：记录时间位置与结论" for c in report["human_checks"])+"\n")
    if issues:
        raise StageFailure("成品检查失败："+"；".join(issues), "FinalIntegrityError")
    final = ctx.output_dir / "final.mp4"
    with final.open("xb") as out, candidate.open("rb") as source:
        shutil.copyfileobj(source, out)
    report.update(final=str(final), sha256=digest_file(final))
    write_json(ctx.output_dir / "verification.json", report)
    return {"video": str(final), "automatic_checks": "passed", "human_review": "pending", "verification": "verification.json"}
