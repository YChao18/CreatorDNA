"""Strict orchestration configuration; existing provider schemas stay authoritative."""
import json
from pathlib import Path


def obj(properties, required=()):
    return {"type":"object", "properties":properties, "required":list(required), "additionalProperties":False}


text = {"type":"string", "minLength":1}
boolean = {"type":"boolean"}
hashes = {"type":"array", "minItems":1, "uniqueItems":True, "items":{"type":"string", "pattern":"^[0-9a-f]{64}$"}}
properties = {k:text for k in ("profile","assets","speech","timing","production_config","direction","tts","lipsync","voice_asset","skill_version","brief")}
properties.update({
    "schema_version":{"const":"3.0-06"},
    "quality":boolean, "max_revisions":{"type":"integer","minimum":0,"maximum":10},
    "director":{"enum":["agent","local"]},
    "agent":obj({"binary":text,"model":text,"timeout":{"type":"integer","minimum":1}}),
    "render":obj({"ffmpeg":text,"ffprobe":text,"segment_seconds":{"type":"number","minimum":.1,"maximum":120},
                  "crf":{"type":"integer","minimum":0,"maximum":51},"preset":text,"threads":{"type":"integer","minimum":1,"maximum":64},
                  "audio_mix":{"type":"array","items":obj({"path":text,"sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                     "start":{"type":"number","minimum":0},"end":{"type":"number","minimum":0},"source_start":{"type":"number","minimum":0},
                     "volume":{"type":"number","minimum":0,"maximum":2},"rights":{"enum":["owned","licensed","authorized"]},"rights_note":text},
                     ("path","sha256","start","end","source_start","volume","rights","rights_note"))}}),
    "alignment_command":{"type":"array","minItems":1,"items":text},
    "portrait_processing_command":{"type":"array","minItems":1,"items":text},
    "portrait":obj({k:text for k in ("image","library","review","candidate","source_id")}|{
        "reference_max_dimension":{"type":"integer","minimum":512,"maximum":8192},
        "cooldown":{"type":"number","minimum":0},"max_uses":{"type":"integer","minimum":1,"maximum":10}}),
    "portrait_authorization":obj({"creator_id":text,"authorized":boolean,"processing":{"const":"local"},
                                   "scope":{"const":"selected_portraits_with_workflow_speech"},
                                   "source_sha256":hashes,"confirmation":text,"confirmed_at":text},
                                  ("creator_id","authorized","processing","scope","source_sha256","confirmation","confirmed_at")),
})
schema = obj(properties,("schema_version","profile","assets"))
schema["$schema"]="https://json-schema.org/draft/2020-12/schema"
schema["allOf"]=[{"if":{"required":["lipsync"]},"then":{"required":["portrait","portrait_authorization"]}},
                 {"if":{"properties":{"portrait":{"required":["image"]}},"required":["portrait"]},
                  "then":{"properties":{"portrait":{"not":{"anyOf":[{"required":["library"]},{"required":["review"]},{"required":["candidate"]},{"required":["source_id"]}]}}}}},
                 {"if":{"required":["portrait"],"properties":{"portrait":{"not":{"required":["image"]}}}},
                  "then":{"properties":{"portrait":{"required":["library"]}}}}]

if __name__=="__main__":
    path=Path(__file__).resolve().parents[1]/"src/creator_dna/schemas/video-workflow-config.json"
    path.write_text(json.dumps(schema,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
