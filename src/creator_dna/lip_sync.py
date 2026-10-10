"""Provider-neutral lip-sync contracts. Media selection never calls an AI model."""

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .contracts import validate
from .errors import CreatorDNAError
from .storage import read_json


class LipSyncError(CreatorDNAError):
    def __init__(self, code, *, retryable=False):
        self.error_type = code
        self.retryable = retryable
        super().__init__(f"LipSync: {code}")


@dataclass(frozen=True)
class LipSyncCapability:
    provider: str
    model: str
    reference_types: tuple
    processing: str
    preserves_source_motion: bool
    generates_body_motion: bool
    requirements: dict
    parameters: tuple

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class LipSyncRequest:
    reference: Path
    reference_type: str
    audio: Path
    duration: float
    width: int
    height: int
    consent: dict


@dataclass(frozen=True)
class LipSyncResult:
    video: Path
    metadata: dict


class LipSyncProvider(Protocol):
    name: str

    def capabilities(self) -> LipSyncCapability: ...
    def environment(self) -> dict: ...
    def generate(self, request: LipSyncRequest, context) -> LipSyncResult: ...
    def status(self, job_directory: Path) -> dict: ...


DEFAULTS = {"provider": "musetalk", "model": "v15", "python": "python",
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "gpu_id": 0,
            "batch_size": 2, "float16": True, "extra_margin": 10,
            "parsing_mode": "jaw", "fps": 25, "seed": 42}


def config(value):
    raw = read_json(value) if not isinstance(value, dict) else dict(value)
    result = {**DEFAULTS, **raw}
    validate(result, "lip-sync-config")
    result["repository"] = str(Path(result["repository"]).resolve())
    for key in ("python", "ffmpeg", "ffprobe"):
        if "/" in result[key] or "\\" in result[key]:
            result[key] = str(Path(result[key]).resolve())
    return result


def make_provider(settings):
    from .lip_sync_provider import MuseTalkProvider
    return MuseTalkProvider(config(settings))


def duration_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 5 <= value <= 10:
        raise LipSyncError("DurationMustBe5To10Seconds")
    return float(value)


def require_consent(consent, creator, source_hash, audio_hash, processing="local"):
    validate(consent, "portrait-consent")
    if (consent["creator_id"] != creator or source_hash not in consent["source_sha256"]
            or audio_hash not in consent["audio_sha256"] or not consent["authorized"]
            or consent["processing"] != processing or not consent["confirmation"].strip()):
        raise LipSyncError("PortraitAndAudioConsentRequired")
    # A future cloud adapter MUST require separate provider/endpoint scoped consent.
    if processing != "local":
        raise LipSyncError("CloudProviderNotImplemented")
