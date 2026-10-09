"""Regenerate the bundled JSON Schemas from shared definitions."""
import json
from pathlib import Path


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def string(**kwargs):
    return {"type": "string", "minLength": 1, **kwargs}


def schemas():
    evidence = obj({"source_id": string(), "segment_index": {"type": "integer", "minimum": 0}, "quote": string()})
    rule = obj({"id": string(), "dimension": string(enum=["hook", "structure", "language", "rhythm", "emotion", "ending", "boundaries"]),
                "instruction": string(), "confidence": string(enum=["high", "medium", "low"]), "evidence": array(evidence, 1)})
    profile = obj({"schema_version": string(const="1.0"), "creator_id": string(pattern=r"^[a-z0-9][a-z0-9-]{0,47}$"),
                   "sample_count": {"type": "integer", "minimum": 1}, "summary": string(),
                   "rules": array(rule, 1), "limitations": array(string(), 1)})
    segment = obj({"start": {"type": ["number", "null"], "minimum": 0},
                   "end": {"type": ["number", "null"], "minimum": 0}, "text": string()})
    transcript = obj({"schema_version": string(const="1.0"), "source_id": string(),
                      "source_sha256": string(pattern=r"^[a-f0-9]{64}$"), "source_name": string(), "engine": string(),
                      "language": {"type": ["string", "null"]}, "segments": array(segment, 1)})
    return {"style-profile": profile, "transcript": transcript,
            "distillation": obj({"style_profile": profile, "skill_markdown": string()}),
            "generation": obj({"title": string(), "script": string(), "applied_rule_ids": array(string(), 1),
                               "notes": array(string())})}


def main():
    root = Path(__file__).resolve().parents[1] / "src" / "creator_dna" / "schemas"
    root.mkdir(parents=True, exist_ok=True)
    for name, data in schemas().items():
        data = {"$schema": "https://json-schema.org/draft/2020-12/schema", **data}
        (root / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
