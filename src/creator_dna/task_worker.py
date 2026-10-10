"""Isolated stage host. Parent owns task state; worker only writes attempt data."""

import importlib
import json
import sys
import traceback
from pathlib import Path
from typing import Protocol

from .errors import CreatorDNAError
from .storage import now, read_json, write_json
from .task_progress import progress


class StageFailure(CreatorDNAError):
    def __init__(self, message, error_type="StageFailure", retryable=False):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


class StageExecutor(Protocol):
    def __call__(self, context: "StageContext", params: dict) -> dict:
        """Return JSON metadata; write deliverables exclusively in output_dir."""
        ...


class StageContext:
    def __init__(self, request, attempt_dir):
        self.task_id = request["task_id"]
        self.stage_id = request["stage_id"]
        self.idempotency_key = f"{self.task_id}:{self.stage_id}"
        self.creator_id = request["creator_id"]
        self.materials = request["materials"]
        self.attempt = request["attempt"]
        self.inputs = request["inputs"]
        self.previous_attempts = request["previous_attempts"]
        self.timeout_seconds = request["timeout"]
        self.attempt_dir = Path(attempt_dir)
        self.output_dir = self.attempt_dir / "outputs"
        self.output_dir.mkdir()
        self.cancel_file = Path(request["cancel_file"])

    def check_cancelled(self):
        if self.cancel_file.exists():
            raise StageFailure("用户取消任务", "Cancelled")

    def emit(self, event, message, **data):
        self.check_cancelled()
        with (self.attempt_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "message": str(message), **data},
                                    ensure_ascii=False, allow_nan=False) + "\n")

    def report(self, message, current_object=None, completed=None, total=None, unit=None,
               basis="provider status", estimated=False):
        self.emit("progress", message, current_object=current_object,
                  progress=progress(completed, total, unit, basis, estimated))

    def log(self, message, level="info", **data):
        self.emit("log", message, level=level, data=data)


def main():
    attempt_dir = Path(sys.argv[1])
    request = read_json(attempt_dir / "request.json")
    try:
        module, name = request["handler"].split(":")
        handler = getattr(importlib.import_module(module), name)
        metadata = handler(StageContext(request, attempt_dir), request["params"])
        if not isinstance(metadata, dict):
            raise StageFailure("执行器结果须为 JSON 对象", "InvalidResult")
        # Fail here on non-JSON metadata, rather than losing a result in the parent.
        json.dumps(metadata, allow_nan=False)
        write_json(attempt_dir / "result.json", {"status": "completed", "metadata": metadata,
                                                "finished_at": now()})
        return 0
    except BaseException as exc:
        traceback.print_exc()
        write_json(attempt_dir / "result.json", {"status": "failed", "error": {
            "type": getattr(exc, "error_type", type(exc).__name__), "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False))}, "finished_at": now()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
