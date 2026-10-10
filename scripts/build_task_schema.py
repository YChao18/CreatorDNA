"""Authoritative V3.0-02 task state contract (independent of AI contracts)."""

import json
from pathlib import Path


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


nullable_time = {"type": ["string", "null"], "format": "date-time"}
text = {"type": "string"}
number = {"type": "number", "minimum": 0}
count = {"type": "integer", "minimum": 0}
error = {"anyOf": [{"type": "null"}, obj({"type": text, "message": text, "retryable": {"type": "boolean"}})]}
status = {"enum": ["pending", "running", "completed", "failed", "cancelled"]}
progress = obj({"completed": {"type": ["number", "null"], "minimum": 0},
                "total": {"type": ["number", "null"], "exclusiveMinimum": 0},
                "unit": {"type": ["string", "null"]},
                "percent": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
                "estimated": {"type": "boolean"}, "basis": text})
artifact = obj({"path": text, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "size": count})
attempt = obj({"attempt": count, "started_at": nullable_time, "finished_at": nullable_time,
               "status": status, "error": error, "directory": text})
stage_ids = ["script", "material_analysis", "tts", "lip_sync", "composition", "render", "final_check"]
stage = obj({"id": {"enum": stage_ids}, "name": text, "status": status,
             "started_at": nullable_time, "finished_at": nullable_time, "attempt": count,
             "attempts": {"type": "array", "items": attempt}, "progress": progress,
             "artifacts": {"type": "array", "items": artifact}, "result": {"type": ["object", "null"]}})
schema = obj({"schema_version": {"const": "3.0-02"},
              "task_id": {"type": "string", "pattern": "^task-[0-9a-f]{32}$"}, "kind": text,
              "creator_id": {"type": ["string", "null"]}, "materials": {"type": "array", "items": {"type": "object"}},
              "created_at": nullable_time, "started_at": nullable_time, "finished_at": nullable_time,
              "run_started_at": nullable_time, "updated_at": nullable_time, "status": status,
              "current_stage": {"enum": [None, *stage_ids]}, "current_object": {"type": ["string", "null"]},
              "elapsed_seconds": number, "stage_elapsed_seconds": number, "message": text,
              "error": error, "last_error": error, "cancel_requested": {"type": "boolean"},
              "worker_pid": {"type": ["integer", "null"]}, "runner_pid": {"type": ["integer", "null"]},
              "progress": progress, "overall_progress": progress,
              "output_files": {"type": "array", "items": text}, "log_file": text,
              "stages": {"type": "array", "items": stage, "minItems": 1}})
schema.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
               "title": "CreatorDNA local task status"})


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "src/creator_dna/schemas/task-status.json"
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
