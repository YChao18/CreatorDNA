"""Working local adapters; demo media is explicitly synthetic, not a pipeline."""

import json
import math
import subprocess
import time
from pathlib import Path

from .storage import Workspace, digest_file, read_json, write_json
from .task_progress import FFmpegProgressParser
from .task_worker import StageFailure


def local_chunks(ctx, params):
    """Write actual chunks and hash them; delay only paces the observable demo."""
    count, delay = params.get("chunks", 12), params.get("delay", 0.1)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10000:
        raise StageFailure("chunks 须为 1–10000 的整数", "InvalidParameter")
    if not isinstance(delay, (int, float)) or not math.isfinite(delay) or not 0 <= delay <= 10:
        raise StageFailure("delay 须为 0–10 秒", "InvalidParameter")
    if ctx.attempt <= params.get("fail_until", 0):
        (ctx.output_dir / "unpublished.txt").write_text("synthetic failed attempt", encoding="utf-8")
        raise StageFailure("合成工程任务的可重试失败", "SyntheticTransientError", True)
    entries = []
    ctx.report("本地工程演示：写入并校验分块（不代表素材理解）", completed=0, total=count,
               unit="chunks", basis="written and hashed chunks")
    for i in range(count):
        ctx.check_cancelled()
        path = ctx.output_dir / f"chunk-{i:04d}.bin"
        path.write_bytes((f"CreatorDNA synthetic chunk {i}\n".encode())*1024)
        entries.append({"name": path.name, "sha256": digest_file(path)})
        ctx.report(f"已写入并校验 {i+1}/{count} 个分块", path.name, i+1, count, "chunks",
                   "written and hashed chunks")
        if delay:
            time.sleep(delay)
    write_json(ctx.output_dir / "manifest.json", {"synthetic_only": True, "entries": entries})
    return {"synthetic_only": True, "chunks": count}


def check_chunks(ctx, params):
    artifacts = ctx.inputs["material_analysis"]["artifacts"]
    manifest = next(Path(a["path"]) for a in artifacts if Path(a["path"]).name == "manifest.json")
    entries = read_json(manifest)["entries"]
    for i, entry in enumerate(entries):
        if digest_file(manifest.parent / entry["name"]) != entry["sha256"]:
            raise StageFailure("分块哈希不一致", "IntegrityError")
        ctx.report(f"核验分块 {i+1}/{len(entries)}", entry["name"], i+1, len(entries), "chunks", "verified chunk hashes")
    write_json(ctx.output_dir / "verification.json", {"synthetic_only": True, "verified_chunks": len(entries)})
    return {"verified_chunks": len(entries), "synthetic_only": True}


def run_ffmpeg(ctx, command, target_duration=None, *, cwd=None, log_name="ffmpeg"):
    """Native progress, stdout drained live; stderr saved without pipe deadlock.

    Parent applies stage timeout and cancellation to worker + subprocess tree.
    Calling modules must provide a command whose outputs stay in output_dir.
    """
    parser = FFmpegProgressParser(target_duration)
    ctx.log("启动 FFmpeg", command=command, target_duration=target_duration)
    log_path = ctx.attempt_dir / f"{log_name}.stderr.log"
    with log_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen([command[0], "-hide_banner", "-nostdin", "-nostats",
                                    "-stats_period", "0.1", "-progress", "pipe:1", *command[1:]],
                                   stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8", errors="replace", cwd=cwd)
        try:
            for line in process.stdout:
                ctx.check_cancelled()
                p = parser.feed(line)
                if p:
                    ctx.report(f"FFmpeg 已输出 {parser.seconds:.3f} 秒媒体", "FFmpeg", p["completed"],
                               p["total"], p["unit"], p["basis"])
            code = process.wait()
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    if code:
        detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise StageFailure(f"FFmpeg 退出码 {code}：{detail}", "FFmpegError")
    return {"processed_seconds": parser.seconds, "target_duration": target_duration}


def ffmpeg_demo(ctx, params):
    duration = params.get("duration", 3)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0.1 <= duration <= 60:
        raise StageFailure("演示时长须为 0.1–60 秒", "InvalidParameter")
    output = ctx.output_dir / "synthetic-demo.mp4"
    ctx.report("合成测试图渲染；无真实创作者媒体", output.name, 0, duration, "media_seconds", "FFmpeg out_time / target duration")
    measured = run_ffmpeg(ctx, [params.get("ffmpeg", "ffmpeg"), "-v", "error", "-n",
        "-re", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={duration}",
        "-an", "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p", str(output)], duration)
    if not output.is_file() or output.stat().st_size == 0:
        raise StageFailure("FFmpeg 返回成功但没有生成有效输出文件", "InvalidArtifact")
    return {"synthetic_only": True, **measured}


def check_video(ctx, params):
    output = next(Path(a["path"]) for a in ctx.inputs["render"]["artifacts"] if a["path"].endswith(".mp4"))
    ctx.report("检查视频元数据与完整解码", output.name, basis="verification in progress; no measurable total")
    raw = subprocess.run([params.get("ffprobe", "ffprobe"), "-v", "error", "-show_streams", "-show_format",
                          "-of", "json", str(output)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    data = json.loads(raw.stdout)
    duration = float(data["format"]["duration"])
    expected = ctx.inputs["render"]["result"]["target_duration"]
    if not math.isfinite(duration) or abs(duration-expected) > 0.1:
        raise StageFailure("成片时长与目标不一致", "IntegrityError")
    run_ffmpeg(ctx, [params.get("ffmpeg", "ffmpeg"), "-v", "error", "-xerror", "-i", str(output),
                    "-map", "0:v:0", "-an", "-f", "null", "-"], duration)
    write_json(ctx.output_dir / "verification.json", {"synthetic_only": True, "duration": duration,
                                                      "sha256": digest_file(output), "decode_ok": True})
    return {"duration": duration, "decode_ok": True, "synthetic_only": True}


def analyze_video(ctx, params):
    """Reuse V3.0-01 in an isolated workspace; never replace its latest pointer."""
    from .video import LocalVideoAnalyzer, scan_videos, video_source
    from .video_assets import VideoAssetWorkflow

    def report(message, current_object=None, completed=None, total=None, unit=None, basis="provider status"):
        # File/frame counts have reliable local denominators, but they exclude
        # decoding, hashing and aggregation; don't call these stage percentages.
        if total is not None:
            message += f"（当前子操作 {completed:g}/{total:g} {unit}）"
        ctx.report(message, current_object, completed, None, unit, basis + "; whole stage work total unknown")

    analyzer = LocalVideoAnalyzer(**params.get("analyzer", {}), progress_callback=report)
    workspace = Workspace(ctx.output_dir)
    folder, paths = scan_videos(params["input"], workspace.root, params.get("recursive", False))
    snapshot = {m["path"]: m["sha256"] for m in ctx.materials}
    current = {str(path): video_source(path, folder).sha256 for path in paths}
    if current != snapshot:
        raise StageFailure("素材列表或内容与创建任务时的快照不同，请创建新任务", "SourceChanged")
    result = VideoAssetWorkflow(workspace, analyzer, progress_callback=report).analyze(
        params["input"], ctx.creator_id, params.get("recursive", False),
        run_directory=ctx.output_dir / "analysis", publish_latest=False)
    if result["status"] == "failed":
        raise StageFailure("所有素材分析失败；查看本尝试的 report.md 和工具日志", "AnalysisFailed")
    if result["status"] == "partial":
        ctx.log("部分素材分析失败；阶段输出保留明确的 partial 状态和每个失败来源", "warning", summary=result["summary"])
    if any(digest_file(path) != snapshot[str(path)] for path in paths):
        raise StageFailure("分析期间素材发生变化，拒绝发布任务输出", "SourceChanged")
    # Paths in metadata must remain relative to output_dir for portable checkpoints.
    return {"analysis_status": result["status"], "summary": result["summary"],
            "library": Path(result["library"]).relative_to(ctx.output_dir).as_posix(),
            "report": Path(result["report"]).relative_to(ctx.output_dir).as_posix()}
