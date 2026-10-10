"""Opt-in BILLABLE cloud verification. Default discovery performs no network calls.

Uses an already authorized asset; never uploads reference audio implicitly.
Run once per provider with explicit CREATORDNA_TTS_REAL_TEST=1.
"""

import os
import unittest

from creator_dna.storage import Workspace, read_json
from creator_dna.tasks import TaskManager
from creator_dna.tts import config
from creator_dna.tts_audio import metadata
from creator_dna.tts_workflow import create_synthesis_task, create_voice_task


@unittest.skipUnless(os.getenv("CREATORDNA_TTS_REAL_TEST") == "1", "REAL API disabled: explicit paid integration opt-in required")
class RealTTSIntegrationTests(unittest.TestCase):
    def test_real_authorized_creator_voice_outputs_decodable_audio(self):
        cfg = config(os.environ["CREATORDNA_TTS_CONFIG"])
        if not os.getenv(cfg["api_key_env"]):
            self.skipTest("REAL API: key missing; provider remains unverified")
        cfg["max_attempts"] = 1
        workspace = Workspace(os.environ["CREATORDNA_TTS_WORKSPACE"])
        creator = os.environ["CREATORDNA_TTS_CREATOR"]
        if os.getenv("CREATORDNA_TTS_REAL_CLONE") == "1":
            # A second explicit opt-in plus a provider-scoped consented reference.
            registered = create_voice_task(workspace, creator, os.environ["CREATORDNA_TTS_REFERENCE"],
                                           cfg, os.environ["CREATORDNA_TTS_VOICE_NAME"])
            registration = TaskManager(workspace).run(registered["task_id"])
            self.assertEqual(registration["status"], "completed", registration.get("error"))
            voice = registration["stages"][0]["result"]["voice_asset"]
        else:
            voice = os.environ["CREATORDNA_TTS_VOICE"]
        state = create_synthesis_task(workspace, creator, voice, cfg, "这是一段全新的口播文案，用于验证已授权声音的云端语音合成。")
        result = TaskManager(workspace).run(state["task_id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        from pathlib import Path
        manifest_file = next(Path(p) for p in result["output_files"] if Path(p).name == "speech.json")
        manifest = read_json(manifest_file)
        self.assertGreater(metadata(manifest_file.parent / manifest["audio"], cfg)["duration_seconds"], 0)
        self.assertEqual(manifest["provider"], cfg["provider"])
