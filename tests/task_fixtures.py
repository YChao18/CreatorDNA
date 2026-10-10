"""Synthetic engineering executors only. No cloud or creator-quality evidence."""

import subprocess
import sys
import time

from creator_dna.storage import write_json
from creator_dna.task_worker import StageFailure


def unknown_progress(ctx, params):
    ctx.report("synthetic provider accepted job", "synthetic-job-001", basis="synthetic provider status only")
    time.sleep(params.get("delay", 0.4))
    ctx.report("synthetic provider completed one chunk; total unknown", "synthetic-job-001",
               completed=1, unit="chunks", basis="completed chunk count, total unknown")
    time.sleep(params.get("delay", 0.4))
    write_json(ctx.output_dir / "result.json", {"synthetic_only": True})
    return {"synthetic_only": True}


def fail_once(ctx, params):
    if ctx.attempt == 1:
        (ctx.output_dir / "failed.txt").write_text("synthetic failure", encoding="utf-8")
        raise StageFailure("synthetic non-retryable failure", "SyntheticFailure")
    write_json(ctx.output_dir / "verified.json", {"synthetic_only": True})
    return {"synthetic_only": True}


def hang_with_child(ctx, params):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    write_json(ctx.attempt_dir / "child.json", {"pid": child.pid})
    ctx.report("synthetic child running", str(child.pid), basis="unknown provider status")
    time.sleep(120)
    return {}


def invalid_result(ctx, params):
    return "not a JSON object"


def ffmpeg_bad_filter(ctx, params):
    from creator_dna.task_executors import run_ffmpeg
    run_ffmpeg(ctx, ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "creator_dna_missing_filter",
                    "-f", "null", "-"], 1)
    return {}
