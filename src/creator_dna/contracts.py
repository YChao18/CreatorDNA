import json
from importlib.resources import files

from jsonschema import Draft202012Validator

from .errors import CreatorDNAError


def schema(name):
    return json.loads(files("creator_dna").joinpath("schemas", f"{name}.json").read_text(encoding="utf-8"))


def validate(data, name):
    errors = sorted(Draft202012Validator(schema(name)).iter_errors(data), key=lambda e: str(e.path))
    if errors:
        e = errors[0]
        location = ".".join(map(str, e.path)) or "$"
        raise CreatorDNAError(f"{name} 格式错误 ({location}): {e.message}")
