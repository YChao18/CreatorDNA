"""Thin orchestration over V1/V2 and V3-03/04/05, hosted by V3-02 workers."""
import json
import math
import shutil
import subprocess
from pathlib import Path

from .agents import CodexAdapter
from .contracts import validate
from .errors import CreatorDNAError
from .lip_sync_workflow import generate_stage, prepare_task, verify_sources
from .production import ProductionWorkflow
from .production_assets import import_asset, validate_assets
from .render import artifact, settings as render_settings
from .storage import Workspace, digest_file, now, read_json, write_json, write_text
from .subtitles import speech_input
from .task_executors import run_ffmpeg
from .task_worker import StageFailure
from .tasks import TaskManager, stage
from .tts import config as tts_config, make_provider
from .tts_workflow import synthesize_stage
from .voice_assets import VoiceAssets


PATHS = {"profile", "assets", "speech", "timing", "production_config", "direction"}


def load_config(path):
    path = Path(path).resolve()
    cfg = read_json(path)
    validate(cfg, "video-workflow-config")
    for key in PATHS:
        if cfg.get(key):
            cfg[key] = str((path.parent/cfg[key]).resolve())
    for key in ("image", "library", "review"):
        if cfg.get("portrait", {}).get(key):
            cfg["portrait"][key] = str((path.parent/cfg["portrait"][key]).resolve())
    for key in ("tts", "lipsync"):
        if cfg.get(key):
            cfg[key] = read_json((path.parent/cfg[key]).resolve())
            if key == "tts":
                cfg[key] = tts_config(cfg[key])
            if key == "lipsync":
                from .lip_sync import config
                cfg[key] = config(cfg[key])
    for track in cfg.get("render", {}).get("audio_mix", []):
        track["path"] = str((path.parent/track["path"]).resolve())
    cfg["render"] = render_settings(cfg.get("render"))
    return cfg


def verify_materials(ctx):
    verify_sources(ctx.materials)


def create_video_task(workspace, creator, config_path=None, *, topic=None, script=None, timeline=None, render=None, timeout=7200):
    manager = TaskManager(workspace)
    snapshots = []
    if timeline:
        path = Path(timeline).resolve()
        cfg = render_settings(render)
        from .timeline import validate_timeline
        report = validate_timeline(read_json(path), path.parent, ffprobe=cfg["ffprobe"], check_ass_consistency=False)
        if not report["valid"]:
            raise CreatorDNAError("重渲染时间轴无效："+"；".join(report["issues"]))
        # Rebuild generates ASS from edited cues/styles, never edits source run.
        params = {"workspace": str(workspace.root), "timeline": str(path), "ffprobe": cfg["ffprobe"]}
        snapshots = [{"path": str(path), "sha256": digest_file(path)}]
        plan = [stage("composition", "video_rebuild", params, timeout=timeout, retry_safe=True)]
    else:
        if not config_path or bool(topic) == bool(script):
            raise CreatorDNAError("需要 --config，并且选择 --topic 或 --script-file。")
        cfg = load_config(config_path)
        params = {"workspace": str(workspace.root), "config": cfg, "topic": topic, "script": script}
        profile = read_json(cfg["profile"])
        validate(profile, "video-style-profile")
        if profile["creator_id"] != creator:
            raise CreatorDNAError("Video Style Profile 创作者不匹配。")
        registry = validate_assets(read_json(cfg["assets"]))
        # Freeze all explicit files, plus registry media, before any billable call.
        files = [Path(config_path).resolve(), Path(cfg["profile"]), Path(cfg["assets"])]
        files += [Path(cfg[k]) for k in PATHS-{"profile", "assets"} if cfg.get(k)]
        files += [Path(a["path"]) for a in registry["assets"]]
        files += [Path(v) for k, v in cfg.get("portrait", {}).items() if k in {"image", "library", "review"}]
        for a in registry["assets"]:
            source_folder = Path(a["path"]).resolve().parent
            if source_folder == workspace.root or source_folder in workspace.root.parents:
                raise CreatorDNAError("workspace 不得位于只读源素材目录内。")
        if topic or cfg.get("quality", False):
            from .skills import SkillManager
            skill, metadata = SkillManager(workspace).load(creator, cfg.get("skill_version"))
            cfg["skill_version"] = skill.name
            files += [p for p in skill.rglob("*") if p.is_file()]
        snapshots = [{"path": str(p.resolve()), "sha256": digest_file(p)} for p in dict.fromkeys(files)]
        if not cfg.get("speech"):
            if not cfg.get("tts") or not cfg.get("voice_asset"):
                raise CreatorDNAError("新声音需要 tts 配置和 voice_asset。")
            provider = make_provider(tts_config(cfg["tts"]))
            voice = VoiceAssets(workspace, creator).usable(cfg["voice_asset"], provider)
            cfg["voice_id_snapshot"] = voice["voice_id"]
        if cfg.get("lipsync"):
            auth = cfg.get("portrait_authorization")
            if not auth or auth["creator_id"] != creator or not auth["authorized"] or not auth["confirmation"].strip():
                raise CreatorDNAError("需要明确授权选定人像与本工作流新声音进行本地生成。")
        elif not cfg.get("speech"):
            raise CreatorDNAError("生成新声音的视频流程需要 lipsync；已有 speech 可复用音频绑定的人像素材。")
        plan = [stage("script", "video_script", params, timeout=timeout, retry_safe=True),
                stage("tts", "video_tts", params, timeout=timeout, retry_safe=True)]
        if cfg.get("lipsync"):
            plan.append(stage("lip_sync", "video_lipsync", params, timeout=timeout, retry_safe=True))
        plan.append(stage("composition", "video_composition", params, timeout=timeout, retry_safe=True))
    render_params = {"workspace": str(workspace.root), "render": cfg if timeline else cfg["render"]}
    plan += [stage("render", "video_render", render_params, timeout=timeout, retry_safe=True),
             stage("final_check", "video_verify", render_params, timeout=timeout, retry_safe=True)]
    return manager.create(plan, creator, snapshots, kind="video-production")


def agent(cfg):
    return CodexAdapter(**cfg.get("agent", {}))


def script_stage(ctx, params):
    verify_materials(ctx)
    cfg, text = params["config"], params["script"]
    if cfg.get("tts") and not cfg.get("speech"):
        make_provider(cfg["tts"]).check_credentials()
    if cfg.get("lipsync"):
        from .lip_sync import make_provider as lip_provider
        ctx.report("检查口型模型环境；未就绪时停止后续收费步骤", basis="read-only prerequisites; total unknown")
        environment = lip_provider(cfg["lipsync"]).environment()
        write_json(ctx.output_dir/"environment.json", environment)
        if not environment["ready"]:
            raise StageFailure("真实口型后端未就绪；查看 environment.json", "LocalBackendNotReady")
    workspace = Workspace(params["workspace"])
    # AI runs in a stage-owned workspace; only the pinned Skill is copied in.
    local = Workspace(ctx.output_dir/"workflow")
    if params["topic"] or cfg.get("quality", False):
        from .skills import SkillManager
        folder, metadata = SkillManager(workspace).load(ctx.creator_id, cfg.get("skill_version"))
        target = local.creator(ctx.creator_id)/"skills"/folder.name
        shutil.copytree(folder, target)
        write_json(target.parent/"active.json", {"version": folder.name})
        selected = folder.name
    else:
        selected = None
    if cfg.get("quality", False):
        from .quality import QualityWorkflow
        ctx.report("执行 V2 文案评估与有限修订", basis="Agent running; total unknown")
        result = QualityWorkflow(local, agent(cfg)).cycle(ctx.creator_id, params["topic"] or "已有文案", selected,
                    cfg.get("brief"), max_revisions=cfg.get("max_revisions", 2), script=text)
        if result["status"] != "passed":
            raise StageFailure("V2 文案评估未通过，查看 revision-history.json；停止后续收费调用。", "ScriptQualityFailed")
        text = Path(result["output"]).read_text(encoding="utf-8-sig")
    elif params["topic"]:
        from .workflow import Workflow
        ctx.report("按已批准 Creator Skill 生成新选题正文", basis="Agent running; total unknown")
        result = Workflow(local, None, agent(cfg)).generate(ctx.creator_id, params["topic"], selected, cfg.get("brief"))
        text = read_json(Path(result["run"])/"generation-result.json")["script"]
    if not isinstance(text, str) or not text.strip():
        raise CreatorDNAError("口播正文为空。")
    write_text(ctx.output_dir/"script.txt", text.strip())
    write_json(ctx.output_dir/"script.json", {"skill_version": selected, "topic": params["topic"], "quality_requested": cfg.get("quality", False)})
    verify_materials(ctx)
    return {"script": "script.txt", "skill_version": selected}


def tts_stage(ctx, params):
    verify_materials(ctx)
    cfg = params["config"]
    text = artifact(ctx, "script", "script.txt").read_text(encoding="utf-8")
    if cfg.get("speech"):
        audio, duration, spoken, manifest = speech_input(cfg["speech"])
        from .subtitles import spoken as normalize_text
        if normalize_text(text) != normalize_text(spoken):
            raise CreatorDNAError("已有 speech 与文案正文不一致。")
        write_json(ctx.output_dir/"speech-reference.json", {"manifest": cfg["speech"], "audio": str(audio), "duration": duration})
        ctx.report("复用已校验的已有声音；未请求 TTS", basis="existing PCM verified")
        return {"speech": cfg["speech"], "reused": True}
    return synthesize_stage(ctx, {"workspace": params["workspace"], "config": cfg["tts"], "text": text,
                                 "voice_asset": cfg["voice_asset"], "voice_id_snapshot": cfg["voice_id_snapshot"]})


def speech_path(ctx):
    result = ctx.inputs["tts"]["result"]
    return Path(result["speech"]) if result.get("reused") else artifact(ctx, "tts", "speech.json")


class SegmentContext:
    """Forward provider events to parent Task Manager with isolated segment files."""
    def __init__(self, parent, directory, index, total):
        self.parent = parent
        self.__dict__.update({k: v for k, v in parent.__dict__.items() if k != "parent"})
        self.task_id = parent.task_id+f"-segment-{index:04d}"
        self.attempt_dir = directory
        self.output_dir = directory/"outputs"
        self.output_dir.mkdir(parents=True)
        self.index, self.total = index, total

    def check_cancelled(self):
        self.parent.check_cancelled()

    def report(self, message, current_object=None, completed=None, total=None, unit=None, basis="provider status", estimated=False):
        self.parent.report(f"口型段 {self.index+1}/{self.total}；已完成 {self.index} 段；{message}", current_object,
                           completed, total, unit, basis, estimated)

    def log(self, message, level="info", **data):
        self.parent.log(message, level, segment=self.index, **data)


def lip_stage(ctx, params):
    verify_materials(ctx)
    cfg = params["config"]
    audio, duration, text, manifest = speech_input(speech_path(ctx))
    if duration < 5:
        raise CreatorDNAError("现有口型 Provider 要求至少 5 秒真实音频。")
    n = math.ceil(duration/10)
    boundaries = [0]+[math.floor(duration*i/n*25)/25 for i in range(1,n)]+[duration]
    cache = ctx.attempt_dir.parent/"checkpoints"
    cache.mkdir(exist_ok=True)
    from .lip_sync import make_provider as lip_provider
    provider = lip_provider(cfg["lipsync"])
    fingerprint = provider.fingerprint()
    identity = cache/"model-fingerprint.json"
    if identity.exists() and read_json(identity) != fingerprint:
        raise CreatorDNAError("口型分段任务模型已改变；不能混合不同模型结果。")
    write_json(identity,fingerprint)
    videos, audit = [], []
    for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        ctx.check_cancelled()
        folder = cache/f"segment-{i:04d}"
        folder.mkdir(exist_ok=True)
        receipt_path = folder/"complete.json"
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            for record in receipt["artifacts"]:
                if digest_file(record["path"]) != record["sha256"]:
                    raise CreatorDNAError("口型片段缓存损坏。")
            video = Path(receipt["video"])
        else:
            prepared = folder/"params.json"
            if not prepared.exists():
                auth = cfg["portrait_authorization"]
                consent = {"schema_version": "3.0-04", "creator_id": ctx.creator_id, "authorized": auth["authorized"],
                    "processing": "local", "confirmation": auth["confirmation"], "confirmed_at": auth["confirmed_at"],
                    "source_sha256": auth["source_sha256"], "audio_sha256": [digest_file(audio)]}
                p, snapshots = prepare_task(Workspace(params["workspace"]), ctx.creator_id, cfg["lipsync"], audio=audio,
                    consent=consent, duration=end-start, audio_start=start, timeline=ctx.task_id,
                    position=start, **cfg["portrait"])
                write_json(prepared, p)
                write_json(folder/"authorization.json", {"workflow_authorization": auth, "bound_consent": consent})
            p = read_json(prepared)
            # Each retry keeps the original reservation/selection/model fingerprint.
            directory = folder/f"attempt-{ctx.attempt:03d}"
            directory.mkdir()
            sub = SegmentContext(ctx, directory, i, n)
            generate_stage(sub, p)
            if provider.fingerprint() != fingerprint:
                raise CreatorDNAError("口型模型在分段任务期间发生变化。")
            video = sub.output_dir/"portrait.mp4"
            receipt = {"video": str(video), "artifacts": [{"path": str(f), "sha256": digest_file(f)} for f in sub.output_dir.rglob("*") if f.is_file()]}
            write_json(receipt_path, receipt)
        videos.append(video)
        audit.append({"index": i, "start": start, "end": end, "receipt": str(receipt_path)})
        ctx.report(f"已完成口型片段 {i+1}/{n}", video.name, i+1, n, "segments", "verified real inference segments")
    # Join same-geometry 25fps portraits; reject mixed source geometries rather than stretch.
    from .render import probe
    dimensions = {tuple((s["width"],s["height"])) for v in videos for s in probe(v,cfg["render"]["ffprobe"])["streams"] if s["codec_type"] == "video"}
    if len(dimensions) != 1:
        raise CreatorDNAError("口型片段尺寸不同；请选择相同模板的人像素材。")
    for i, video in enumerate(videos):
        shutil.copyfile(video, ctx.attempt_dir/f"portrait-{i:04d}.mp4")
    write_text(ctx.attempt_dir/"portraits.txt", "".join(f"file 'portrait-{i:04d}.mp4'\n" for i in range(n)))
    full = ctx.output_dir/"portrait.mp4"
    run_ffmpeg(ctx, [cfg["render"]["ffmpeg"], "-v", "error", "-n", "-f", "concat", "-safe", "1", "-i", "portraits.txt",
                    "-map", "0:v:0", "-an", "-c:v", "copy", "-t", str(duration), str(full)], duration, cwd=ctx.attempt_dir, log_name="portrait-concat")
    a = import_asset(full, "generated-portrait", "portrait", "video", "real lip-sync provider output", "authorized",
                     cfg["portrait_authorization"]["confirmation"], [], "真实口型结果，人工验收待完成", cfg["render"]["ffprobe"], audio_sha256=digest_file(audio))
    if abs(a["duration"]-duration) > .08:
        raise StageFailure("口型分段拼接时长不匹配", "PortraitConcatMismatch")
    # CFR last frame may extend by < one frame; actual source duration remains measured.
    write_json(ctx.output_dir/"portrait-asset.json", a)
    write_json(ctx.output_dir/"portrait-segments.json", audit)
    for p in ctx.attempt_dir.glob("portrait-*.mp4"):
        p.unlink()
    verify_materials(ctx)
    return {"segments": n, "human_review": "pending", "asset": "portrait-asset.json"}


class CommandAligner:
    """Adapter for an existing external aligner; never estimates timing."""
    def __init__(self, command, ctx):
        self.command, self.ctx = command, ctx

    def align(self, audio, text):
        request, output = self.ctx.attempt_dir/"alignment-request.json", self.ctx.output_dir/"timing.json"
        write_json(request, {"audio": str(audio), "audio_sha256": digest_file(audio), "text": text, "output": str(output)})
        command = [arg.replace("{request}", str(request)).replace("{output}", str(output)) for arg in self.command]
        self.ctx.report("等待配置的真实音频对齐器", basis="external aligner running; total unknown")
        with (self.ctx.attempt_dir/"alignment.stdout.log").open("wb") as out, (self.ctx.attempt_dir/"alignment.stderr.log").open("wb") as err:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=self.ctx.timeout_seconds, check=False)
        if result.returncode:
            raise StageFailure("对齐器失败；查看 alignment.stderr.log", "AlignmentFailed")
        return read_json(output)


def composition_stage(ctx, params):
    verify_materials(ctx)
    cfg = params["config"]
    registry = read_json(cfg["assets"])
    if "lip_sync" in ctx.inputs:
        # The new audio must use the new portrait, never a stale portrait from another speech.
        registry["assets"] = [a for a in registry["assets"] if a["kind"] != "portrait"]
        registry["assets"].append(read_json(artifact(ctx, "lip_sync", "portrait-asset.json")))
    registry_path = ctx.output_dir/"registry.json"
    write_json(registry_path, registry)
    ctx.report("按真实音频时间戳生成字幕和场景时间轴", basis="existing production planner running; total unknown")
    workflow = ProductionWorkflow(Workspace(ctx.output_dir/"workflow"), agent(cfg) if cfg.get("director", "agent") == "agent" else None,
                                  cfg["render"]["ffprobe"])
    result = workflow.plan(ctx.creator_id, cfg["profile"], registry_path, speech=speech_path(ctx), timing=cfg.get("timing"),
                           config_path=cfg.get("production_config"), direction_path=cfg.get("direction"),
                           local=cfg.get("director", "agent") == "local", aligner=CommandAligner(cfg["alignment_command"],ctx) if cfg.get("alignment_command") else None)
    if not result["ready_for_render"]:
        raise StageFailure("编排缺少必要素材；查看 timeline.json/validation.json。", "MissingAssets")
    verify_materials(ctx)
    return {"timeline": result["timeline"], "ready_for_render": True}


def rebuild_stage(ctx, params):
    verify_materials(ctx)
    result = ProductionWorkflow(Workspace(ctx.output_dir/"workflow"), ffprobe=params.get("ffprobe", "ffprobe")).rebuild(ctx.creator_id, params["timeline"])
    return {"timeline": result["timeline"], "ready_for_render": result["ready_for_render"]}
