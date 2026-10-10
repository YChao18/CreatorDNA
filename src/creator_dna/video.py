"""Read-only local video measurements. No AI, ASR, network or source writes."""

import hashlib
import math
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from .errors import CreatorDNAError
from .input import VIDEO_EXTENSIONS, Source, validate_input_folder
from .storage import digest_file


def scan_videos(folder, workspace, recursive=False):
    folder = validate_input_folder(folder, workspace)
    paths = sorted((p for p in (folder.rglob("*") if recursive else folder.iterdir())
                    if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS),
                   key=lambda p: p.relative_to(folder).as_posix().casefold())
    if any(p.is_symlink() or folder not in p.resolve().parents for p in paths):
        raise CreatorDNAError("视频素材包含链接或文件夹外引用。")
    if not paths:
        raise CreatorDNAError("文件夹中没有支持的视频。")
    # Unlike transcript input, same-stem videos in different containers are valid.
    return folder, paths


def video_source(path, folder):
    relative = path.relative_to(folder).as_posix()
    return Source(path, relative, "s-" + hashlib.sha256(relative.encode()).hexdigest()[:16],
                  digest_file(path), None)


def ratio(value, default=None):
    try:
        result = Fraction(str(value).replace(":", "/"))
        return result if result > 0 else default
    except (ValueError, ZeroDivisionError):
        return default


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def metadata_from_probe(data):
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise CreatorDNAError("文件没有可分析的视频流（封面图不算视频）。")
    video = videos[0]
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    duration = number(video.get("duration")) or number(data.get("format", {}).get("duration"))
    if width <= 0 or height <= 0 or duration is None or duration <= 0:
        raise CreatorDNAError("视频尺寸或时长无效；不猜测缺失的时长。")
    rotation = number(video.get("tags", {}).get("rotate")) or 0.0
    for side in video.get("side_data_list", []):
        if "rotation" in side:
            rotation = number(side["rotation"]) or 0.0
    rotation %= 360
    sar = ratio(video.get("sample_aspect_ratio"), Fraction(1))
    unrotated = Fraction(width, height) * sar
    angle = math.radians(rotation)
    display_w = abs(width * float(sar) * math.cos(angle)) + abs(height * math.sin(angle))
    display_h = abs(width * float(sar) * math.sin(angle)) + abs(height * math.cos(angle))
    dar = 1 / unrotated if rotation in (90, 270) else unrotated if rotation in (0, 180) else None
    avg, nominal = ratio(video.get("avg_frame_rate")), ratio(video.get("r_frame_rate"))
    fps = avg or nominal
    audios = [{"stream_index": s["index"], "codec": s.get("codec_name"),
               "sample_rate": int(s["sample_rate"]) if str(s.get("sample_rate", "")).isdigit() else None,
               "channels": s.get("channels")} for s in streams if s.get("codec_type") == "audio"]
    return {"video_stream_index": video["index"], "width": width, "height": height,
            "display_width": round(display_w, 6), "display_height": round(display_h, 6),
            "display_aspect_ratio": str(dar) if dar else None,
            "display_aspect_ratio_value": round(display_w / display_h, 8),
            "sample_aspect_ratio": str(sar), "sample_aspect_ratio_assumed": ratio(video.get("sample_aspect_ratio")) is None,
            "reported_display_aspect_ratio": video.get("display_aspect_ratio"),
            "rotation_degrees": rotation, "fps": float(fps) if fps else None,
            "avg_frame_rate": str(avg) if avg else None, "nominal_frame_rate": str(nominal) if nominal else None,
            "variable_frame_rate_hint": bool(avg and nominal and avg != nominal),
            "duration": duration, "video_codec": video.get("codec_name"),
            "pixel_format": video.get("pix_fmt"), "audio_streams": audios,
            "subtitle_streams": [s["index"] for s in streams if s.get("codec_type") == "subtitle"],
            "container": data.get("format", {}).get("format_name")}


class VideoAnalyzer(Protocol):
    """Explicit local provider boundary for future analyzers."""

    def analyze(self, source: Source, destination: Path) -> dict: ...


class LocalVideoAnalyzer:
    def __init__(self, ffmpeg="ffmpeg", ffprobe="ffprobe", scene_threshold=0.3,
                 sample_interval=2.0, max_frames=120, segment_seconds=12.0, timeout=600,
                 face_detection=True, progress_callback=None):
        values = (scene_threshold, sample_interval, segment_seconds, timeout)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise CreatorDNAError("视频分析参数必须为有限数值。")
        if not 0 < scene_threshold < 1 or min(sample_interval, segment_seconds, timeout) <= 0:
            raise CreatorDNAError("scene-threshold 须在 0–1 内；间隔、片段长度、超时须大于 0。")
        if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= 2000:
            raise CreatorDNAError("max-frames 须为 1–2000 的整数。")
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.scene_threshold, self.sample_interval = scene_threshold, sample_interval
        self.max_frames, self.segment_seconds, self.timeout = max_frames, segment_seconds, timeout
        self.face_detection = face_detection
        self._face = None
        self.progress_callback = progress_callback

    def report(self, message, current_object=None, completed=None, total=None, unit=None, basis="local operation status"):
        if self.progress_callback:
            self.progress_callback(message, current_object, completed, total, unit, basis)

    def dependencies(self):
        for name in (self.ffmpeg, self.ffprobe):
            if not shutil.which(name):
                raise CreatorDNAError(f"找不到本地工具：{name}。请安装或指定完整路径。")
        try:
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            raise CreatorDNAError('关键帧/联系图需要 Pillow：pip install -e ".[video]"') from exc

    def command(self, args):
        self.report("本地工具执行中（无整体百分比）", Path(args[0]).name)
        try:
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CreatorDNAError(f"本地视频工具执行失败：{exc}") from exc
        if result.returncode:
            raise CreatorDNAError(f"本地视频工具失败：{result.stderr[-2000:]}")
        return result

    def detect_faces(self, image):
        if not self.face_detection:
            return None
        if self._face is None:
            try:
                import cv2
                cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
                if cascade.empty():
                    raise ImportError("人脸检测器不可用")
                self._face = (cv2, cascade)
            except ImportError:
                self._face = False
        if self._face is False:
            return None
        import numpy as np
        cv2, cascade = self._face
        gray = np.array(image.convert("L"))
        boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        w, h = image.size
        return [{"x": round(int(x) / w, 6), "y": round(int(y) / h, 6),
                 "width": round(int(bw) / w, 6), "height": round(int(bh) / h, 6),
                 "sharpness_proxy": round(float(cv2.Laplacian(gray[y:y+bh, x:x+bw], cv2.CV_64F).var()), 3)}
                for x, y, bw, bh in boxes]

    def analyze(self, source, destination):
        import json
        from PIL import Image, ImageDraw, ImageStat

        self.dependencies()
        destination.mkdir(parents=True, exist_ok=True)
        raw = json.loads(self.command([self.ffprobe, "-v", "error", "-show_streams", "-show_format",
                                      "-of", "json", str(source.path)]).stdout)
        from .storage import write_json, write_text
        write_json(destination / "probe.json", raw)
        metadata = metadata_from_probe(raw)
        duration, stream = metadata["duration"], metadata["video_stream_index"]
        self.report("视频元数据已读取，开始全片切镜检测", source.relative_path)
        source_start = number(next(s for s in raw["streams"] if s["index"] == stream).get("start_time")) or 0
        scene = self.command([self.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-xerror",
                              "-i", str(source.path), "-map", f"0:{stream}", "-an", "-sn",
                              "-vf", f"setpts=PTS-STARTPTS,select='gt(scene,{self.scene_threshold})',showinfo", "-fps_mode", "vfr",
                              "-f", "null", "-"])
        write_text(destination / "scene-detection.log", scene.stderr)
        # Filter timestamps are explicitly normalized to the first decoded frame.
        cuts = sorted({round(float(t), 6) for t in re.findall(r"pts_time:([\d.eE+\-]+)", scene.stderr)
                       if 0.001 < float(t) < duration - 0.001})
        if len(cuts) > 10000:
            raise CreatorDNAError("候选镜头超过 10000；请提高 scene-threshold 或缩小输入范围。")
        boundaries = [0.0, *cuts, duration]
        shots, segments = [], []
        for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            shot_id = f"{source.source_id}-shot-{i:05d}"
            shots.append({"shot_id": shot_id, "start": start, "end": end, "duration": end-start,
                          "scene_id": None, "scene_type": "unknown", "status": "pending_review",
                          "basis": "FFmpeg scene 像素变化阈值；切换可能是动作/闪光，固定场景数量待确认。"})
            count = math.ceil((end-start) / self.segment_seconds)
            for n in range(count):
                a, b = start+n*self.segment_seconds, min(end, start+(n+1)*self.segment_seconds)
                segments.append({"candidate_id": f"{shot_id}-clip-{n:05d}", "shot_id": shot_id,
                                 "start": a, "end": b, "duration": b-a})
        # Bound sample memory even on very long files; uncovered candidates remain unknown.
        regular_count = math.ceil(duration / self.sample_interval)
        times = {(s["start"] + s["end"]) / 2 for s in segments}
        if regular_count <= self.max_frames * 4:
            times.update(min(duration-0.001, i*self.sample_interval) for i in range(regular_count))
        else:
            times.update(duration*(i+0.5)/self.max_frames for i in range(self.max_frames))
        times = sorted(times)
        limited = len(times) > self.max_frames or regular_count > self.max_frames * 4
        if len(times) > self.max_frames:
            times = [times[round(i*(len(times)-1)/(self.max_frames-1))] for i in range(self.max_frames)] if self.max_frames > 1 else [times[len(times)//2]]
        safe_end = max(0, duration - 1 / (metadata["fps"] or 25))
        times = sorted({min(t, safe_end) for t in times})
        # Normalize SAR before rotation. Autorotation can retain an unsuitable SAR
        # for anamorphic portrait sources, so use the measured display transform.
        square_width = metadata["width"] * float(ratio(metadata["sample_aspect_ratio"]))
        factor = min(1.0, 640 / max(square_width, metadata["height"]))
        pre_w = max(2, round(square_width * factor / 2) * 2)
        pre_h = max(2, round(metadata["height"] * factor / 2) * 2)
        filters = [f"scale={pre_w}:{pre_h}", "setsar=1"]
        rotation = metadata["rotation_degrees"]
        if rotation == 90:
            filters.append("transpose=cclock")
        elif rotation == 270:
            filters.append("transpose=clock")
        elif rotation == 180:
            filters.extend(["hflip", "vflip"])
        elif rotation:
            angle = f"-{rotation}*PI/180"
            filters.append(f"rotate={angle}:ow=rotw({angle}):oh=roth({angle}):c=black")
        filters.append("showinfo")
        frames, previous, decoded_times = [], None, set()
        for i, t in enumerate(times):
            self.report(f"采样请求 {i+1}/{len(times)}：{t:.3f}s", source.relative_path,
                        i, len(times), "sample_requests", "completed sampling requests; not whole analysis")
            path = destination / f"frame-{i:04d}.png"
            decoded = self.command([self.ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-xerror",
                                   "-noautorotate", "-copyts", "-ss", str(t), "-i", str(source.path),
                                   "-map", f"0:{stream}", "-frames:v", "1", "-fps_mode", "vfr",
                                   "-vf", ",".join(filters), "-y", str(path)])
            if not path.exists():
                raise CreatorDNAError(f"无法解码时间 {t:.3f}s 的关键帧。")
            with Image.open(path) as opened:
                img = opened.convert("RGB")
            timestamps = re.findall(r"pts_time:([\d.eE+\-]+)", decoded.stderr)
            if not timestamps:
                raise CreatorDNAError("关键帧缺少实际解码时间戳。")
            actual_time = max(0.0, float(timestamps[0]) - source_start)
            if actual_time >= duration:
                raise CreatorDNAError("关键帧实际时间超出源视频。")
            if round(actual_time, 6) in decoded_times:
                # Two seek requests may resolve to the same frame. Do not turn
                # sampling duplication into evidence of repeated source visuals.
                path.unlink()
                continue
            decoded_times.add(round(actual_time, 6))
            gray = img.convert("L")
            small = list(gray.resize((16, 16)).tobytes())
            motion = sum(abs(a-b) for a, b in zip(previous["pixels"], small))/256 if previous else None
            frames.append({"frame_id": f"{source.source_id}-frame-{i:04d}", "time": actual_time,
                           "path": path.name, "brightness": round(ImageStat.Stat(gray).mean[0], 3),
                           "faces": self.detect_faces(img), "image_signature": small,
                           "frame_change_proxy": round(motion, 3) if motion is not None else None,
                           "compared_frame_id": previous["id"] if previous else None})
            previous = {"pixels": small, "id": frames[-1]["frame_id"]}
        self.report("采样请求完成，生成联系图", source.relative_path, len(times), len(times),
                    "sample_requests", "completed sampling requests; not whole analysis")
        # Paginated contact sheets retain legible thumbnails and exact sample labels.
        contacts = []
        for page in range(math.ceil(len(frames)/24)):
            batch = frames[page*24:(page+1)*24]
            sheet = Image.new("RGB", (960, math.ceil(len(batch)/4)*204), "#202020")
            draw = ImageDraw.Draw(sheet)
            for j, frame in enumerate(batch):
                x, y = (j%4)*240, (j//4)*204
                with Image.open(destination / frame["path"]) as opened:
                    thumb = opened.convert("RGB")
                thumb.thumbnail((232, 174))
                sheet.paste(thumb, (x+(240-thumb.width)//2, y+(174-thumb.height)//2))
                label = frame["frame_id"].rsplit("-", 1)[-1]
                draw.text((x+5, y+180), f"{frame['time']:.3f}s / {label}", fill="white")
            contact = f"contact-{page:03d}.jpg"
            sheet.save(destination / contact, quality=90)
            contacts.append(contact)
        if digest_file(source.path) != source.sha256:
            raise CreatorDNAError("分析期间原视频发生变化；本次结果不能加入资产库。")
        return {"metadata": metadata, "shots": shots, "segments": segments, "frames": frames,
                "contacts": contacts, "sampling_limited": limited, "cuts": cuts,
                "face_detector": "opencv-haar-frontal" if self._face else "unavailable_or_disabled"}
