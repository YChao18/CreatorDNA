"""Cloud TTS CLI. Creating a task never uploads media or calls a paid API."""

from pathlib import Path

from .storage import now
from .task_cli import TerminalProgress
from .tasks import TaskManager
from .tts import TTSError, config, make_provider
from .tts_workflow import create_synthesis_task, create_voice_task, resolve_segment
from .voice_assets import VoiceAssets


def add_commands(commands, workspace):
    tts = commands.add_parser("tts", help="云端口播：能力、授权声音资产、注册音色和真实语音合成")
    operations = tts.add_subparsers(dest="operation", required=True)
    for name in ("capabilities", "reference", "voices", "binding", "bind", "remote-voices", "register", "import-voice", "delete-voice", "synthesize", "resolve"):
        command = operations.add_parser(name)
        workspace(command)
        if name not in {"voices", "binding", "resolve"}:
            command.add_argument("--config", type=Path, required=True, help="无 API Key 的 JSON 配置")
        if name not in {"capabilities", "remote-voices", "resolve"}:
            command.add_argument("--creator", required=True)
        if name in {"reference", "import-voice", "bind"}:
            command.add_argument("--authorize", action="store_true", help="确认已获得声音使用与复刻授权")
            command.add_argument("--cloud-processing", action="store_true", help="确认所选服务商云端处理参考声音与文案")
            command.add_argument("--confirmation", required=True, help="授权人署名、授权依据和云端处理确认")
        if name == "reference":
            sources = command.add_mutually_exclusive_group(required=True)
            sources.add_argument("--source", type=Path, help="本地声音参考；复制并规范化到 workspace，不上传")
            sources.add_argument("--library", type=Path, help="V3.0-01 library.json，按候选提取授权片段")
            command.add_argument("--candidate", help="library 中的 candidate_id")
        if name == "register":
            command.add_argument("--reference", required=True)
            command.add_argument("--name", required=True, help="千问 1–16 字符前缀；MiniMax 8–256 字符 Voice ID")
        if name in {"import-voice", "bind"}:
            command.add_argument("--voice-id", required=True, help="须能从当前账户的云端音色列表确认")
        if name == "bind":
            command.add_argument("--input", type=Path, required=True, help="只读素材文件夹；每个创作者的默认音色关联")
        if name in {"synthesize", "delete-voice"}:
            command.add_argument("--voice", required=name == "delete-voice", help="本地 voice-<hash> 资产 ID；合成省略时使用该素材文件夹的绑定音色")
        if name == "delete-voice":
            command.add_argument("--confirm-delete", action="store_true")
        if name == "remote-voices":
            command.add_argument("--page", type=int, default=0)
        if name in {"register", "synthesize"}:
            command.add_argument("--run", action="store_true", help="创建后立即执行；省略时仅保存任务")
            command.add_argument("--timeout", type=float, default=1800)
        if name == "synthesize":
            command.add_argument("--script-file", type=Path, required=True, help="Creator Skill 生成的新口播纯正文 UTF-8 文件")
        if name == "resolve":
            command.add_argument("--task", required=True)
            command.add_argument("--segment", type=int, required=True, help="从 0 开始的段号")
            command.add_argument("--accept-additional-billing", action="store_true")
            command.add_argument("--confirmation", required=True, help="核对服务商账单/请求后允许再次计费的说明")


def execute(args, workspace):
    if args.operation == "resolve":
        if not args.accept_additional_billing:
            raise TTSError("AdditionalBillingConfirmationRequired")
        return resolve_segment(TaskManager(workspace), args.task, args.segment, args.confirmation)
    if args.operation == "voices":
        return {"voices": VoiceAssets(workspace, args.creator).voices()}
    if args.operation == "binding":
        return VoiceAssets(workspace, args.creator).binding()
    provider = make_provider(config(args.config))
    if args.operation == "capabilities":
        return {**provider.capabilities().to_dict(), "scope": provider.scope,
                "output_format": provider.settings["format"], "output_sample_rate": provider.settings["sample_rate"],
                "note": "formats/sample_rates 是云端原生能力；成品由本地 FFmpeg 统一。异步任务接口当前适配器不支持。"}
    if args.operation == "remote-voices":
        if args.page < 0:
            raise TTSError("InvalidPage")
        return provider.list_voices(args.page)
    assets = VoiceAssets(workspace, args.creator)
    if args.operation in {"reference", "import-voice", "bind"}:
        consent = {"provider": provider.name, "scope": provider.scope, "authorized": args.authorize,
                   "cloud_processing": args.cloud_processing, "confirmation": args.confirmation,
                   "confirmed_at": now()}
        from .tts import require_consent
        require_consent(consent, provider.name, provider.scope)
        if args.operation == "bind":
            folder = assets.check_binding_folder(args.input)
            asset = assets.import_voice(provider, args.voice_id, consent)
            return assets.bind(folder, asset["asset_id"], provider)
        if args.operation == "import-voice":
            return assets.import_voice(provider, args.voice_id, consent)
        if args.library and not args.candidate:
            raise TTSError("CandidateRequired")
        return assets.add_reference(provider.settings, source=args.source, library=args.library,
                                    candidate=args.candidate, consent=consent)
    if args.operation == "delete-voice":
        if not args.confirm_delete:
            raise TTSError("DeleteConfirmationRequired")
        return assets.delete(args.voice, provider)
    if args.operation == "register":
        state = create_voice_task(workspace, args.creator, args.reference, provider.settings, args.name, args.timeout)
    else:
        text = args.script_file.read_text(encoding="utf-8-sig")
        state = create_synthesis_task(workspace, args.creator, args.voice, provider.settings, text, args.timeout)
    if args.run:
        state = TaskManager(workspace).run(state["task_id"], observer=TerminalProgress())
    return state
