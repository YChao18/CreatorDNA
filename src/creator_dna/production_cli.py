"""V3.0-05 command entry points."""
from pathlib import Path

from .agents import CodexAdapter
from .contracts import validate
from .errors import CreatorDNAError
from .production import ProductionWorkflow, default_config
from .production_assets import from_library, import_asset, validate_assets
from .storage import digest_file, read_json, write_json
from .timeline import validate_timeline
from .video_style import build_profile, review_template


def add_commands(commands, workspace, inference):
    root = commands.add_parser("production", help="视觉风格、授权素材、ASS 字幕与可编辑视频时间轴（不渲染最终 MP4）")
    operations = root.add_subparsers(dest="operation", required=True)
    for name in ("profile", "assets", "import", "config", "plan", "validate", "rebuild"):
        cmd = operations.add_parser(name)
        workspace(cmd)
        if name != "validate":
            cmd.add_argument("--creator", required=True)
        if name in {"profile", "assets"}:
            cmd.add_argument("--library", required=True, type=Path)
        if name == "profile":
            cmd.add_argument("--template")
            cmd.add_argument("--review", type=Path)
        if name in {"config", "plan"}:
            cmd.add_argument("--profile", required=True, type=Path)
        if name in {"import", "plan"}:
            cmd.add_argument("--registry", required=True, type=Path)
        if name in {"import", "plan", "validate", "rebuild"}:
            cmd.add_argument("--ffprobe", default="ffprobe")
        if name == "import":
            cmd.add_argument("--asset", required=True, type=Path)
            cmd.add_argument("--id", required=True)
            cmd.add_argument("--kind", required=True, choices=["image", "video", "product", "screenshot", "background", "portrait"])
            cmd.add_argument("--media-type", required=True, choices=["image", "video"])
            cmd.add_argument("--source", required=True)
            cmd.add_argument("--rights", required=True, choices=["owned", "licensed", "authorized", "unknown", "denied"])
            cmd.add_argument("--rights-note", required=True)
            cmd.add_argument("--topic", action="append", default=[])
            cmd.add_argument("--description", required=True)
            cmd.add_argument("--audio-offset", type=float, help="人像成品首帧对应主口播音频的秒位置，默认 0")
            cmd.add_argument("--portrait-audio", type=Path, help="人像成品对应的主口播 PCM WAV，portrait 必填以绑定哈希")
        if name == "plan":
            inference(cmd)
            source = cmd.add_mutually_exclusive_group(required=True)
            source.add_argument("--speech", type=Path, help="V3.0-03 speech.json；使用实测 canonical_audio")
            source.add_argument("--audio", type=Path, help="已知实际音频的单声道 PCM16 WAV；须配合 --timing")
            cmd.add_argument("--timing", type=Path, help="绑定当前 PCM 音频哈希的 subtitle-timing JSON")
            cmd.add_argument("--config", type=Path)
            cmd.add_argument("--direction-file", type=Path)
            cmd.add_argument("--director", choices=["codex", "local"], default="codex")
        if name in {"validate", "rebuild"}:
            cmd.add_argument("--timeline", required=True, type=Path)


def execute(args, workspace):
    if args.operation in {"profile", "assets"}:
        source_root = Path(read_json(args.library)["input_folder"]).resolve()
        if source_root == workspace.root or source_root in workspace.root.parents or workspace.root in source_root.parents:
            raise CreatorDNAError("workspace 与原始素材目录须互不包含。")
    if args.operation == "validate":
        return validate_timeline(read_json(args.timeline), args.timeline.resolve().parent, ffprobe=args.ffprobe)
    if args.operation in {"plan", "rebuild"}:
        if getattr(args, "timeout", 1) <= 0 or getattr(args, "max_input_chars", 1) <= 0:
            raise CreatorDNAError("timeout/max-input-chars 须为正数。")
        agent = CodexAdapter(args.codex_bin, args.model, args.timeout) if args.operation == "plan" and args.director == "codex" and not args.direction_file else None
        workflow = ProductionWorkflow(workspace, agent, args.ffprobe, getattr(args, "max_input_chars", 240000))
        if args.operation == "rebuild":
            return workflow.rebuild(args.creator, args.timeline)
        return workflow.plan(args.creator, args.profile, args.registry, speech=args.speech, audio=args.audio,
                             timing=args.timing, config_path=args.config, direction_path=args.direction_file,
                             local=args.director == "local")
    if args.operation == "profile":
        data = build_profile(args.library, args.template, read_json(args.review) if args.review else None)
        if data["creator_id"] != args.creator:
            raise CreatorDNAError("library 创作者与命令不匹配。")
        filename = "video-style-profile.json"
    elif args.operation == "assets":
        library = read_json(args.library)
        if library["creator_id"] != args.creator:
            raise CreatorDNAError("library 创作者与命令不匹配。")
        data, filename = from_library(library), "assets.json"
    elif args.operation == "config":
        profile = read_json(args.profile)
        validate(profile, "video-style-profile")
        if profile["creator_id"] != args.creator:
            raise CreatorDNAError("profile 创作者与命令不匹配。")
        data, filename = default_config(profile["output"]), "config.json"
    else:
        data = validate_assets(read_json(args.registry))
        asset = import_asset(args.asset, args.id, args.kind, args.media_type, args.source, args.rights,
                             args.rights_note, args.topic, args.description, args.ffprobe, args.audio_offset,
                             digest_file(args.portrait_audio) if args.portrait_audio else None)
        if any(a["asset_id"] == asset["asset_id"] for a in data["assets"]):
            raise CreatorDNAError("素材 ID 已存在；请使用新 ID 或编辑清单。")
        data["assets"].append(asset)
        filename = "assets.json"
    with workspace.lock(args.creator):
        run = workspace.new_run(args.creator, "production-"+args.operation)
        write_json(run / filename, data)
        if args.operation == "profile":
            write_json(run / "review-template.json", review_template())
        workspace.finish(run, "completed")
    return {"status": "completed", "output": str(run / filename), "run_id": run.name}
