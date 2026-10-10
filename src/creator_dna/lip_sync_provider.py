"""Real, local MuseTalk adapter; no cloud upload, synthetic fallback or shell I/O."""

import json
import os
import subprocess
import time
from pathlib import Path

from .lip_sync import LipSyncCapability, LipSyncError, LipSyncResult
from .storage import digest_file, now, read_json, write_json


MODEL_FILES = ("musetalkV15/musetalk.json", "musetalkV15/unet.pth",
               "sd-vae/config.json", "sd-vae/diffusion_pytorch_model.bin",
               "whisper/config.json", "whisper/pytorch_model.bin", "whisper/preprocessor_config.json",
               "dwpose/dw-ll_ucoco_384.pth", "face-parse-bisent/79999_iter.pth",
               "face-parse-bisent/resnet18-5c106cde.pth", "s3fd/s3fd-619a316812.pth")
CODE_FILES = ("musetalk/utils/preprocessing.py", "musetalk/utils/utils.py", "musetalk/utils/blending.py",
              "musetalk/utils/audio_processor.py", "musetalk/utils/face_parsing/__init__.py",
              "musetalk/utils/face_detection/detection/sfd/sfd_detector.py",
              "musetalk/utils/dwpose/rtmpose-l_8xb32-270e_coco-ubody-wholebody-384x288.py")


class MuseTalkProvider:
    name = "musetalk"

    def __init__(self, settings):
        self.settings = settings
        self.runner = Path(__file__).with_name("musetalk_runner.py")

    def capabilities(self):
        return LipSyncCapability(self.name, "v15", ("video", "image"), "local", True, False,
            {"cuda": True, "recommended_fps": 25, "face_region": [256, 256],
             "upstream_reported_low_vram_example_gb": 4,
             "vram_note": "4 GB 是上游 fp16/8 秒演示报告，不是本适配器的显存保证；按实际峰值验证。",
             "blackwell": "CUDA 12.8+ / PyTorch 2.7+ with sm_120 kernel support; MMLab extensions also need verification",
             "weights": list(MODEL_FILES)},
            ("batch_size", "float16", "extra_margin", "parsing_mode", "seed", "gpu_id"))

    def environment(self):
        import tempfile
        repo = Path(self.settings["repository"])
        missing = [str(repo / "models" / f) for f in MODEL_FILES if not (repo / "models" / f).is_file()]
        missing += [str(repo / f) for f in CODE_FILES if not (repo / f).is_file()]
        # Read-only external runtime check; config lives only in a temporary file.
        with tempfile.TemporaryDirectory(prefix="creator-dna-lipsync-") as temp:
            config_file = Path(temp) / "config.json"
            write_json(config_file, self.settings)
            try:
                raw = subprocess.run([self.settings["python"], str(self.runner), "doctor", str(config_file)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
                runtime = json.loads(raw.stdout) if raw.returncode == 0 else {"runtime_error": "RuntimeProbeFailed"}
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                runtime = {"runtime_error": type(exc).__name__}
        import shutil
        tools = {k: shutil.which(self.settings[k]) for k in ("ffmpeg", "ffprobe")}
        return {"provider": self.name, "repository": str(repo), "missing_files": missing,
                "runtime": runtime, "tools": tools,
                "ready": not missing and not runtime.get("runtime_error") and not runtime.get("missing_modules")
                         and runtime.get("cuda", False) and all(tools.values()),
                "note": "readiness checks prerequisites only; real inference and human quality review still required"}

    def fingerprint(self):
        repo = Path(self.settings["repository"])
        paths = [repo / "models" / f for f in MODEL_FILES] + [repo / f for f in CODE_FILES]
        if any(not p.is_file() for p in paths):
            raise LipSyncError("ModelCodeOrWeightsMissing")
        # Include all Python helpers and model weights, not just the top-level entry point.
        paths.extend(repo.glob("musetalk/**/*.py"))
        paths.extend((repo / "models").rglob("*.json"))
        return {str(p.relative_to(repo)): digest_file(p) for p in sorted(set(paths))}

    def status(self, job_directory):
        path = Path(job_directory) / "provider-status.json"
        return read_json(path) if path.exists() else {"status": "not_started"}

    def generate(self, request, context):
        if request.reference_type not in self.capabilities().reference_types:
            raise LipSyncError("UnsupportedReferenceType")
        if request.consent.get("authorized") is not True or request.consent.get("processing") != "local":
            raise LipSyncError("LocalConsentRequired")
        directory = context.output_dir / "backend"
        directory.mkdir()
        job = {"settings": self.settings, "reference": str(request.reference), "reference_type": request.reference_type,
               "audio": str(request.audio), "directory": str(directory)}
        write_json(directory / "job.json", job)
        write_json(directory / "provider-status.json", {"status": "running", "started_at": now()})
        started = time.monotonic()
        context.report("MuseTalk：启动真实本地模型；加载和定位总工作量未知", basis="model loading; total unknown")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        command = [self.settings["python"], str(self.runner), "generate", str(directory / "job.json")]
        try:
            with (context.attempt_dir / "musetalk.stderr.log").open("w", encoding="utf-8") as stderr, \
                    (context.attempt_dir / "musetalk.stdout.log").open("w", encoding="utf-8") as stdout:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr, text=True,
                                           encoding="utf-8", errors="replace", env=env)
                try:
                    for line in process.stdout:
                        context.check_cancelled()
                        stdout.write(line)
                        stdout.flush()
                        if line.startswith("CREATORDNA_LIPSYNC "):
                            event = json.loads(line.split(" ", 1)[1])
                            # Counts describe the active sub-operation; no fictitious stage percentage.
                            context.report(f"MuseTalk：{event['phase']}" +
                                (f" {event['completed']}/{event['total']} 帧" if "completed" in event else ""),
                                completed=event.get("completed"), unit=event.get("unit"),
                                basis=f"actual {event['phase']} sub-operation; whole-stage total unknown")
                            write_json(directory / "provider-status.json", {"status": "running", **event})
                    code = process.wait()
                finally:
                    process.stdout.close()
                    if process.poll() is None:
                        from .tasks import stop_process_tree
                        stop_process_tree(process)
            if code:
                raise LipSyncError("ModelInferenceFailed")
            output, metrics = directory / "model.mp4", directory / "metrics.json"
            if not output.is_file() or output.stat().st_size == 0 or not metrics.is_file():
                raise LipSyncError("MissingModelOutput")
            metadata = {**read_json(metrics), "provider_wall_seconds": round(time.monotonic()-started, 3)}
            if metadata.get("source_looped") is not False or metadata.get("dropped_frames") != 0:
                raise LipSyncError("InvalidModelSequence")
            write_json(directory / "provider-status.json", {"status": "completed", "finished_at": now()})
            return LipSyncResult(output, metadata)
        except BaseException as exc:
            write_json(directory / "provider-status.json", {"status": "failed", "finished_at": now(),
                       "error": getattr(exc, "error_type", type(exc).__name__)})
            raise
