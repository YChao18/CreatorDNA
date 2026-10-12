"""Opt-in LOCAL real-model verification. Never synthesizes fixture videos."""

import os
import unittest
from pathlib import Path

from creator_dna.lip_sync_workflow import create_task
from creator_dna.storage import Workspace, read_json
from creator_dna.tasks import TaskManager


@unittest.skipUnless(os.getenv("CREATORDNA_LIPSYNC_REAL_TEST") == "1",
                     "REAL model disabled: new speech audio + reviewed authorized portrait + weights required")
class RealLipSyncIntegrationTests(unittest.TestCase):
    def test_real_local_portrait_inference_pending_human_quality_review(self):
        workspace = Workspace(os.environ["CREATORDNA_LIPSYNC_WORKSPACE"])
        task = create_task(workspace, os.environ["CREATORDNA_LIPSYNC_CREATOR"], os.environ["CREATORDNA_LIPSYNC_CONFIG"],
            audio=os.environ["CREATORDNA_LIPSYNC_AUDIO"], library=os.environ["CREATORDNA_LIPSYNC_LIBRARY"],
            review=os.getenv("CREATORDNA_LIPSYNC_REVIEW"), consent=os.environ["CREATORDNA_LIPSYNC_CONSENT"],
            candidate=os.getenv("CREATORDNA_LIPSYNC_CANDIDATE"), timeline=os.environ["CREATORDNA_LIPSYNC_TIMELINE"],
            duration=float(os.getenv("CREATORDNA_LIPSYNC_DURATION", "8")))
        result = TaskManager(workspace).run(task["task_id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        path = next(Path(p) for p in result["output_files"] if Path(p).name == "portrait.json")
        manifest = read_json(path)
        self.assertEqual(manifest["provider"], "musetalk")
        self.assertNotIn("synthetic_only", manifest["backend"])
        self.assertGreater(manifest["backend"]["peak_gpu_allocated_bytes"], 0)
        self.assertEqual(manifest["human_review"], "pending")
