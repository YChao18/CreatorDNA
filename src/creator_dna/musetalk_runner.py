"""Standalone bridge executed by the isolated MuseTalk Python, not CreatorDNA's.

Uses upstream model APIs, replaces its shell-based media I/O and ping-pong loop.
All generated media/cache/logs live in the attempt directory. No network access.
"""

import importlib.util
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def emit(value):
    print("CREATORDNA_LIPSYNC " + json.dumps(value, allow_nan=False), flush=True)


def doctor(settings):
    missing = []
    for module in ("torch", "cv2", "numpy", "diffusers", "transformers", "librosa", "omegaconf",
                   "mmpose", "mmdet", "mmcv", "einops", "face_detection"):
        if importlib.util.find_spec(module) is None:
            missing.append(module)
    data = {"python": sys.version.split()[0], "missing_modules": missing, "cuda": False}
    if "torch" not in missing:
        try:
            import torch
            data.update(torch=torch.__version__, cuda_runtime=torch.version.cuda,
                        supported_architectures=torch.cuda.get_arch_list())
            if torch.cuda.is_available() and settings["gpu_id"] < torch.cuda.device_count():
                device = settings["gpu_id"]
                torch.cuda.set_device(device)
                # A real CUDA kernel catches old wheels that cannot run sm_120.
                matrix = torch.ones((8, 8), device=f"cuda:{device}")
                (matrix @ matrix).sum().item()
                torch.cuda.synchronize()
                free, total = torch.cuda.mem_get_info(device)
                data.update(cuda=True, gpu=torch.cuda.get_device_name(device),
                            compute_capability=list(torch.cuda.get_device_capability(device)),
                            free_vram_bytes=free, total_vram_bytes=total)
        except Exception as exc:
            data["cuda_error"] = type(exc).__name__
    if not missing:
        try:
            # Detect incompatible native MMLab extensions before inference.
            with contextlib.redirect_stdout(io.StringIO()):
                import mmcv.ops
                from mmpose.apis import inference_topdown
                from transformers import WhisperModel
                from diffusers import AutoencoderKL
        except Exception as exc:
            data["runtime_error"] = type(exc).__name__
    return data


def no_network(*args, **kwargs):
    raise RuntimeError("OfflineModelCacheRequired")


def run(job):
    settings = job["settings"]
    output = Path(job["directory"])
    # Upstream uses relative model/config paths; only reads the external checkout.
    repository = Path(settings["repository"])
    sys.path.insert(0, str(repository))
    sys.path.insert(0, str(repository / "musetalk/utils"))
    os.chdir(repository)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HOME=str(output / "cache/huggingface"),
                      TORCH_HOME=str(output / "cache/torch"),
                      XDG_CACHE_HOME=str(output / "cache"),
                      MPLCONFIGDIR=str(output / "cache/matplotlib"),
                      CUDA_VISIBLE_DEVICES=str(settings["gpu_id"]))
    cache = output / "cache/torch/hub/checkpoints"
    cache.mkdir(parents=True)
    # The face_detection dependency may otherwise download S3FD on import.
    detector = repository / "models/s3fd/s3fd-619a316812.pth"
    shutil.copyfile(detector, cache / detector.name)
    socket.socket.connect = no_network
    socket.create_connection = no_network
    import torch
    import cv2
    import numpy as np
    # Upstream read_imgs uses imread, which cannot reliably open Windows Unicode
    # paths. Preserve image data while replacing only filesystem decoding.
    def unicode_imread(path, flags=cv2.IMREAD_COLOR):
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), flags)
    cv2.imread = unicode_imread
    from transformers import WhisperModel
    from musetalk.utils.utils import load_all_model, datagen
    from musetalk.utils.preprocessing import get_landmark_and_bbox, coord_placeholder
    from musetalk.utils.audio_processor import AudioProcessor
    from musetalk.utils.face_parsing import FaceParsing
    from musetalk.utils.blending import get_image

    started = time.monotonic()
    torch.manual_seed(settings["seed"])
    np.random.seed(settings["seed"])
    device = torch.device("cuda:0")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDARequired")
    torch.cuda.reset_peak_memory_stats(device)
    emit({"phase": "loading_models"})
    vae, unet, pe = load_all_model(unet_model_path="models/musetalkV15/unet.pth",
        unet_config="models/musetalkV15/musetalk.json", device=device)
    if settings["float16"]:
        pe, vae.vae, unet.model = pe.half(), vae.vae.half(), unet.model.half()
    pe, vae.vae, unet.model = pe.to(device), vae.vae.to(device), unet.model.to(device)
    dtype = unet.model.dtype
    whisper = WhisperModel.from_pretrained("models/whisper", local_files_only=True).to(device=device, dtype=dtype).eval()
    whisper.requires_grad_(False)
    fp = FaceParsing(left_cheek_width=90, right_cheek_width=90)
    processor = AudioProcessor(feature_extractor_path="models/whisper")
    frames_dir = output / "source_frames"
    frames_dir.mkdir()
    if job["reference_type"] == "video":
        subprocess.run([settings["ffmpeg"], "-v", "error", "-nostdin", "-n", "-i", job["reference"],
                        "-an", "-start_number", "0", str(frames_dir / "%08d.png")], check=True)
        image_paths = [str(p) for p in sorted(frames_dir.glob("*.png"))]
    else:
        image_paths = [job["reference"]]
    emit({"phase": "face_landmarks"})
    coords, frames = get_landmark_and_bbox(image_paths, 0)
    if not frames or len(coords) != len(frames) or any(tuple(b) == coord_placeholder for b in coords):
        raise RuntimeError("FaceMissingNoFrameSkippingAllowed")
    feature, audio_length = processor.get_audio_feature(job["audio"])
    chunks = processor.get_whisper_chunk(feature, device, dtype, whisper, audio_length, fps=settings["fps"],
                                         audio_padding_length_left=2, audio_padding_length_right=2)
    if len(chunks) == 0:
        raise RuntimeError("EmptyAudioFeatures")
    if job["reference_type"] == "video" and len(chunks) > len(frames):
        raise RuntimeError("InsufficientSourceFramesNoLoopAllowed")
    n = len(chunks)
    wanted = len(frames) if job["reference_type"] == "image" else n
    latents, boxes = [], []
    with torch.inference_mode():
        for i in range(wanted):
            frame = frames[i]
            x1, y1, x2, y2 = map(int, coords[i])
            y2 = min(y2 + settings["extra_margin"], frame.shape[0])
            if not 0 <= x1 < x2 <= frame.shape[1] or not 0 <= y1 < y2 <= frame.shape[0]:
                raise RuntimeError("InvalidFaceBounds")
            crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents.append(vae.get_latents_for_unet(crop))
            boxes.append([x1, y1, x2, y2])
            emit({"phase": "face_encoding", "completed": i+1, "total": wanted, "unit": "frames"})
        generated = output / "generated_frames"
        generated.mkdir()
        completed = 0
        batches = datagen(chunks, latents, batch_size=settings["batch_size"], device=device)
        timestep = torch.tensor([0], device=device)
        for audio_batch, latent_batch in batches:
            predictions = unet.model(latent_batch.to(device=device, dtype=dtype), timestep,
                encoder_hidden_states=pe(audio_batch.to(device))).sample
            for res in vae.decode_latents(predictions):
                idx = 0 if job["reference_type"] == "image" else completed
                x1, y1, x2, y2 = boxes[idx]
                face = cv2.resize(res.astype(np.uint8), (x2-x1, y2-y1))
                combined = get_image(frames[idx].copy(), face, boxes[idx], mode=settings["parsing_mode"], fp=fp)
                # imencode/tofile supports Unicode Windows workspace paths.
                ok, encoded = cv2.imencode(".png", combined)
                if not ok:
                    raise RuntimeError("FrameWriteFailed")
                encoded.tofile(str(generated / f"{completed:08d}.png"))
                completed += 1
            emit({"phase": "inference", "completed": completed, "total": n, "unit": "frames"})
        if completed != n:
            raise RuntimeError("IncompleteInference")
    emit({"phase": "encoding"})
    video = output / "model.mp4"
    subprocess.run([settings["ffmpeg"], "-v", "error", "-nostdin", "-n", "-framerate", str(settings["fps"]),
        "-i", str(generated / "%08d.png"), "-frames:v", str(n), "-an", "-c:v", "libx264",
        "-crf", "18", "-pix_fmt", "yuv420p", str(video)], check=True)
    torch.cuda.synchronize()
    metrics = {"model": "MuseTalk-1.5", "inferred_frames": n,
        "elapsed_seconds": round(time.monotonic()-started, 3),
        "gpu": torch.cuda.get_device_name(device), "torch": torch.__version__,
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(device),
        "peak_gpu_reserved_bytes": torch.cuda.max_memory_reserved(device),
        "motion": "preserved_source_sequence" if job["reference_type"] == "video" else "static_reference_no_body_motion",
        "source_looped": False, "dropped_frames": 0}
    (output / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    # Frames are temporary run artifacts, not deliverables.
    shutil.rmtree(frames_dir)
    shutil.rmtree(generated)
    emit({"phase": "completed"})


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if sys.argv[1] == "doctor":
        sys.path.insert(0, payload["repository"])
        sys.path.insert(0, str(Path(payload["repository"]) / "musetalk/utils"))
        print(json.dumps(doctor(payload)))
    else:
        try:
            run(payload)
        except Exception as exc:
            emit({"phase": "failed", "error": type(exc).__name__, "detail": str(exc)[:200]})
            raise
