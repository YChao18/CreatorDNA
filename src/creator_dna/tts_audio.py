"""Deterministic local segmentation, media checks, conversion and assembly."""

import json
import math
import re
import shutil
import subprocess
import wave
from pathlib import Path

from .tts import TTSError


def tool(command, timeout=120):
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise TTSError("MediaToolFailed") from None
    if result.returncode:
        raise TTSError("MediaToolFailed")
    return result.stdout


def dependencies(settings):
    if not all(shutil.which(settings[key]) for key in ("ffmpeg", "ffprobe")):
        raise TTSError("MissingFFmpeg")


def metadata(path, settings):
    try:
        data = json.loads(tool([settings["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
        audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
        if len(audio) != 1:
            raise ValueError()
        stream = audio[0]
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        return {"duration_seconds": duration, "sample_rate": int(stream["sample_rate"]),
                "channels": int(stream["channels"]), "codec": stream["codec_name"],
                "format": data["format"]["format_name"], "bytes": Path(path).stat().st_size}
    except (KeyError, ValueError, TypeError):
        raise TTSError("InvalidAudioMetadata") from None


def validate_reference(path, settings, provider):
    path = Path(path)
    if path.suffix.lower() not in {".wav", ".mp3", ".m4a"} or not path.is_file():
        raise TTSError("InvalidReference")
    m = metadata(path, settings)
    if provider == "qwen":
        valid = 3 <= m["duration_seconds"] <= 60 and m["bytes"] <= 10 * 1024**2 and m["channels"] == 1 and m["sample_rate"] >= 24000
        if path.suffix.lower() == ".wav":
            valid = valid and m["codec"] == "pcm_s16le"
    else:
        valid = 10 <= m["duration_seconds"] <= 300 and m["bytes"] <= 20 * 1024**2
    if not valid:
        raise TTSError("ReferenceRequirementsNotMet")
    return m


def split_text(text, limit, pause):
    if not isinstance(text, str) or not text.strip():
        raise TTSError("EmptyText")
    segments = []
    for paragraph in re.finditer(r"[^\r\n]+", text):
        start, end = paragraph.start(), paragraph.end()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        while start < end:
            cut = min(start + limit, end)
            if cut < end:
                boundaries = list(re.finditer(r"[。！？!?；;，,\.\s]", text[start:cut]))
                if boundaries and boundaries[-1].end() >= limit // 2:
                    cut = start + boundaries[-1].end()
            segments.append({"index": len(segments), "text": text[start:cut], "text_start": start,
                             "text_end": cut, "pause_after": pause if cut == end else 0})
            start = cut
    segments[-1]["pause_after"] = 0
    return segments


def normalize(source, target, settings):
    tool([settings["ffmpeg"], "-v", "error", "-nostdin", "-y", "-xerror", "-i", str(source),
          "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(settings["sample_rate"]),
          "-c:a", "pcm_s16le", str(target)])
    return wave_metadata(target)


def wave_metadata(path):
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getnframes() <= 0:
                raise ValueError()
            return {"frames": audio.getnframes(), "sample_rate": audio.getframerate(),
                    "duration_seconds": audio.getnframes() / audio.getframerate(), "channels": 1}
    except (OSError, EOFError, wave.Error, ValueError):
        raise TTSError("InvalidWAV") from None


def assemble(paths, segments, target, settings):
    rate, offset, mapping = settings["sample_rate"], 0, []
    with wave.open(str(target), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        for path, segment in zip(paths, segments):
            m = wave_metadata(path)
            if m["sample_rate"] != rate:
                raise TTSError("AudioFormatMismatch")
            with wave.open(str(path), "rb") as audio:
                output.writeframes(audio.readframes(audio.getnframes()))
            frames = m["frames"]
            silence = round(segment["pause_after"] * rate)
            mapping.append({**segment, "start_seconds": offset / rate, "end_seconds": (offset + frames) / rate,
                            "duration_seconds": frames / rate, "pause_after_seconds": silence / rate,
                            "timing_basis": "measured_audio_segment_boundaries", "word_timestamps": None})
            output.writeframes(b"\0\0" * silence)
            offset += frames + silence
    return mapping


def export_audio(wav, output, settings):
    if settings["format"] == "wav":
        shutil.copyfile(wav, output)
    else:
        codec = "libmp3lame" if settings["format"] == "mp3" else "flac"
        tool([settings["ffmpeg"], "-v", "error", "-nostdin", "-y", "-i", str(wav),
              "-c:a", codec, str(output)])
    m = metadata(output, settings)
    tool([settings["ffmpeg"], "-v", "error", "-nostdin", "-xerror", "-i", str(output), "-f", "null", "-"])
    return m
