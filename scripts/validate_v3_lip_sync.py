"""Read-only media preflight; optional explicit real LOCAL inference, no fixtures."""

import argparse
import json
import subprocess
import uuid
from pathlib import Path

from creator_dna.lip_sync import config, make_provider
from creator_dna.lip_sync_workflow import create_task
from creator_dna.portrait import load_library, rank_candidates, review_template
from creator_dna.storage import Workspace, now, read_json, write_json
from creator_dna.task_cli import TerminalProgress
from creator_dna.tasks import TaskManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--creator", required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--consent", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--tts-manifest", type=Path)
    parser.add_argument("--duration", type=float, default=8)
    parser.add_argument("--timeline", default="validation")
    parser.add_argument("--run", action="store_true", help="执行真实本地推理；必须有新音频、已审核候选和授权")
    args = parser.parse_args()
    workspace = Workspace(args.workspace)
    folder = workspace.root / "lip-sync-validation" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    library = load_library(args.library, args.creator)
    ranked = rank_candidates(library, read_json(args.review) if args.review else None)
    settings = config(args.config)
    environment = make_provider(settings).environment()
    write_json(folder / "environment.json", environment)
    write_json(folder / "ranked.json", ranked)
    write_json(folder / "review-template.json", review_template(library))
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        gpu = None
    report = {"schema_version": "3.0-04", "created_at": now(), "library": str(args.library.resolve()),
        "real_model_inference": "not_run", "synthetic_model_evidence": False, "gpu_inventory": gpu,
        "sources": library["summary"]["analyzed_count"], "candidates": len(ranked),
        "approved_candidates": sum(c["eligible"] for c in ranked), "backend_ready": environment["ready"],
        "audio_supplied": bool(args.audio or args.tts_manifest), "human_quality_review": "not_run"}
    write_json(folder / "validation.json", report)
    if args.run:
        if not args.review or not args.consent or not (args.audio or args.tts_manifest):
            parser.error("--run requires reviewed material, local consent and new speech audio")
        task = create_task(workspace, args.creator, settings, audio=args.audio, tts_manifest=args.tts_manifest,
            library=args.library, review=args.review, consent=args.consent, duration=args.duration, timeline=args.timeline)
        result = TaskManager(workspace).run(task["task_id"], observer=TerminalProgress())
        report.update(real_model_inference=result["status"], task_id=task["task_id"],
                      output_files=result["output_files"], error=result["error"], elapsed_seconds=result["elapsed_seconds"])
        write_json(folder / "validation.json", report)
        if result["status"] != "completed":
            print(json.dumps({"report": str(folder), **report}, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps({"report": str(folder), **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
