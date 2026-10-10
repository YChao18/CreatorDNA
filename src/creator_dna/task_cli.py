"""Terminal progress on stderr, structured command results on stdout."""

import math
import shutil
import sys
import time
from pathlib import Path

from .errors import CreatorDNAError
from .storage import default_id
from .tasks import STAGES, TERMINAL, TaskManager, stage


def add_commands(commands, workspace):
    tasks = commands.add_parser("tasks", help="统一任务：创建、运行、状态、历史、日志与取消")
    operations = tasks.add_subparsers(dest="operation", required=True)
    for name in ("create", "run", "status", "history", "logs", "cancel"):
        command = operations.add_parser(name)
        workspace(command)
        if name in {"run", "status", "logs", "cancel"}:
            command.add_argument("--task", required=True, help="task-<UUID>，由 create 返回")
        if name in {"run", "status"}:
            command.add_argument("--interval", type=float, default=0.5, help="状态刷新秒，默认 0.5")
        if name == "run":
            command.add_argument("--resume", action="store_true", help="显式恢复安全可重复阶段；校验并复用已完成阶段")
        if name == "status":
            command.add_argument("--watch", action="store_true", help="持续观察，Ctrl+C 仅停止观察")
        if name == "history":
            command.add_argument("--creator")
            command.add_argument("--status", choices=["pending", "running", *sorted(TERMINAL)])
            command.add_argument("--limit", type=int, default=20)
        if name == "logs":
            command.add_argument("--tail", type=int, default=100)
        if name == "create":
            command.add_argument("--kind", choices=["local-demo", "ffmpeg-demo", "analyze-video"], default="local-demo")
            command.add_argument("--creator")
            command.add_argument("--run", action="store_true", help="创建后立即前台执行，显示进度")
            command.add_argument("--timeout", type=float, default=600, help="每阶段尝试超时秒")
            command.add_argument("--max-attempts", type=int, default=1, help="每阶段本次执行最多尝试次数，1–10")
            command.add_argument("--retry-delay", type=float, default=0.2)
            command.add_argument("--chunks", type=int, default=12, help="本地工程演示实际写入分块数")
            command.add_argument("--delay", type=float, default=0.1, help="演示分块间隔；不用于推算百分比")
            command.add_argument("--duration", type=float, default=3, help="合成 FFmpeg 演示的目标秒数，0.1–60")
            command.add_argument("--ffmpeg", default="ffmpeg")
            command.add_argument("--ffprobe", default="ffprobe")
            command.add_argument("--input", type=Path, help="analyze-video 的只读素材文件夹")
            command.add_argument("--recursive", action="store_true")
            command.add_argument("--sample-interval", type=float, default=2)
            command.add_argument("--max-frames", type=int, default=120)
            command.add_argument("--no-face-detection", action="store_true")


class TerminalProgress:
    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.last_line = None

    def __call__(self, state):
        p, overall = state["progress"], state["overall_progress"]
        value = "未知百分比"
        if p["percent"] is not None:
            value = f"{'估算 ' if p['estimated'] else ''}{p['percent']:.1f}%"
        if p["completed"] is not None:
            value += f" ({p['completed']:g}/{p['total']:g} {p['unit']})" if p["total"] else f" ({p['completed']:g} {p['unit']} 已完成)"
        total = "未知" if overall["percent"] is None else f"{'估算 ' if overall['estimated'] else ''}{overall['percent']:.1f}%"
        attempt = next((s["attempt"] for s in state["stages"] if s["id"] == state["current_stage"]), 0)
        line = (f"[{state['status']}] {STAGES.get(state['current_stage'], '等待')} | {state['current_object'] or '—'}"
                f" | {state['elapsed_seconds']:.1f}s (阶段 {state['stage_elapsed_seconds']:.1f}s) | 阶段 {value} | 整体 {total}"
                f" | 尝试 {attempt} | {state['message']}")
        if state["error"]:
            line += f" | 错误 {state['error']['type']}: {state['error']['message']}"
        elif state["last_error"]:
            line += f" | 上次错误 {state['last_error']['type']}"
        if self.last_line is None:
            print(f"开始时间：{state['started_at'] or '尚未开始'}；创建时间：{state['created_at']}", file=self.stream, flush=True)
        if line == self.last_line:
            return
        self.last_line = line
        if self.stream.isatty():
            width = shutil.get_terminal_size().columns
            # Avoid wrapping a carriage-return line, including CJK display width.
            import unicodedata
            cropped, used = "", 0
            for char in line:
                used += 2 if unicodedata.east_asian_width(char) in "WF" else 1
                if used > width-1:
                    break
                cropped += char
            print("\r" + cropped + " " * max(0, width-1-used), end="", file=self.stream, flush=True)
            if state["status"] in TERMINAL:
                print(file=self.stream)
        else:
            print(line, file=self.stream, flush=True)


def execute(args, workspace):
    interval = getattr(args, "interval", 0.5)
    if not math.isfinite(interval) or not 0.05 <= interval <= 60:
        raise CreatorDNAError("interval 须为 0.05–60 秒。")
    manager = TaskManager(workspace, heartbeat=interval)
    if args.operation == "history":
        return {"tasks": manager.history(args.creator, args.status, args.limit)}
    if args.operation == "logs":
        return {"task_id": args.task, "logs": manager.logs(args.task, args.tail)}
    if args.operation == "cancel":
        return manager.cancel(args.task)
    if args.operation == "status":
        state = manager.status(args.task)
        if args.watch:
            display = TerminalProgress()
            try:
                while True:
                    display(state)
                    if state["status"] in TERMINAL:
                        break
                    time.sleep(interval)
                    state = manager.status(args.task)
            except KeyboardInterrupt:
                print("\n停止观察，任务继续执行。", file=sys.stderr)
        return state
    if args.operation == "run":
        print(f"任务：{args.task}；状态文件：{manager.folder(args.task) / 'status.json'}", file=sys.stderr)
        return manager.run(args.task, resume=args.resume, observer=TerminalProgress())
    materials = []
    creator = args.creator
    options = {"timeout": args.timeout, "max_attempts": args.max_attempts,
               "retry_safe": True, "retry_delay": args.retry_delay}
    if args.kind == "local-demo":
        if not 1 <= args.chunks <= 10000 or not math.isfinite(args.delay) or not 0 <= args.delay <= 10:
            raise CreatorDNAError("chunks 须为 1–10000；delay 须为 0–10 秒。")
        stages = [stage("material_analysis", "local_chunks", {"chunks": args.chunks, "delay": args.delay}, **options),
                  stage("final_check", "check_chunks", **options)]
    elif args.kind == "ffmpeg-demo":
        if not math.isfinite(args.duration) or not 0.1 <= args.duration <= 60:
            raise CreatorDNAError("duration 须为 0.1–60 秒。")
        stages = [stage("render", "ffmpeg_demo", {"duration": args.duration, "ffmpeg": args.ffmpeg}, **options),
                  stage("final_check", "check_video", {"ffmpeg": args.ffmpeg, "ffprobe": args.ffprobe}, **options)]
    else:
        from .video import LocalVideoAnalyzer, scan_videos, video_source
        if args.input is None:
            raise CreatorDNAError("analyze-video 任务需要 --input。")
        folder, paths = scan_videos(args.input, workspace.root, args.recursive)
        creator = creator or default_id(folder)
        config = {"ffmpeg": args.ffmpeg, "ffprobe": args.ffprobe, "sample_interval": args.sample_interval,
                  "max_frames": args.max_frames, "face_detection": not args.no_face_detection, "timeout": args.timeout}
        LocalVideoAnalyzer(**config)  # Parameter validation before task creation.
        materials = [{"source_id": s.source_id, "path": str(s.path), "sha256": s.sha256,
                      "relative_path": s.relative_path} for s in (video_source(path, folder) for path in paths)]
        stages = [stage("material_analysis", "analyze_video", {"input": str(folder), "recursive": args.recursive,
                                                               "analyzer": config}, **options)]
    state = manager.create(stages, creator, materials, args.kind)
    if args.run:
        print(f"任务：{state['task_id']}；状态文件：{manager.folder(state['task_id']) / 'status.json'}\n"
              f"开始时间：创建 {state['created_at']}；Ctrl+C 取消执行。", file=sys.stderr, flush=True)
        return manager.run(state["task_id"], observer=TerminalProgress())
    return state
