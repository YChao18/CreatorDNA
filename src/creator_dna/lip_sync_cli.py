"""Short portrait CLI. Local authorization never implies third-party upload."""

from pathlib import Path

from .lip_sync import LipSyncError, config, make_provider
from .lip_sync_workflow import audio_input, create_task
from .portrait import UsageLedger, load_library, plan, rank_candidates, review_template
from .portrait_sources import creator_raw_folder
from .storage import digest_file, now, read_json, write_json
from .task_cli import TerminalProgress
from .tasks import TaskManager


def add_commands(commands, workspace):
    group = commands.add_parser("lipsync", help="本地人像口型同步：自动候选选择、非循环复用规划和真实 MuseTalk 推理")
    operations = group.add_subparsers(dest="operation", required=True)
    for name in ("capabilities", "doctor", "rank", "review-template", "plan", "consent", "generate"):
        command = operations.add_parser(name)
        workspace(command)
        if name in {"capabilities", "doctor", "generate"}:
            command.add_argument("--config", type=Path, required=True, help="独立 MuseTalk 环境的 JSON 配置")
        if name not in {"capabilities", "doctor"}:
            command.add_argument("--creator", required=True)
        if name in {"rank", "review-template", "plan"}:
            command.add_argument("--library", type=Path, required=True, help="V3.0-01 library.json")
        if name in {"rank", "plan"}:
            command.add_argument("--review", type=Path, help="可选，已有人工筛选记录；省略时自动选择候选")
        if name in {"review-template", "consent"}:
            command.add_argument("--destination", type=Path, required=True, help="workspace 内新文件；拒绝覆盖")
        if name in {"plan", "generate"}:
            command.add_argument("--timeline", default="default", help="同一视频的镜头复用记账 ID")
            command.add_argument("--position", type=float, default=0, help="在该视频时间线中的秒位置")
            command.add_argument("--cooldown", type=float, default=30)
            command.add_argument("--max-uses", type=int, default=1, help="同一时间线候选/重叠源区间/动作最多使用次数")
            command.add_argument("--candidate")
            command.add_argument("--source-id", help="锁定单个源文件的 source_id；同一视频不得换源")
        if name == "plan":
            command.add_argument("--durations", type=float, nargs="+", required=True, help="每段 5–10 秒，素材不足则失败")
        if name in {"consent", "generate"}:
            sound = command.add_mutually_exclusive_group(required=True)
            sound.add_argument("--audio", type=Path)
            sound.add_argument("--tts-manifest", type=Path, help="V3.0-03 speech.json，校验音频哈希")
        if name == "consent":
            command.add_argument("--source", type=Path, nargs="+", required=True, help="已获授权的源视频/参考图")
            command.add_argument("--authorize", action="store_true", help="确认人像重定向和新音频使用已获授权，仅限本地")
            command.add_argument("--confirmation", required=True, help="授权人、依据和本地使用范围")
        if name == "generate":
            command.add_argument("--library", type=Path)
            command.add_argument("--review", type=Path)
            command.add_argument("--image", type=Path, help="方案 B：静态参考图口型；不生成身体动作")
            command.add_argument("--consent", type=Path, required=True)
            command.add_argument("--duration", type=float, default=8)
            command.add_argument("--audio-start", type=float, default=0)
            command.add_argument("--reference-max-dimension", type=int, help="等比缩放工作参考副本的最长边，原片不变；512–8192")
            command.add_argument("--timeout", type=float, default=1800)
            command.add_argument("--max-attempts", type=int, default=1)
            command.add_argument("--run", action="store_true", help="创建后执行；无此标志只保存任务")


def destination(args, workspace):
    path = args.destination.resolve()
    if workspace.root not in path.parents or path.exists():
        raise LipSyncError("DestinationMustBeNewFileInsideWorkspace")
    return path


def execute(args, workspace):
    if args.operation in {"capabilities", "doctor"}:
        provider = make_provider(config(args.config))
        return provider.capabilities().to_dict() if args.operation == "capabilities" else provider.environment()
    if args.operation == "consent":
        if not args.authorize or not args.confirmation.strip():
            raise LipSyncError("LocalPortraitAndAudioAuthorizationRequired")
        from .contracts import validate
        value = {"schema_version": "3.0-04", "creator_id": args.creator, "authorized": True,
                 "processing": "local", "confirmation": args.confirmation, "confirmed_at": now(),
                 "source_sha256": sorted({digest_file(p) for p in args.source}),
                 "audio_sha256": [digest_file(audio_input(args.audio, args.tts_manifest))]}
        validate(value, "portrait-consent")
        target = destination(args, workspace)
        write_json(target, value)
        return {"consent": str(target), "processing": "local"}
    if args.operation == "generate":
        state = create_task(workspace, args.creator, args.config, audio=args.audio, tts_manifest=args.tts_manifest,
            library=args.library, review=args.review, candidate=args.candidate, source_id=args.source_id, image=args.image,
            consent=args.consent, duration=args.duration, audio_start=args.audio_start,
            timeline=args.timeline, position=args.position, cooldown=args.cooldown, max_uses=args.max_uses,
            timeout=args.timeout, max_attempts=args.max_attempts, reference_max_dimension=args.reference_max_dimension)
        return TaskManager(workspace).run(state["task_id"], observer=TerminalProgress()) if args.run else state
    data = load_library(args.library, args.creator)
    if args.operation == "review-template":
        target = destination(args, workspace)
        write_json(target, review_template(data))
        return {"review": str(target), "note": "人工核验原片、填写依据和安全起止时间；模板不会自动批准候选。"}
    ranked = rank_candidates(data, read_json(args.review) if args.review else None,
                             reference_folder=creator_raw_folder(workspace,args.creator))
    if args.operation == "rank":
        return {"creator_id": args.creator, "candidates": ranked,
                "note": "proxy_score 仅为采样代理排序；quality_score 由人工填写，不是模型质量指标。"}
    return plan(ranked, args.durations, UsageLedger(workspace, args.creator).read(), timeline=args.timeline,
                position=args.position, cooldown=args.cooldown, max_uses=args.max_uses, candidate=args.candidate, source_id=args.source_id)
