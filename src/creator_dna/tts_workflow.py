"""Task adapters with durable per-segment receipts and bounded paid attempts."""

import hashlib
import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from .contracts import validate
from .storage import Workspace, digest_file, now, read_json, write_json
from .tasks import TaskManager, stage
from .tts import SynthesisReceipt, TTSError, config, make_provider
from .tts_audio import assemble, dependencies, export_audio, normalize, split_text, wave_metadata
from .voice_assets import VoiceAssets


def pause(ctx, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.check_cancelled()
        time.sleep(min(.1, max(0, deadline-time.monotonic())))


def create_synthesis_task(workspace, creator, asset_id, settings, text, timeout=1800):
    settings = config(settings)
    provider = make_provider(settings)
    dependencies(settings)
    asset = VoiceAssets(workspace, creator).usable(asset_id, provider)
    segments = split_text(text, min(settings["segment_chars"], provider.capabilities().max_characters), settings["paragraph_pause"])
    if len(segments) > 1000:
        raise TTSError("TooManySegments")
    params = {"workspace": str(workspace.root), "config": provider.settings, "voice_asset": asset_id,
              "voice_id_snapshot": asset["voice_id"], "text": text}
    return TaskManager(workspace).create([stage("tts", "cloud_tts", params, timeout=timeout,
                                             max_attempts=1, retry_safe=True)], creator, kind="cloud-tts")


def create_voice_task(workspace, creator, reference, settings, name, timeout=600):
    provider = make_provider(settings)
    dependencies(provider.settings)
    data, path = VoiceAssets(workspace, creator).reference(reference)
    from .tts import require_consent
    from .tts_audio import validate_reference
    require_consent(data["consent"], provider.name, provider.scope)
    validate_reference(path, provider.settings, provider.name)
    provider.validate_voice_name(name)
    return TaskManager(workspace).create([stage("tts", "cloud_voice", {
        "workspace": str(workspace.root), "config": provider.settings, "reference": reference,
        "name": name}, timeout=timeout, max_attempts=1, retry_safe=True)], creator, kind="cloud-voice-registration")


def register_voice(ctx, params):
    provider = make_provider(params["config"])
    ctx.report(f"{provider.name} / {provider.settings['model']}：注册音色（与语音合成独立）", basis="remote voice registration; total unknown")
    asset = VoiceAssets(Workspace(params["workspace"]), ctx.creator_id).create(params["reference"], provider, params["name"])
    ctx.check_cancelled()
    write_json(ctx.output_dir / "voice.json", asset)
    return {"voice_asset": asset["asset_id"], "voice_status": asset["status"], "provider": provider.name}


def synthesize_stage(ctx, params, provider=None):
    provider = provider or make_provider(params["config"])
    settings = provider.settings
    assets = VoiceAssets(Workspace(params["workspace"]), ctx.creator_id)
    asset = assets.usable(params["voice_asset"], provider)
    if asset["voice_id"] != params["voice_id_snapshot"]:
        raise TTSError("VoiceChanged")
    dependencies(settings)
    segments = split_text(params["text"], min(settings["segment_chars"], provider.capabilities().max_characters), settings["paragraph_pause"])
    if len(segments) > 1000:
        raise TTSError("TooManySegments")
    cache = ctx.attempt_dir.parent / "checkpoints"
    cache.mkdir(exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps({"config": settings, "voice": asset["voice_id"],
                          "text": params["text"]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    identity = cache / "identity.json"
    if identity.exists() and read_json(identity)["sha256"] != fingerprint:
        raise TTSError("CheckpointInputChanged")
    write_json(identity, {"sha256": fingerprint})
    paths, mapping = [], []
    prefix = f"{provider.name} / {settings['model']}"
    ctx.report(f"{prefix}：准备 {len(segments)} 段文本", completed=0, total=len(segments), unit="segments", basis="verified audio segments")
    for segment in segments:
        ctx.check_cancelled()
        index = segment["index"]
        record_path = cache / f"segment-{index:04d}.json"
        audio_path = cache / f"segment-{index:04d}.wav"
        record = read_json(record_path) if record_path.exists() else {"status": "pending", "attempts": 0}
        if record["status"] in {"inflight", "outcome_unknown"}:
            raise TTSError("BillableOutcomeUnknown")
        if record["status"] == "completed":
            if not audio_path.is_file() or digest_file(audio_path) != record["sha256"]:
                raise TTSError("CheckpointCorrupt")
            wave_metadata(audio_path)
        else:
            while record["status"] in {"pending", "rejected"}:
                if record["attempts"] >= settings["max_attempts"]:
                    raise TTSError("RequestBudgetExhausted")
                provider.check_credentials()
                pause(ctx, max(settings["request_interval"], record.get("retry_after", 0)))
                record.update(status="inflight", attempts=record["attempts"]+1, started_at=now())
                write_json(record_path, record)  # also protects the HTTP response -> receipt crash window
                ctx.report(f"{prefix}：请求段 {index+1}/{len(segments)}", str(index+1), index, len(segments), "segments", "verified audio segments")
                try:
                    receipt = provider.synthesize(segment["text"], asset["voice_id"])
                    record.update(status="receipt", receipt=asdict(receipt))
                    write_json(record_path, record)
                except TTSError as exc:
                    record.update(status="rejected" if exc.rejected else "outcome_unknown", error=exc.code,
                                  retry_after=max(settings["retry_delay"], exc.retry_after))
                    write_json(record_path, record)
                    if not exc.rejected:
                        raise
                    ctx.log(f"{prefix}：服务商明确拒绝请求，受预算限制重试", code=exc.code)
                    continue
            if record["status"] != "receipt":
                raise TTSError("InvalidCheckpoint")
            assets.mark_used(asset["asset_id"])
            ctx.check_cancelled()
            # Download/normalization failures leave the receipt for GET-only recovery.
            receipt = SynthesisReceipt(**record["receipt"])
            audio, timestamps = provider.download(receipt)
            # Keep the real media suffix: FFmpeg can prefer the .raw video
            # demuxer on Windows extended paths despite a short WAV header.
            raw = cache / f"segment-{index:04d}.raw.{receipt.format}"
            raw.write_bytes(audio)
            measured = normalize(raw, audio_path, settings)
            record.update(status="completed", sha256=digest_file(audio_path), metadata=measured,
                          timestamps=timestamps, finished_at=now())
            write_json(record_path, record)
            raw.unlink(missing_ok=True)
        copied = ctx.output_dir / f"segment-{index:04d}.wav"
        shutil.copyfile(audio_path, copied)
        paths.append(copied)
        mapping.append({"provider_request_id": record["receipt"].get("request_id"),
                        "provider_timestamps": record.get("timestamps"),
                        "provider_timestamps_unit": "milliseconds" if record.get("timestamps") is not None else None,
                        "timestamps_relative_to": "segment_audio", "audio": copied.name,
                        "sha256": record["sha256"]})
        ctx.report(f"{prefix}：已完成 {index+1}/{len(segments)} 段", str(index+1), index+1, len(segments), "segments", "verified audio segments")
    ctx.check_cancelled()
    ctx.report(f"{prefix}：合并音频、统一格式并完整解码检查", completed=len(segments),
               unit="segments", basis="all segments verified; merge/encoding total work unknown")
    canonical = ctx.output_dir / "timeline.wav"
    timeline = assemble(paths, segments, canonical, settings)
    output = ctx.output_dir / ("speech." + settings["format"])
    measured = export_audio(canonical, output, settings)
    manifest = {"schema_version": "3.0-03", "provider": provider.name, "model": settings["model"],
                "voice_asset": asset["asset_id"], "created_at": now(), "text": params["text"],
                "audio": output.name, "sha256": digest_file(output), "metadata": measured,
                "canonical_audio": canonical.name, "canonical_duration_seconds": wave_metadata(canonical)["duration_seconds"],
                "timing_note": "分段边界为实际 PCM 帧数；服务商字幕保留原始 JSON，未推测词级或句级对齐；压缩成品可能有编码延迟。",
                "segments": [{**t, **m} for t, m in zip(timeline, mapping)]}
    validate(manifest, "tts-manifest")
    write_json(ctx.output_dir / "speech.json", manifest)
    ctx.report(f"{prefix}：音频与分段清单已验证", completed=len(segments), total=len(segments),
               unit="segments", basis="verified segments and final audio")
    return {"provider": provider.name, "model": settings["model"], "segments": len(segments),
            "audio": output.name, "manifest": "speech.json", "metadata": measured}


def resolve_segment(manager, task_id, index, confirmation):
    """Human-reviewed release of exactly one ambiguous paid call; budget unchanged."""
    if not confirmation or not confirmation.strip() or index < 0:
        raise TTSError("ConfirmationRequired")
    folder = manager.folder(task_id)
    with manager._lock(folder):
        state = manager.status(task_id)
        if state["kind"] not in {"cloud-tts", "video-production"} or state["status"] not in {"failed", "cancelled"}:
            raise TTSError("TaskNotStopped")
        path = folder / "stages" / "tts" / "checkpoints" / f"segment-{index:04d}.json"
        record = read_json(path)
        if record["status"] not in {"inflight", "outcome_unknown", "receipt"}:
            raise TTSError("NoAmbiguousRequest")
        params = next(s["params"] for s in read_json(folder / "task.json")["stages"] if s["id"] == "tts")
        settings = params["config"]["tts"] if state["kind"] == "video-production" else params["config"]
        if record["attempts"] >= settings["max_attempts"]:
            raise TTSError("RequestBudgetExhausted")
        if "receipt" in record:
            record.setdefault("previous_receipts", []).append(record.pop("receipt"))
        record.update(status="pending", resolution={"confirmed_at": now(), "confirmation": confirmation,
                                                    "additional_billing_accepted": True})
        write_json(path, record)
        return {"task_id": task_id, "segment": index, "status": "pending", "attempts_used": record["attempts"]}
