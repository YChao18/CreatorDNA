"""Build the standalone V3.0-01 video asset contract (no changes to V1/V2)."""

import json
from pathlib import Path


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def arr(items):
    return {"type": "array", "items": items}


def enum(*values):
    return {"enum": list(values)}


def ref(name):
    return {"$ref": f"#/$defs/{name}"}


S = {"type": "string"}
N = {"type": "number"}
P = {"type": "number", "exclusiveMinimum": 0}
Z = {"type": "number", "minimum": 0}
I = {"type": "integer", "minimum": 0}
F = {"type": "number", "minimum": 0, "maximum": 1}
B = {"type": "boolean"}
NS = {"type": ["string", "null"]}
NN = {"type": ["number", "null"]}

face = obj({"x": F, "y": F, "width": F, "height": F, "sharpness_proxy": Z})
frame = obj({"frame_id": S, "time": Z, "path": S, "brightness": Z,
             "faces": {"anyOf": [arr(face), {"type": "null"}]},
             "image_signature": {**arr({"type": "integer", "minimum": 0, "maximum": 255}), "minItems": 256, "maxItems": 256},
             "frame_change_proxy": NN, "compared_frame_id": NS})
metadata = obj({"video_stream_index": I, "width": {"type": "integer", "minimum": 1},
                "height": {"type": "integer", "minimum": 1}, "display_width": P, "display_height": P,
                "display_aspect_ratio": NS, "display_aspect_ratio_value": P,
                "sample_aspect_ratio": S, "sample_aspect_ratio_assumed": B, "reported_display_aspect_ratio": NS,
                "rotation_degrees": {"type": "number", "minimum": 0, "exclusiveMaximum": 360},
                "fps": NN, "avg_frame_rate": NS, "nominal_frame_rate": NS, "variable_frame_rate_hint": B,
                "duration": P, "video_codec": NS, "pixel_format": NS,
                "audio_streams": arr(obj({"stream_index": I, "codec": NS,
                                          "sample_rate": {"type": ["integer", "null"], "minimum": 1},
                                          "channels": {"type": ["integer", "null"], "minimum": 0}})),
                "subtitle_streams": arr(I), "container": NS})
shot = obj({"shot_id": S, "start": Z, "end": P, "duration": P, "scene_id": {"type": "null"},
            "scene_type": enum("unknown"), "status": enum("pending_review"), "basis": S})
fields = ["person_visibility", "composition", "fixed_scene", "face_clarity", "mouth_visibility", "head_motion",
          "expression", "body_action", "subtitle_position_style_occlusion", "b_roll_product_screenshot_background",
          "action_continuity", "shot_matching", "repeated_action_risk", "lip_sync_suitability"]
candidate = obj({"candidate_id": S, "shot_id": S, "start": Z, "end": P, "duration": P,
                 "scene_type": enum("unknown"), "category": {"type": "null"},
                 "suggested_categories": {**arr(enum("A", "B", "C", "D", "E")), "minItems": 1, "uniqueItems": True},
                 "status": enum("pending_review"), "confidence": {"type": "null"},
                 "person_visibility": enum("unknown"), "sampled_face_detection_ratio": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                 "face_area_ratio_mean": NN, "face_center_mean": {"anyOf": [obj({"x": F, "y": F}), {"type": "null"}]},
                 "reuse_advice": S, "quality_issues": arr(S), "evidence_frame_ids": arr(S),
                 "similar_visual_group_ids": arr(S), "basis": S,
                 "manual_checks": obj({field: obj({"status": enum("pending_review"), "value": {"type": "null"}}) for field in fields})})
base = {"relative_path": S, "path": S, "source_id": S,
        "sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"}}
source = {"oneOf": [obj({**base, "status": enum("failed"), "error": S}),
                     obj({**base, "status": enum("analyzed"), "error": {"type": "null"}, "metadata": ref("metadata"),
                          "shots": {**arr(ref("shot")), "minItems": 1}, "candidates": arr(ref("candidate")),
                          "frames": {**arr(ref("frame")), "minItems": 1}, "contacts": arr(S),
                          "sampling_limited": B, "cuts": arr(Z),
                          "face_detector": enum("opencv-haar-frontal", "unavailable_or_disabled")})]}
template = obj({"template_id": S, "width": {"type": "integer", "minimum": 2},
                "height": {"type": "integer", "minimum": 2}, "display_aspect_ratio": NS,
                "frame_rate": NS, "source_ids": {**arr(S), "minItems": 1}, "total_duration": P,
                "pixel_aspect_ratio": enum("1"), "rotation_degrees": enum(0),
                "status": enum("recommended_pending_review"), "basis": S, "preferred_by_duration": B})
group = obj({"group_id": S, "frames": {**arr(obj({"source_id": S, "frame_id": S, "time": Z})), "minItems": 2},
             "status": enum("pending_review"), "basis": S})
contract = obj({"schema_version": enum("3.0-01"), "creator_id": S, "run_id": S,
                "created_at": S, "input_folder": S, "status": enum("completed", "partial", "failed"),
                "analysis_provider": enum("local"), "sources": arr(ref("source")),
                "output_templates": arr(ref("template")), "similar_visual_groups": arr(ref("group")),
                "summary": obj({"video_count": I, "analyzed_count": I, "failed_count": I,
                                "total_duration": Z, "format_distribution": {"type": "object", "additionalProperties": I},
                                "duplicate_source_groups": arr(arr(S)), "candidate_count": I,
                                "fixed_scene_count": {"type": "null"}})})
contract.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                 "$defs": {"metadata": metadata, "shot": shot, "candidate": candidate, "frame": frame,
                           "source": source, "template": template, "group": group}})

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "src/creator_dna/schemas/video-library.json"
    path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
