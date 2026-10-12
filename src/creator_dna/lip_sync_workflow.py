"""V3.0-02 stage integration: immutable inputs, local inference, technical QA."""

import json
import math
import os
import subprocess
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

from .contracts import validate
from .lip_sync import LipSyncError, LipSyncRequest, config, duration_value, make_provider, require_consent
from .portrait import GUARD_SECONDS, UsageLedger, load_library, plan, rank_candidates
from .portrait_sources import creator_raw_folder, allowed_video
from .storage import Workspace, digest_file, now, read_json, write_json, write_text
from .task_executors import run_ffmpeg
from .tasks import TaskManager, stage


def probe(path, settings):
    try:
        output = subprocess.run([settings["ffprobe"], "-v", "error", "-show_streams", "-show_format",
            "-count_frames", "-of", "json", str(path)], capture_output=True, timeout=120, check=True)
        return json.loads(output.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise LipSyncError("MediaProbeFailed") from None


def audio_input(audio=None, tts_manifest=None):
    if bool(audio) == bool(tts_manifest):
        raise LipSyncError("ProvideAudioOrTTSManifest")
    if audio:
        return Path(audio).resolve()
    path = Path(tts_manifest).resolve()
    manifest = read_json(path)
    validate(manifest, "tts-manifest")
    source = (path.parent / manifest["audio"]).resolve()
    if source.parent != path.parent or not source.is_file() or digest_file(source) != manifest["sha256"]:
        raise LipSyncError("TTSOutputChanged")
    return source


def verify_sources(snapshots):
    for item in snapshots:
        path = Path(item["path"])
        if not path.is_file() or digest_file(path) != item["sha256"]:
            raise LipSyncError("InputChanged")


@contextmanager
def gpu_lease(workspace, gpu_id):
    """OS releases this advisory lock even if the worker is forcibly killed."""
    path = workspace.root / f".lip-sync-gpu-{gpu_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LipSyncError("LocalGPUInUse", retryable=True) from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def prepare_task(workspace, creator, settings, *, audio=None, tts_manifest=None,
                library=None, review=None, candidate=None, source_id=None, image=None, consent, planned_selection=None,
                duration=8, audio_start=0, timeline="default", position=0,
                cooldown=30, max_uses=1, timeout=1800, max_attempts=1, reference_max_dimension=None):
    settings = config(settings)
    duration = duration_value(duration)
    if reference_max_dimension is not None and (type(reference_max_dimension) is not int or not 512 <= reference_max_dimension <= 8192):
        raise LipSyncError("InvalidReferenceMaxDimension")
    if isinstance(audio_start, bool) or not isinstance(audio_start, (float, int)) or not math.isfinite(audio_start) or audio_start < 0:
        raise LipSyncError("InvalidAudioStart")
    provider = make_provider(settings)
    sound = audio_input(audio, tts_manifest)
    audio_info = probe(sound, settings)
    streams = [s for s in audio_info["streams"] if s["codec_type"] == "audio"]
    if len(streams) != 1 or audio_start+duration > float(audio_info["format"]["duration"])+.001:
        raise LipSyncError("AudioTooShortOrAmbiguous")
    selection = None
    reference_folder = creator_raw_folder(workspace, creator)
    if image and reference_folder is not None:
        raise LipSyncError(f"RawDynamicPortraitVideoRequired: 请使用 {reference_folder} 内的无字幕动态原片，不使用静态截图。")
    options = {"timeline": timeline, "position": position, "cooldown": cooldown, "max_uses": max_uses}
    if image:
        if library or review or candidate or source_id or planned_selection:
            raise LipSyncError("ConflictingReferenceInputs")
        reference, reference_type = Path(image).resolve(), "image"
        info = probe(reference, settings)
        video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        if not video or reference.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise LipSyncError("InvalidReferenceImage")
        width, height = video["width"], video["height"]
    else:
        if not library:
            raise LipSyncError("VideoLibraryRequired")
        data = load_library(library, creator)
        from .input import validate_input_folder
        validate_input_folder(data["input_folder"], workspace.root)
        ranked = rank_candidates(data, read_json(review) if review else None, reference_folder=reference_folder)
        if planned_selection is None:
            selection = plan(ranked, [duration], UsageLedger(workspace, creator).read(),
                             candidate=candidate, source_id=source_id, **options)["clips"][0]
        else:
            selection = planned_selection
            c, u = selection["candidate"], selection["usage"]
            known = next((r for r in ranked if r["candidate_id"] == c["candidate_id"]), None)
            if (known != c or not c["eligible"] or (source_id and c["source_id"] != source_id)
                    or (candidate and c["candidate_id"] != candidate) or u["timeline"] != timeline
                    or u["position"] != position or u["duration"] != duration
                    or u["source_sha256"] != c["source_sha256"]
                    or not c["start"] <= u["source_start"] < u["source_end"] <= c["end"]+1e-6
                    or abs(u["source_end"]-u["source_start"]-duration-GUARD_SECONDS) > 1e-6):
                raise LipSyncError("PortraitPlanChanged")
        c = selection["candidate"]
        reference, reference_type = Path(c["source_path"]).resolve(), "video"
        if digest_file(reference) != c["source_sha256"]:
            raise LipSyncError("SourceChanged")
        width, height = c["display_width"], c["display_height"]
    original_geometry = {"width": width, "height": height}
    factor = min(1,reference_max_dimension/max(width,height)) if reference_max_dimension else 1
    width,height = max(2,round(width*factor/2)*2),max(2,round(height*factor/2)*2)
    source_hash, sound_hash = digest_file(reference), digest_file(sound)
    consent_data = read_json(consent) if not isinstance(consent, dict) else consent
    require_consent(consent_data, creator, source_hash, sound_hash)
    snapshots = [{"path": str(p.resolve()), "sha256": digest_file(p)} for p in
                 [reference, sound] + ([Path(library)] if library else []) + ([Path(review)] if review else []) +
                 ([Path(tts_manifest)] if tts_manifest else [])]
    # No model setup/upload is performed by task creation. Missing prerequisites
    # produce a durable failed lip_sync task when run, not a mock video.
    params = {"workspace": str(workspace.root), "settings": settings,
              "reference": str(reference), "reference_type": reference_type,
              "audio": str(sound), "audio_start": audio_start, "duration": duration,
              "width": max(2, round(width/2)*2), "height": max(2, round(height/2)*2),
              "reference_processing": {"source_geometry": original_geometry, "max_dimension": reference_max_dimension,
                                       "aspect_preserved": True, "source_unchanged": True},
              "selection": selection, "library": str(Path(library).resolve()) if library else None,
              "review": str(Path(review).resolve()) if review else None, "reuse": options,
              "consent": consent_data, "snapshots": snapshots}
    return params, snapshots


def create_task(workspace, creator, settings, **kwargs):
    params, snapshots = prepare_task(workspace, creator, settings, **kwargs)
    return TaskManager(workspace).create([
        stage("lip_sync", "local_lip_sync", params, timeout=kwargs.get("timeout", 1800), max_attempts=kwargs.get("max_attempts", 1), retry_safe=True),
        stage("final_check", "check_lip_sync", {"settings": params["settings"]}, timeout=180, retry_safe=True)],
        creator, snapshots, kind="portrait-lip-sync")


def generate_stage(ctx, params, provider=None):
    settings = config(params["settings"])
    verify_sources(params["snapshots"])
    reference, sound = Path(params["reference"]), Path(params["audio"])
    reference_folder = creator_raw_folder(Workspace(params["workspace"]), ctx.creator_id)
    if reference_folder is not None and (params["reference_type"] != "video" or not allowed_video(reference, reference_folder)):
        raise LipSyncError(f"RawDynamicPortraitVideoRequired: 人物参考必须来自 {reference_folder} 内的动态原片。")
    require_consent(params["consent"], ctx.creator_id, digest_file(reference), digest_file(sound))
    provider = provider or make_provider(settings)
    ctx.report("检查真实本地后端环境与模型权重", basis="local prerequisite check; total unknown")
    environment = provider.environment()
    write_json(ctx.output_dir / "environment.json", environment)
    if not environment["ready"]:
        raise LipSyncError("LocalBackendNotReadySeeEnvironmentJSON")
    fingerprint = provider.fingerprint()
    # Bind retries to exactly the same model files. No cloud side effects here.
    checkpoint = ctx.attempt_dir.parent / "model-fingerprint.json"
    if checkpoint.exists() and read_json(checkpoint) != fingerprint:
        raise LipSyncError("ModelChangedSincePreviousAttempt")
    write_json(checkpoint, fingerprint)
    ledger = UsageLedger(Workspace(params["workspace"]), ctx.creator_id)
    if params["selection"]:
        ranked = rank_candidates(load_library(params["library"], ctx.creator_id), read_json(params["review"]) if params["review"] else None,
                                 reference_folder=reference_folder)
        ledger.reserve(ctx.task_id, params["selection"], ranked, params["reuse"])
    try:
        ctx.check_cancelled()
        width, height, duration = params["width"], params["height"], params["duration"]
        canonical_audio = ctx.output_dir / "speech.wav"
        run_ffmpeg(ctx, [settings["ffmpeg"], "-v", "error", "-n", "-xerror", "-i", str(sound),
            "-ss", str(params["audio_start"]), "-t", str(duration), "-map", "0:a:0", "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(canonical_audio)], duration)
        from .tts_audio import wave_metadata
        if abs(wave_metadata(canonical_audio)["duration_seconds"]-duration) > .001:
            raise LipSyncError("PreparedAudioDurationMismatch")
        prepared = ctx.output_dir / ("reference.mp4" if params["reference_type"] == "video" else "reference.png")
        cut = (["-ss", str(params["selection"]["usage"]["source_start"]), "-t", str(duration+GUARD_SECONDS)]
               if params["reference_type"] == "video" else ["-frames:v", "1"])
        run_ffmpeg(ctx, [settings["ffmpeg"], "-v", "error", "-n", "-xerror", "-i", str(reference), *cut,
            "-map", "0:v:0", "-an", "-vf", f"scale={width}:{height},setsar=1" +
            (",fps=25" if params["reference_type"] == "video" else ""),
            *(["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"] if params["reference_type"] == "video" else []),
            str(prepared)], duration+GUARD_SECONDS if params["reference_type"] == "video" else None)
        request = LipSyncRequest(prepared, params["reference_type"], canonical_audio, duration, width, height, params["consent"])
        with gpu_lease(Workspace(params["workspace"]), settings["gpu_id"]):
            result = provider.generate(request, ctx)
        if ctx.output_dir.resolve() not in result.video.resolve().parents or result.video.is_symlink():
            raise LipSyncError("ProviderOutputOutsideWorkspace")
        ctx.report("合并原始新口播音频并检查技术一致性", basis="verified inference; mux and decode total unknown")
        output = ctx.output_dir / "portrait.mp4"
        run_ffmpeg(ctx, [settings["ffmpeg"], "-v", "error", "-n", "-i", str(result.video), "-i", str(canonical_audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration), "-movflags", "+faststart", str(output)], duration)
        verification = verify_video(ctx, output, settings, duration, width, height)
        verify_sources(params["snapshots"])
        if provider.fingerprint() != fingerprint:
            raise LipSyncError("ModelChangedDuringInference")
        manifest = {"schema_version": "3.0-04", "task_id": ctx.task_id, "creator_id": ctx.creator_id,
            "provider": provider.name, "model": settings["model"], "created_at": now(),
            "video": output.name, "sha256": digest_file(output), "duration": duration,
            "reference_type": params["reference_type"], "source_sha256": digest_file(reference),
            "audio_sha256": digest_file(sound), "consent": params["consent"], "selection": params["selection"],
            "settings": settings, "backend": result.metadata, "verification": verification,
            "human_review": "pending", "review_checks": ["口型同步", "面部稳定性", "身份一致性", "动作/手势与语义",
                "源动作保留", "切镜连续性/光影", "牙齿/口部边缘失真", "音画感知同步"]}
        validate(manifest, "lip-sync-manifest")
        write_json(ctx.output_dir / "portrait.json", manifest)
        write_text(ctx.output_dir / "human-review.md", "# 口型同步样片人工验收（未完成）\n\n"
            "技术解码和时间戳通过不代表人物质量通过；逐项观看 portrait.mp4 与 reference。\n\n"+
            "\n".join(f"- [ ] {check}：记录出现问题的秒数及严重度" for check in manifest["review_checks"])+"\n")
        if params["selection"]:
            ledger.finish(ctx.task_id, "generated_pending_review", str(output))
        return {"video": output.name, "manifest": "portrait.json", "human_review": "pending"}
    except BaseException:
        if params["selection"]:
            ledger.finish(ctx.task_id, "failed_reserved_for_retry")
        raise


def verify_video(ctx, output, settings, duration, width, height):
    info = probe(output, settings)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise LipSyncError("InvalidOutputStreams")
    v, a = videos[0], audios[0]
    try:
        vd, ad = float(v["duration"]), float(a["duration"])
        vs, als = float(v["start_time"]), float(a["start_time"])
        fps, frames = float(Fraction(v["avg_frame_rate"])), int(v["nb_read_frames"])
        if (not all(math.isfinite(x) for x in (vd, ad, vs, als, fps)) or
                (v["width"], v["height"]) != (width, height) or abs(fps-25) > .001 or
                abs(vd-duration) > .08 or abs(ad-duration) > .08 or abs(vs) > .05 or abs(als) > .05 or
                abs(vs-als) > .05 or abs(vd-ad) > .08 or abs(frames-duration*25) > 2 or
                v.get("sample_aspect_ratio") not in {"1:1", "1/1"}):
            raise LipSyncError("OutputTimingOrGeometryMismatch")
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        raise LipSyncError("IncompleteOutputMetadata") from None
    run_ffmpeg(ctx, [settings["ffmpeg"], "-v", "error", "-xerror", "-i", str(output),
                    "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], duration)
    return {"decode_ok": True, "video_duration": vd, "audio_duration": ad,
            "av_start_delta": abs(vs-als), "video_frames": frames, "fps": fps,
            "width": width, "height": height, "perceptual_sync": "manual_review_required"}


def check_stage(ctx, params):
    artifacts = ctx.inputs["lip_sync"]["artifacts"]
    manifest_path = next(Path(a["path"]) for a in artifacts if Path(a["path"]).name == "portrait.json")
    manifest = read_json(manifest_path)
    validate(manifest, "lip-sync-manifest")
    output = manifest_path.parent / manifest["video"]
    if output.parent.resolve() != manifest_path.parent.resolve() or digest_file(output) != manifest["sha256"]:
        raise LipSyncError("ModelOutputChanged")
    v = manifest["verification"]
    result = verify_video(ctx, output, params["settings"], manifest["duration"], v["width"], v["height"])
    write_json(ctx.output_dir / "verification.json", {**result, "human_review": "pending"})
    return {"technical_verification": "passed", "human_review": "pending"}
