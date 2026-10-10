"""Agent-first unified production entry point; Task Manager owns execution."""
import shutil
from pathlib import Path

from .errors import CreatorDNAError
from .storage import read_json
from .task_cli import TerminalProgress
from .tasks import TaskManager, TERMINAL
from .video_workflow import create_video_task


def add_commands(commands, workspace):
    root = commands.add_parser("video", help="完整视频工作流：新选题/已有文案/时间轴重渲染")
    operations = root.add_subparsers(dest="operation", required=True)
    for name in ("create", "render", "clean"):
        cmd = operations.add_parser(name)
        workspace(cmd)
        if name == "clean":
            cmd.add_argument("--task", required=True)
            cmd.add_argument("--cache", action="store_true", help="删除共享渲染缓存；保留阶段成果及收费凭据")
            continue
        cmd.add_argument("--creator", required=True)
        cmd.add_argument("--run", action="store_true", help="立即执行；否则返回任务 ID 供 tasks run")
        cmd.add_argument("--timeout", type=int, default=7200, help="各阶段超时秒数")
        if name == "create":
            cmd.add_argument("--config", required=True, type=Path)
            group = cmd.add_mutually_exclusive_group(required=True)
            group.add_argument("--topic")
            group.add_argument("--script-file", type=Path, help="UTF-8 纯口播正文")
        else:
            cmd.add_argument("--timeline", required=True, type=Path)
            cmd.add_argument("--render-config", type=Path, help="仅渲染器配置 JSON")


def clean(manager, task_id, cache=False):
    folder = manager.folder(task_id)
    if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
        raise CreatorDNAError("任务目录不能为链接或 junction。")
    with manager._lock(folder):
        if manager.status(task_id)["status"] not in TERMINAL:
            raise CreatorDNAError("只能清理已停止任务。")
        removed = []
        # Keep all outputs, receipts, fingerprints and attempt logs.
        for pattern in ("part-*.mp4", "portrait-*.mp4", "image-*.png", "segment-*.ass"):
            for path in (folder/"stages").rglob(pattern):
                if path.parent.name.startswith("attempt-") and path.resolve().is_relative_to(folder.resolve()) and not path.is_symlink():
                    path.unlink()
                    removed.append(str(path))
        if cache:
            # Don't remove cached segments/locks while any task runner is active.
            if any(s["status"] == "running" for s in manager.history(limit=1000000)):
                raise CreatorDNAError("存在运行任务，不能清理共享缓存。")
            raw = manager.workspace.root/"render-cache"
            target = raw.resolve()
            if target.parent != manager.workspace.root or raw.is_symlink() or getattr(raw, "is_junction", lambda: False)():
                raise CreatorDNAError("缓存目录越出 workspace。")
            if target.exists():
                shutil.rmtree(target)
                removed.append(str(target))
        return {"task_id":task_id,"removed":removed,"checkpoints_preserved":True}


def execute(args, workspace):
    manager = TaskManager(workspace)
    if args.operation == "clean":
        return clean(manager,args.task,args.cache)
    if args.operation == "render":
        state = create_video_task(workspace,args.creator,timeline=args.timeline,
                    render=read_json(args.render_config) if args.render_config else None,timeout=args.timeout)
    else:
        script = args.script_file.read_text(encoding="utf-8-sig") if args.script_file else None
        state = create_video_task(workspace,args.creator,args.config,topic=args.topic,script=script,timeout=args.timeout)
    if args.run:
        import sys
        print(f"任务：{state['task_id']}；状态：{manager.folder(state['task_id'])/'status.json'}",file=sys.stderr,flush=True)
        return manager.run(state["task_id"],observer=TerminalProgress())
    return state
