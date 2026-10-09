import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from .contracts import validate
from .errors import CreatorDNAError
from .input import MEDIA_EXTENSIONS
from .storage import digest_file, read_json, write_json


class Transcriber(Protocol):
    @property
    def fingerprint(self) -> str: ...

    def transcribe(self, audio: Path) -> tuple[list[dict], str | None]: ...


class WhisperTranscriber:
    def __init__(self, model="small", language="zh", device="cpu", compute_type="int8"):
        self.model_name, self.language = model, language
        self.device, self.compute_type = device, compute_type
        self._model = None

    @property
    def fingerprint(self):
        return f"faster-whisper:{self.model_name}:{self.language}:{self.device}:{self.compute_type}"

    def transcribe(self, audio):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
            except Exception as exc:
                raise CreatorDNAError("本地 ASR 无法初始化。安装 `pip install -e \".[asr]\"`，"
                                      "或提供同名 txt/srt/json 转写稿；离线时 --asr-model 指向本地模型目录。"
                                      f" 原因：{exc}") from exc
        try:
            segments, info = self._model.transcribe(str(audio), language=self.language, vad_filter=True)
            return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.text.strip()], info.language
        except Exception as exc:
            raise CreatorDNAError(f"ASR 转写失败：{exc}") from exc


def check_segments(segments):
    if not isinstance(segments, list) or not segments:
        raise CreatorDNAError("转写稿必须至少包含一个非空 segment。")
    previous = -1.0
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != {"start", "end", "text"}:
            raise CreatorDNAError("segment 须包含且仅包含 start、end、text。")
        if not isinstance(segment["text"], str) or not segment["text"].strip():
            raise CreatorDNAError("转写片段不能为空。")
        start, end = segment["start"], segment["end"]
        if start is None and end is None:
            continue
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
            raise CreatorDNAError("时间戳须同时为空或同时为有限数字。")
        if start < 0 or end <= start or start < previous:
            raise CreatorDNAError("时间戳须非负、end > start 且按起始时间排序。")
        previous = start


def srt_seconds(value):
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def read_transcript(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig").strip()
    language = None
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            segments = data["segments"]
            language = data.get("language")
        except (ValueError, KeyError, TypeError) as exc:
            raise CreatorDNAError(f"{path} 应为包含 segments 数组的转写 JSON。") from exc
    elif path.suffix.lower() == ".srt":
        segments = []
        for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
            lines = block.splitlines()
            if lines and lines[0].strip().isdigit():
                lines.pop(0)
            match = re.fullmatch(r"\s*(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*", lines[0] if lines else "")
            if not match or len(lines) < 2:
                raise CreatorDNAError(f"SRT 时间行或字幕内容无效：{path}")
            segments.append({"start": srt_seconds(match[1]), "end": srt_seconds(match[2]),
                             "text": "\n".join(lines[1:]).strip()})
    else:
        segments = [{"start": None, "end": None, "text": line.strip()} for line in text.splitlines() if line.strip()]
    check_segments(segments)
    if language is not None and not isinstance(language, str):
        raise CreatorDNAError("转写稿 language 须为字符串或 null。")
    return segments, language


class MediaProcessor:
    def __init__(self, transcriber=None, ffmpeg="ffmpeg"):
        self.transcriber = transcriber
        self.ffmpeg = ffmpeg

    def process(self, source, creator_folder, run):
        transcript_path = source.sidecar or (source.path if source.path.suffix.lower() not in MEDIA_EXTENSIONS else None)
        engine = "sidecar-v1" if transcript_path else self.transcriber.fingerprint if self.transcriber else "disabled"
        transcript_hash = digest_file(transcript_path) if transcript_path else None
        key = hashlib.sha256(json.dumps([source.sha256, transcript_hash, engine], ensure_ascii=False).encode()).hexdigest()
        cache = creator_folder / "transcripts" / source.source_id / f"{key}.json"
        if cache.exists():
            data = read_json(cache)
            validate(data, "transcript")
            check_segments(data["segments"])
            if (data["source_id"] != source.source_id or data["source_sha256"] != source.sha256
                    or data["source_name"] != source.relative_path or data["engine"] != engine):
                raise CreatorDNAError(f"缓存与素材不一致：{cache}。请移出损坏缓存后重跑。")
            if digest_file(source.path) != source.sha256:
                raise CreatorDNAError(f"处理期间素材发生变化：{source.relative_path}，请重试。")
            return data, cache, True
        if transcript_path:
            segments, language = read_transcript(transcript_path)
        else:
            if not self.transcriber:
                raise CreatorDNAError(f"{source.relative_path} 没有同名转写稿；使用 --asr faster-whisper 或提供转写稿。")
            executable = shutil.which(self.ffmpeg)
            if not executable:
                raise CreatorDNAError(f"找不到 FFmpeg：{self.ffmpeg}。请安装或使用 --ffmpeg 指定路径。")
            audio = run / "audio" / f"{source.source_id}.wav"
            audio.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                                         "-i", str(source.path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", str(audio)],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CreatorDNAError(f"音频提取无法完成：{exc}") from exc
            if result.returncode:
                raise CreatorDNAError(f"{source.relative_path} 音频提取失败：{result.stderr[-2000:]}")
            segments, language = self.transcriber.transcribe(audio)
            check_segments(segments)
        # Fail if a source changed while it was being processed; never cache mixed revisions.
        if digest_file(source.path) != source.sha256 or (transcript_path and digest_file(transcript_path) != transcript_hash):
            raise CreatorDNAError(f"处理期间素材发生变化：{source.relative_path}，请重试。")
        data = {"schema_version": "1.0", "source_id": source.source_id, "source_sha256": source.sha256,
                "source_name": source.relative_path, "engine": engine, "language": language, "segments": segments}
        validate(data, "transcript")
        write_json(cache, data)
        return data, cache, False
