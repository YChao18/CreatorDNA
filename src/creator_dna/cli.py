import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .agents import CodexAdapter, codex_command
from .errors import CreatorDNAError
from .configuration import load_environment
from .media import MediaProcessor, WhisperTranscriber
from .skills import SkillManager
from .storage import Workspace, read_json, write_json
from .workflow import Workflow
from .quality import QualityWorkflow
from .evaluation import DEFAULT_CONFIG
from .video import LocalVideoAnalyzer
from .video_assets import VideoAssetWorkflow
from . import task_cli, tts_cli, lip_sync_cli, production_cli, video_cli


def parser():
    root = argparse.ArgumentParser(prog="creator-dna", description="CreatorDNA：素材文件夹 → 风格 Skill / 视频资产 → 创作与质量闭环")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    def workspace(command):
        command.add_argument("--workspace", type=Path, default=Path("workspace"), help="运行数据目录（默认 ./workspace）")

    def inference(command):
        command.add_argument("--codex-bin", default="codex")
        command.add_argument("--model", help="可选，默认沿用 Codex CLI 配置")
        command.add_argument("--timeout", type=int, default=900)
        command.add_argument("--max-input-chars", type=int, default=240000)

    task_cli.add_commands(commands, workspace)
    tts_cli.add_commands(commands, workspace)
    lip_sync_cli.add_commands(commands, workspace)
    production_cli.add_commands(commands, workspace, inference)
    video_cli.add_commands(commands, workspace)

    video = commands.add_parser("analyze-video", help="本地批量视频分析、关键帧和可复用素材资产库（不上传媒体）")
    workspace(video)
    video.add_argument("--input", required=True, type=Path)
    video.add_argument("--creator", help="创作者 ID；默认从路径生成")
    video.add_argument("--recursive", action="store_true")
    video.add_argument("--ffmpeg", default="ffmpeg")
    video.add_argument("--ffprobe", default="ffprobe")
    video.add_argument("--scene-threshold", type=float, default=0.3, help="像素变化阈值，0–1，默认 0.3")
    video.add_argument("--sample-interval", type=float, default=2.0, help="采样间隔秒，默认 2")
    video.add_argument("--max-frames", type=int, default=120, help="每视频最大采样帧，默认 120")
    video.add_argument("--segment-seconds", type=float, default=12.0, help="长镜头候选片段上限秒，默认 12")
    video.add_argument("--timeout", type=int, default=600, help="每次本地工具调用超时秒，默认 600")
    video.add_argument("--no-face-detection", action="store_true", help="禁用可选本地人脸检测")

    for name in ["prepare", "distill"]:
        command = commands.add_parser(name, help="素材预处理" if name == "prepare" else "预处理并蒸馏创作者 Skill")
        workspace(command)
        command.add_argument("--input", required=True, type=Path)
        command.add_argument("--creator", help="创作者 ID；省略时从路径生成")
        command.add_argument("--recursive", action="store_true", help="扫描子文件夹")
        command.add_argument("--asr", choices=["faster-whisper", "none"], default="faster-whisper")
        command.add_argument("--asr-model", default="small", help="模型名或本地模型目录")
        command.add_argument("--language", default="zh", help="ASR 语言；auto 表示自动检测")
        command.add_argument("--device", default="cpu")
        command.add_argument("--compute-type", default="int8")
        command.add_argument("--ffmpeg", default="ffmpeg")
        if name == "distill":
            inference(command)

    generate = commands.add_parser("generate", help="使用已确认的 Skill 创作新文案")
    workspace(generate)
    inference(generate)
    generate.add_argument("--creator", required=True)
    generate.add_argument("--topic", required=True)
    generate.add_argument("--skill-version", help="默认使用已激活版本")
    generate.add_argument("--brief-file", type=Path, help="补充受众、时长、事实或限制的 UTF-8 文本文件")

    skills = commands.add_parser("skills", help="查看、确认、修订和导出 Skill")
    operations = skills.add_subparsers(dest="operation", required=True)
    for name in ["list", "approve", "export", "revise", "diff", "rollback", "optimize"]:
        command = operations.add_parser(name)
        workspace(command)
        command.add_argument("--creator", required=True)
        if name not in {"list", "diff"}:
            command.add_argument("--version", required=True)
        if name == "approve":
            command.add_argument("--comparison", help="V2 候选的 acceptance 对照运行 ID")
            command.add_argument("--confirmation", help="人工审核署名与确认意见")
        if name == "diff":
            command.add_argument("--left", required=True)
            command.add_argument("--right", required=True)
        if name == "optimize":
            inference(command)
            command.add_argument("--batch", action="append", required=True, help="development 批量运行 ID，可重复")
            command.add_argument("--feedback", action="append", required=True, help="人工反馈 ID，可重复")
        if name == "export":
            command.add_argument("--destination", required=True, type=Path)
        if name == "revise":
            command.add_argument("--skill-file", required=True, type=Path)
    for name in ["evaluate", "quality", "batch"]:
        command = commands.add_parser(name, help={"evaluate": "单篇结构化评估", "quality": "生成/修订并有限复评", "batch": "固定选题批量稳定性测试"}[name])
        workspace(command)
        inference(command)
        command.add_argument("--creator", required=True)
        command.add_argument("--skill-version")
        command.add_argument("--config", type=Path, help="评估配置 JSON")
        if name != "batch":
            command.add_argument("--topic", required=name == "quality", default="未提供选题")
            command.add_argument("--brief-file", type=Path)
            command.add_argument("--script-file", required=name == "evaluate", type=Path, help="纯口播正文 UTF-8 文件")
        if name == "quality":
            command.add_argument("--max-revisions", type=int, default=2)
            command.add_argument("--feedback-id", help="用于对应原稿的人工反馈 ID")
        if name == "batch":
            command.add_argument("--dataset", required=True, type=Path)
            command.add_argument("--repeats", type=int, default=1)
            command.add_argument("--max-revisions", type=int, default=0)
    compare = commands.add_parser("compare", help="同集、同配置版本对照")
    workspace(compare)
    compare.add_argument("--creator", required=True)
    compare.add_argument("--baseline", required=True, help="基线 batch 运行 ID")
    compare.add_argument("--candidate", required=True, help="候选 batch 运行 ID")
    feedback = commands.add_parser("feedback", help="追加人工反馈及独立覆盖视图")
    workspace(feedback)
    feedback.add_argument("--creator", required=True)
    feedback.add_argument("--run-id", required=True)
    feedback.add_argument("--file", required=True, type=Path)
    creators = commands.add_parser("creators", help="列出 workspace 内创作者和激活版本")
    workspace(creators)
    config = commands.add_parser("evaluation-config", help="导出默认评分配置，拒绝覆盖")
    config.add_argument("--destination", type=Path, required=True)
    doctor = commands.add_parser("doctor", help="检查依赖，不调用模型或下载 ASR 权重")
    doctor.add_argument("--codex-bin", default="codex")
    doctor.add_argument("--ffmpeg", default="ffmpeg")
    doctor.add_argument("--ffprobe", default="ffprobe")
    return root


def execute(args):
    if args.command == "doctor":
        try:
            command = codex_command(args.codex_bin)
        except CreatorDNAError:
            command = None
        return {"python": sys.version.split()[0], "codex_command": command,
                "ffmpeg": shutil.which(args.ffmpeg), "ffprobe": shutil.which(args.ffprobe),
                "pillow": importlib.util.find_spec("PIL") is not None, "opencv": importlib.util.find_spec("cv2") is not None,
                "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
                "note": "只检查可发现性；不会确认 Codex 登录状态、ASR 权重或 GPU 是否可用。"}
    if args.command == "evaluation-config":
        if args.destination.exists():
            raise CreatorDNAError("配置目标已存在，请选择新路径。")
        write_json(args.destination, DEFAULT_CONFIG)
        return {"config": str(args.destination.resolve())}
    workspace = Workspace(args.workspace)
    if args.command == "tasks":
        return task_cli.execute(args, workspace)
    if args.command == "tts":
        return tts_cli.execute(args, workspace)
    if args.command == "lipsync":
        return lip_sync_cli.execute(args, workspace)
    if args.command == "production":
        return production_cli.execute(args, workspace)
    if args.command == "video":
        return video_cli.execute(args, workspace)
    if getattr(args, "timeout", 1) <= 0 or getattr(args, "max_input_chars", 1) <= 0:
        raise CreatorDNAError("timeout 和 max-input-chars 必须大于 0。")
    if args.command == "analyze-video":
        analyzer = LocalVideoAnalyzer(args.ffmpeg, args.ffprobe, args.scene_threshold,
                                      args.sample_interval, args.max_frames, args.segment_seconds,
                                      args.timeout, not args.no_face_detection)
        return VideoAssetWorkflow(workspace, analyzer).analyze(args.input, args.creator, args.recursive)
    if args.command == "creators":
        root = workspace.root / "creators"
        return {"creators": [{"creator_id": p.name,
            "active": read_json(p / "skills/active.json")["version"] if (p / "skills/active.json").exists() else None}
            for p in sorted(root.iterdir()) if p.is_dir() and not p.is_symlink()] if root.exists() else []}
    if args.command in {"compare", "feedback"}:
        quality = QualityWorkflow(workspace)
        if args.command == "compare":
            return quality.compare(args.creator, args.baseline, args.candidate)
        return quality.feedback(args.creator, args.run_id, read_json(args.file))
    if args.command in {"evaluate", "quality", "batch"}:
        quality = QualityWorkflow(workspace, CodexAdapter(args.codex_bin, args.model, args.timeout), args.max_input_chars)
        config = read_json(args.config) if args.config else None
        if args.command == "batch":
            return quality.batch(args.creator, read_json(args.dataset), args.skill_version, args.repeats, config, args.max_revisions)
        brief = args.brief_file.read_text(encoding="utf-8-sig") if args.brief_file else None
        script = args.script_file.read_text(encoding="utf-8-sig") if args.script_file else None
        if args.command == "evaluate":
            return quality.evaluate(args.creator, script, args.skill_version, args.topic, brief, config)
        return quality.cycle(args.creator, args.topic, args.skill_version, brief, config, args.max_revisions, script, args.feedback_id)
    if args.command == "skills":
        manager = SkillManager(workspace)
        if args.operation == "list":
            return {"versions": [read_json(p / "metadata.json") for p in manager.versions(args.creator)]}
        if args.operation == "optimize":
            return QualityWorkflow(workspace, CodexAdapter(args.codex_bin, args.model, args.timeout), args.max_input_chars).optimize(
                args.creator, args.version, args.batch, args.feedback)
        with workspace.lock(args.creator):
            if args.operation == "approve":
                return {"skill": str(manager.approve(args.creator, args.version, args.comparison, args.confirmation)), "status": "approved"}
            if args.operation == "diff":
                return {"diff": manager.diff(args.creator, args.left, args.right)}
            if args.operation == "rollback":
                return {"skill": str(manager.rollback(args.creator, args.version)), "status": "approved"}
            if args.operation == "revise":
                return {"skill": str(manager.revise(args.creator, args.version, args.skill_file)), "status": "draft"}
            else:
                return {"export": str(manager.export(args.creator, args.version, args.destination))}
    agent = None if args.command == "prepare" else CodexAdapter(args.codex_bin, args.model, args.timeout)
    media = None
    if args.command in {"prepare", "distill"}:
        transcriber = (WhisperTranscriber(args.asr_model, None if args.language == "auto" else args.language,
                       args.device, args.compute_type) if args.asr == "faster-whisper" else None)
        media = MediaProcessor(transcriber, args.ffmpeg)
    workflow = Workflow(workspace, media, agent)
    if args.command == "prepare":
        return workflow.prepare(args.input, args.creator, args.recursive)
    if args.command == "distill":
        return workflow.distill(args.input, args.creator, args.recursive, args.max_input_chars)
    brief = args.brief_file.read_text(encoding="utf-8-sig") if args.brief_file else None
    return workflow.generate(args.creator, args.topic, args.skill_version, brief, args.max_input_chars)


def main(argv=None):
    load_environment()
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "video":
            if result.get("status") == "failed":
                return 1
            if result.get("status") == "cancelled":
                return 130
        if args.command == "production" and (result.get("status") in {"failed", "needs_assets"} or result.get("valid") is False):
            return 1
        if args.command == "analyze-video" and result["status"] == "failed":
            return 1
        if ((args.command == "tasks" and args.operation in {"run", "create"}) or
                (args.command == "tts" and args.operation in {"register", "synthesize"}) or
                (args.command == "lipsync" and args.operation == "generate")):
            if result["status"] == "failed":
                return 1
            if result["status"] == "cancelled":
                return 130
        return 0
    except (CreatorDNAError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("任务已中断；已完成的缓存、文案和评估记录保留。", file=sys.stderr)
        return 130
