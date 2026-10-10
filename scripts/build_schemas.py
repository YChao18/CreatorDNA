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
    result = {"style-profile": profile, "transcript": transcript,
            "distillation": obj({"style_profile": profile, "skill_markdown": string()}),
            "generation": obj({"title": string(), "script": string(), "applied_rule_ids": array(string(), 1),
                               "notes": array(string())})}
    status = string(enum=["passed", "failed", "unable"])
    score = {"type": ["number", "null"], "minimum": 0, "maximum": 5}
    quote = obj({"kind": string(enum=["script", "rule", "reference"]),
                 "rule_id": {"type": ["string", "null"]}, "quote": string()})
    dimension = obj({"id": string(), "group": string(enum=["style", "quality"]),
                     "score": score, "reason": string(), "evidence": array(quote)})
    issue = obj({"dimension_id": string(), "description": string(), "suggestion": string(),
                 "evidence": array(quote, 1)})
    evaluation = obj({"schema_version": string(const="2.0"), "dimensions": array(dimension, 1),
                      "originality": obj({"risk": string(enum=["low", "medium", "high", "unknown"]),
                                          "reason": string(), "evidence": array(quote)}),
                      "issues": array(issue), "limitations": array(string(), 1)})
    config_dimension = obj({"id": string(), "group": string(enum=["style", "quality"]),
                            "description": string(), "minimum": {"type": "number", "minimum": 0, "maximum": 5}})
    config = obj({"schema_version": string(const="2.0"), "dimensions": array(config_dimension, 1),
                  "max_originality_risk": string(enum=["low", "medium", "high"])})
    feedback = obj({"schema_version": string(const="2.0"), "author": string(), "comment": string(),
                    "status_override": {"type": ["string", "null"], "enum": ["passed", "failed", "unable", None]},
                    "dimension_overrides": array(obj({"id": string(), "score": score, "reason": string()}))})
    feedback_record = obj({**feedback["properties"], "feedback_id": string(), "target_run_id": string(),
                           "skill_version": string(), "created_at": string()})
    case = obj({"id": string(), "topic": string(), "category": string(enum=["regular", "cross_topic", "distant", "complex"]),
                "brief": {"type": "string"}, "human_reference": {"type": ["string", "null"]}})
    dataset = obj({"schema_version": string(const="2.0"), "id": string(),
                   "purpose": string(enum=["development", "acceptance"]), "cases": array(case, 1)})
    report = obj({"schema_version": string(const="2.0"), "run_id": string(), "creator_id": string(),
                  "skill_version": string(), "created_at": string(), "script_sha256": string(),
                  "skill_files": {"type": "object", "additionalProperties": {"type": "string"}},
                  "config": config, "status": status, "reasons": array(string()),
                  "assessment": {"anyOf": [evaluation, {"type": "null"}]},
                  "disclaimer": string()})
    result.update({"evaluation": evaluation, "evaluation-config": config, "evaluation-report": report,
                   "feedback": feedback, "feedback-record": feedback_record, "dataset": dataset,
                   "skill-candidate": obj({"skill_markdown": string(), "change_notes": string(),
                                           "diagnosis": string(), "target_rule_ids": array(string(), 1)})})
    attempt = obj({"index": {"type": "integer", "minimum": 0}, "script": string(), "report": string(), "status": status})
    result["revision-history"] = obj({"schema_version": string(const="2.0"), "run_id": string(),
        "creator_id": string(), "skill_version": string(), "max_revisions": {"type": "integer", "minimum": 0},
        "status": string(enum=["passed", "failed", "unable", "interrupted"]), "stop_reason": string(),
        "attempts": array(attempt), "human_advice": array(string())})
    entry = obj({"case_id": string(), "repeat": {"type": "integer", "minimum": 1},
                 "category": string(), "status": status, "run_id": {"type": ["string", "null"]},
                 "error": {"type": ["string", "null"]}, "scores": array(dimension),
                 "originality_risk": string(enum=["low", "medium", "high", "unknown"])})
    result["batch-report"] = obj({"schema_version": string(const="2.0"), "run_id": string(),
        "creator_id": string(), "skill_version": string(), "created_at": string(), "dataset": dataset,
        "dataset_sha256": string(), "config": config, "repeats": {"type": "integer", "minimum": 1},
        "skill_files": {"type": "object", "additionalProperties": {"type": "string"}},
        "generation_config": obj({"max_revisions": {"type": "integer", "minimum": 0},
                                  "agent": {"type": "object"}}),
        "entries": array(entry), "status": string(enum=["completed", "interrupted"]),
        "summary": obj({"total": {"type": "integer", "minimum": 0}, "passed": {"type": "integer", "minimum": 0},
                        "failed": {"type": "integer", "minimum": 0}, "unable": {"type": "integer", "minimum": 0},
                        "means": {"type": "object", "additionalProperties": score}})})
    result["skill-metadata"] = {
        "type": "object", "required": ["schema_version", "creator_id", "version", "status", "created_at", "run_id", "files"],
        "properties": {"schema_version": string(enum=["1.0", "2.0"]), "creator_id": string(),
                       "version": string(pattern=r"^v[0-9]{3,}$"), "status": string(enum=["draft", "approved"]),
                       "created_at": string(), "run_id": string(),
                       "files": {"type": "object", "additionalProperties": string(pattern=r"^[a-f0-9]{64}$")},
                       "parent_version": {"type": ["string", "null"]}, "quality_gate_required": {"type": "boolean"},
                       "training_topics": array(string()), "test_run_ids": array(string()),
                       "change_notes": string(), "modified_at": string(), "skill_id": string()},
        "additionalProperties": True,  # V1 metadata and future provenance fields remain readable.
    }
    return result


def main():
    root = Path(__file__).resolve().parents[1] / "src" / "creator_dna" / "schemas"
    root.mkdir(parents=True, exist_ok=True)
    for name, data in schemas().items():
        data = {"$schema": "https://json-schema.org/draft/2020-12/schema", **data}
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        (root / f"{name}.json").write_text(text, encoding="utf-8")
        skill = {"evaluation": "creator-dna-evaluator", "evaluation-config": "creator-dna-evaluator",
                 "generation": "creator-dna-optimizer", "skill-candidate": "creator-dna-optimizer"}.get(name)
        if skill:
            reference = root.parents[2] / ".agents" / "skills" / skill / "references" / f"{name}.schema.json"
            reference.parent.mkdir(parents=True, exist_ok=True)
            reference.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
