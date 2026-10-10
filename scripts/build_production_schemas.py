"""Authoritative V3.0-05 contracts; run to refresh package/portable Skill resources."""
import json
from pathlib import Path


def obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items, **kwargs):
    return {"type": "array", "items": items, **kwargs}


S = {"type": "string", "minLength": 1}
N = {"type": "number", "minimum": 0}
P = {"type": "number", "exclusiveMinimum": 0}
I = {"type": "integer", "minimum": 1}
B = {"type": "boolean"}
NULL = {"type": "null"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
COLOR = {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def nullable(value):
    return {"anyOf": [value, NULL]}


def contracts():
    output = obj(width=I, height=I, frame_rate=S, pixel_aspect_ratio={"const": "1"},
                 orientation=enum("portrait", "landscape", "square"))
    rect = obj(x=N, y=N, width=P, height=P)
    layout = obj(fit=enum("cover", "contain"), anchor=enum("center", "left", "right"),
                 rect=rect, crop=nullable(rect), pad_color=COLOR)
    subtitle_style = obj(font=S, font_size=P, color=COLOR, highlight_color=COLOR,
                         outline_color=COLOR, outline=N, shadow=N,
                         position=enum("bottom", "top", "center"),
                         margin_x=N, margin_y=N, max_chars_per_line=I, max_lines=I)
    transition = obj(type=enum("cut", "fade"), duration=N)
    evidence = obj(source_id=S, start=N, end=P, frame_ids=array(S))
    observation = obj(status=enum("unknown", "manual"), value=nullable(S), evidence=array(evidence))
    asset = obj(asset_id=S, kind=enum("image", "video", "product", "screenshot", "background", "portrait"),
                media_type=enum("image", "video"), path=S, sha256=HASH, source=S,
                rights=enum("owned", "licensed", "authorized", "unknown", "denied"),
                rights_note=S, topics=array(S), description=S, width=I, height=I,
                duration=nullable(P), source_start=N, source_end=nullable(P), audio_offset=nullable(N), audio_sha256=nullable(HASH))
    cue = obj(cue_id=S, start=N, end=P, text=S, timing_basis=S)
    scene = obj(cue_ids=array(S, minItems=1), role=enum("portrait", "b_roll"),
                topics=array(S), keywords=array(S), preferred_asset_id=nullable(S), asset_policy=enum("auto", "selected", "missing"),
                reason=S, layout=nullable(enum("center", "left", "right")),
                background_asset_id=nullable(S), transition=transition)
    missing = obj(scene_id=S, role=enum("portrait", "b_roll", "background"),
                  topics=array(S), reason=S, blocking=B)
    clip = obj(clip_id=S, scene_id=S, start=N, end=P, role=enum("portrait", "b_roll", "background"),
               asset_id=nullable(S), source_start=N, source_end=nullable(P), layout=layout,
               transition=transition, selection_reason=S, mute={"const": True})
    subtitle = obj(cue_id=S, start=N, end=P, text=S, keywords=array(S), timing_basis=S)
    return {
        "production-assets": obj(schema_version={"const": "3.0-05"}, assets=array(asset)),
        "video-style-review": obj(reviewer=S, intervals=array(obj(source_id=S, start=N, end=P,
                    role=enum("portrait", "b_roll", "background"), frame_ids=array(S))),
                    observations=obj(**{k: observation for k in ("transitions", "subtitle_style", "subtitle_position",
                      "font", "colors", "outline_shadow", "keyword_highlight", "background_usage", "opening", "ending", "layout")})),
        "video-style-profile": obj(schema_version={"const": "3.0-05"}, creator_id=S, library_sha256=HASH,
                    template_id=S, output=output, sample_count=I,
                    shot_durations=obj(count=I, median=P, p10=P, p90=P, basis=S),
                    screen_time=obj(total=P, reviewed=N, coverage=N, portrait_ratio=nullable(N),
                                    b_roll_ratio=nullable(N), background_ratio=nullable(N), unknown_ratio=N, basis=S),
                    observations=obj(**{k: observation for k in ("transitions", "subtitle_style", "subtitle_position",
                      "font", "colors", "outline_shadow", "keyword_highlight", "background_usage", "opening", "ending", "layout")}),
                    reviewer=nullable(S), limitations=array(S)),
        "subtitle-timing": obj(schema_version={"const": "3.0-05"}, audio_sha256=HASH,
                    text=S, method=enum("forced_alignment", "asr", "asr_reviewed", "provider"),
                    provenance=S, cues=array(cue, minItems=1)),
        "production-config": obj(schema_version={"const": "3.0-05"}, output=nullable(output),
                    layout=layout, subtitle_style=subtitle_style, background_asset_id=nullable(S)),
        "video-direction": obj(schema_version={"const": "3.0-05"}, scenes=array(scene, minItems=1)),
        "video-timeline": obj(schema_version={"const": "3.0-05"}, duration=P, output=output,
                    audio=obj(path=S, sha256=HASH, text=S, start={"const": 0}, end=P, source_start={"const": 0}),
                    assets=array(asset), foreground=array(clip), background=array(clip),
                    subtitles=obj(path=S, sha256=HASH, style=subtitle_style, cues=array(subtitle, minItems=1)),
                    missing_assets=array(missing), provenance=obj(audio_manifest=nullable(S),
                      style_profile_sha256=HASH, direction_sha256=HASH), ready_for_render=B),
    }


def main():
    root = Path(__file__).resolve().parents[1]
    for name, contract in contracts().items():
        def explicit_types(node):
            # Codex structured outputs require type even for enum/const nodes.
            if isinstance(node, dict):
                if "const" in node and "type" not in node:
                    value = node["const"]
                    node["type"] = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "string"
                for v in node.values():
                    explicit_types(v)
            elif isinstance(node, list):
                for v in node:
                    explicit_types(v)
        explicit_types(contract)
        contract = {"$schema": "https://json-schema.org/draft/2020-12/schema", **contract}
        target = root / "src/creator_dna/schemas" / (name + ".json")
        target.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if name == "video-direction":
            ref = root / ".agents/skills/creator-dna-video-director/references/video-direction.json"
            ref.parent.mkdir(parents=True, exist_ok=True)
            ref.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    main()
