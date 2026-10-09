import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .agents import CodexAdapter, codex_command
from .errors import CreatorDNAError
from .media import MediaProcessor, WhisperTranscriber
from .skills import SkillManager
from .storage import Workspace, read_json
from .workflow import Workflow


def parser():
    root = argparse.ArgumentParser(prog="creator-dna", description="CreatorDNA：素材文件夹 → 风格 Skill → 新选题口播稿")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    def workspace(command):
        command.add_argument("--workspace", type=Path, default=Path("workspace"), help="运行数据目录（默认 ./workspace）")

    def inference(command):
        command.add_argument("--codex-bin", default="codex")
        command.add_argument("--model", help="可选，默认沿用 Codex CLI 配置")
        command.add_argument("--timeout", type=int, default=900)
        command.add_argument("--max-input-chars", type=int, default=240000)

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
    for name in ["list", "approve", "export", "revise"]:
        command = operations.add_parser(name)
        workspace(command)
        command.add_argument("--creator", required=True)
        if name != "list":
            command.add_argument("--version", required=True)
        if name == "export":
            command.add_argument("--destination", required=True, type=Path)
        if name == "revise":
            command.add_argument("--skill-file", required=True, type=Path)
    doctor = commands.add_parser("doctor", help="检查依赖，不调用模型或下载 ASR 权重")
    doctor.add_argument("--codex-bin", default="codex")
    doctor.add_argument("--ffmpeg", default="ffmpeg")
    return root


def execute(args):
    if args.command == "doctor":
        try:
            command = codex_command(args.codex_bin)
        except CreatorDNAError:
            command = None
        return {"python": sys.version.split()[0], "codex_command": command,
                "ffmpeg": shutil.which(args.ffmpeg), "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
                "note": "只检查可发现性；不会确认 Codex 登录状态、ASR 权重或 GPU 是否可用。"}
    workspace = Workspace(args.workspace)
    if args.command == "skills":
        manager = SkillManager(workspace)
        if args.operation == "list":
            return {"versions": [read_json(p / "metadata.json") for p in manager.versions(args.creator)]}
        with workspace.lock(args.creator):
            if args.operation == "approve":
                return {"skill": str(manager.approve(args.creator, args.version)), "status": "approved"}
            if args.operation == "revise":
                return {"skill": str(manager.revise(args.creator, args.version, args.skill_file)), "status": "draft"}
            return {"export": str(manager.export(args.creator, args.version, args.destination))}
    if getattr(args, "timeout", 1) <= 0 or getattr(args, "max_input_chars", 1) <= 0:
        raise CreatorDNAError("timeout 和 max-input-chars 必须大于 0。")
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
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CreatorDNAError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("任务已中断；已完成的转写缓存保留。", file=sys.stderr)
        return 130
