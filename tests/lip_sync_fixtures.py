"""Explicit synthetic infrastructure fixture. NEVER a real lip-sync backend."""

from creator_dna.lip_sync import LipSyncResult
from creator_dna.lip_sync_workflow import generate_stage
from creator_dna.task_executors import run_ffmpeg


class SyntheticOnlyProvider:
    name = "synthetic-engineering-fixture"

    def environment(self):
        return {"ready": True, "synthetic_only": True}

    def fingerprint(self):
        return {"fixture": "not-a-model"}

    def generate(self, request, ctx):
        output = ctx.output_dir / "synthetic-not-lipsynced.mp4"
        run_ffmpeg(ctx, ["ffmpeg", "-v", "error", "-n", "-i", str(request.reference),
            "-t", str(request.duration), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)], request.duration)
        return LipSyncResult(output, {"synthetic_only": True, "source_looped": False, "dropped_frames": 0})


def synthetic_pipeline(ctx, params):
    return generate_stage(ctx, params, provider=SyntheticOnlyProvider())
