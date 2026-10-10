"""Cloud-neutral contracts and validated, secret-free configuration.

Providers return durable receipts before any result download. No provider may
silently emulate an unsupported operation or retry a billable POST.
"""

import math
import re
from dataclasses import asdict, dataclass
from typing import Protocol

from .errors import CreatorDNAError
from .storage import read_json


class TTSError(CreatorDNAError):
    def __init__(self, code, *, rejected=False, retry_after=0):
        # Never use remote messages, URLs, HTTP exception text or request bodies.
        self.code = code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(code)) else "ProviderError"
        self.error_type = self.code
        self.rejected = rejected
        self.retry_after = retry_after
        self.retryable = False  # Task engine must not repeat paid requests itself.
        super().__init__(f"TTS: {self.code}")


def unsupported():
    raise TTSError("Unsupported")


@dataclass(frozen=True)
class Capability:
    provider: str
    models: tuple
    max_characters: int
    formats: tuple
    sample_rates: tuple
    emotions: tuple = ()
    speed_range: tuple = (1.0, 1.0)
    clone: bool = True
    list_voices: bool = True
    delete_voice: bool = True
    query_voice: bool = False
    update_voice: bool = False
    async_synthesis: bool = False
    timestamps: bool = False
    concurrency: int = 1

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SynthesisReceipt:
    format: str
    audio_hex: str | None = None
    audio_url: str | None = None
    subtitle_url: str | None = None
    request_id: str | None = None


class TTSProvider(Protocol):
    name: str
    scope: str  # endpoint + credential environment name; voices are account scoped

    def capabilities(self) -> Capability: ...
    def check_credentials(self) -> None: ...
    def validate_voice_name(self, name: str) -> None: ...
    def create_voice(self, reference, name, consent: dict) -> dict: ...
    def list_voices(self, page: int = 0) -> dict: ...
    def delete_voice(self, voice_id: str) -> None: ...
    def query_voice(self, voice_id: str) -> dict: ...
    def update_voice(self, voice_id: str, reference) -> dict: ...
    def synthesize(self, text: str, voice_id: str) -> SynthesisReceipt: ...
    def download(self, receipt: SynthesisReceipt) -> tuple[bytes, object | None]: ...
    def query_task(self, task_id: str) -> dict: ...


DEFAULTS = {"speed": 1.0, "emotion": None, "format": "wav", "sample_rate": 24000,
            "segment_chars": 500, "paragraph_pause": 0.35, "request_timeout": 90,
            "max_attempts": 2, "retry_delay": 2.0, "request_interval": 1.1,
            "subtitles": False, "ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}


def config(value):
    raw = read_json(value) if not isinstance(value, dict) else dict(value)
    allowed = set(DEFAULTS) | {"provider", "model", "base_url", "api_key_env"}
    if set(raw) - allowed:
        raise TTSError("InvalidConfig")
    result = {**DEFAULTS, **raw}
    if result.get("provider") not in {"qwen", "minimax"}:
        raise TTSError("InvalidProvider")
    if not isinstance(result.get("model"), str):
        raise TTSError("InvalidModel")
    for key, low, high in [("speed", .5, 2), ("paragraph_pause", 0, 10),
                           ("request_timeout", 1, 600), ("retry_delay", 0, 60),
                           ("request_interval", 1, 60), ("segment_chars", 1, 9999),
                           ("max_attempts", 1, 3)]:
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or not low <= number <= high:
            raise TTSError("InvalidConfig")
    for key in ("segment_chars", "max_attempts", "sample_rate"):
        if isinstance(result[key], bool) or not isinstance(result[key], int):
            raise TTSError("InvalidConfig")
    if not isinstance(result["subtitles"], bool) or result["format"] not in {"wav", "mp3", "flac"}:
        raise TTSError("InvalidConfig")
    for key in ("ffmpeg", "ffprobe"):
        if not isinstance(result[key], str) or not result[key]:
            raise TTSError("InvalidConfig")
    env = result.setdefault("api_key_env", "DASHSCOPE_API_KEY" if result["provider"] == "qwen" else "MINIMAX_API_KEY")
    if not isinstance(env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", env):
        raise TTSError("InvalidConfig")
    return result


def require_consent(consent, provider, scope):
    if (not isinstance(consent, dict) or consent.get("authorized") is not True
            or consent.get("cloud_processing") is not True
            or consent.get("provider") != provider or consent.get("scope") != scope
            or not isinstance(consent.get("confirmation"), str) or not consent["confirmation"].strip()):
        raise TTSError("ConsentRequired")


def make_provider(settings, transport=None):
    from .tts_providers import MiniMaxProvider, QwenProvider
    settings = config(settings)
    return {"qwen": QwenProvider, "minimax": MiniMaxProvider}[settings["provider"]](settings, transport)
