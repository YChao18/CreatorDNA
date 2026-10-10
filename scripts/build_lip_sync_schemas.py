"""Build deterministic V3.0-04 sidecar contracts."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/creator_dna/schemas"
STRING = {"type": "string", "minLength": 1}
BOOL = {"type": "boolean"}
NUMBER = {"type": "number", "minimum": 0}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def obj(properties, required=None):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties) if required is None else required, "properties": properties}


def save(name, value):
    value = {"$schema": "https://json-schema.org/draft/2020-12/schema", **value}
    (ROOT / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


save("lip-sync-config", obj({
    "provider": {"const": "musetalk"}, "model": {"const": "v15"}, "repository": STRING,
    "python": STRING, "ffmpeg": STRING, "ffprobe": STRING,
    "gpu_id": {"type": "integer", "minimum": 0, "maximum": 15},
    "batch_size": {"type": "integer", "minimum": 1, "maximum": 8}, "float16": BOOL,
    "extra_margin": {"type": "integer", "minimum": 0, "maximum": 40},
    "parsing_mode": {"enum": ["jaw", "raw"]}, "fps": {"const": 25},
    "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647}}))

review = obj({"candidate_id": STRING, "source_sha256": HASH, "approved": BOOL,
    "reviewer": {"type": "string"}, "evidence": {"type": "string"},
    **{k: BOOL for k in ("front_facing", "face_clear", "mouth_unobstructed", "moderate_head_motion", "natural_action")},
    "quality_score": {"type": "number", "minimum": 0, "maximum": 100},
    **{k: STRING for k in ("scene_id", "action_key", "start_pose", "end_pose")},
    "safe_start": NUMBER, "safe_end": NUMBER})
save("portrait-review", obj({"schema_version": {"const": "3.0-04"}, "creator_id": STRING,
    "library_run_id": STRING, "candidates": {"type": "array", "items": review}}))
save("portrait-consent", obj({"schema_version": {"const": "3.0-04"}, "creator_id": STRING,
    "authorized": BOOL, "processing": {"enum": ["local", "cloud"]},
    "confirmation": STRING, "confirmed_at": STRING,
    **{k: {"type": "array", "minItems": 1, "uniqueItems": True, "items": HASH}
       for k in ("source_sha256", "audio_sha256")}}))
usage = obj({"timeline": STRING, "candidate_id": STRING, "source_sha256": HASH,
    **{k: NUMBER for k in ("source_start", "source_end", "position", "duration")},
    **{k: STRING for k in ("action_key", "scene_id", "start_pose", "end_pose", "transition", "task_id")},
    "usage_count": {"type": "integer", "minimum": 1},
    "status": {"enum": ["reserved", "generated_pending_review", "failed_reserved_for_retry"]},
    "artifact": {"type": ["string", "null"]}}, required=["timeline", "candidate_id", "source_sha256",
        "source_start", "source_end", "position", "duration", "action_key", "scene_id", "start_pose", "end_pose",
        "transition", "task_id", "usage_count", "status"])
save("portrait-usage", obj({"schema_version": {"const": "3.0-04"}, "events": {"type": "array", "items": usage}}))
save("lip-sync-manifest", obj({"schema_version": {"const": "3.0-04"}, "task_id": STRING,
    "creator_id": STRING, "provider": STRING, "model": STRING, "created_at": STRING,
    "video": STRING, "sha256": HASH, "duration": {"type": "number", "minimum": 5, "maximum": 10},
    "reference_type": {"enum": ["video", "image"]}, "source_sha256": HASH, "audio_sha256": HASH,
    "consent": {"type": "object"}, "selection": {"type": ["object", "null"]},
    "settings": {"type": "object"}, "backend": {"type": "object"},
    "verification": {"type": "object"}, "human_review": {"const": "pending"},
    "review_checks": {"type": "array", "items": STRING, "minItems": 7}}))
