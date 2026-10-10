"""Opt-in REAL cloud/model suite. Never runs engineering fixtures as real proof."""
import importlib.util
import os
import unittest
from pathlib import Path

from creator_dna.storage import Workspace


@unittest.skipUnless(os.getenv("CREATORDNA_VIDEO_REAL_TEST")=="1","REAL video levels disabled: credentials, registered voice, reviewed portraits and model weights required")
class RealVideoIntegration(unittest.TestCase):
    def test_three_real_levels_sequentially(self):
        spec=importlib.util.spec_from_file_location("validate_v3_render",Path(__file__).resolve().parents[1]/"scripts/validate_v3_render.py")
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report=module.real_suite(Workspace(os.environ["CREATORDNA_VIDEO_WORKSPACE"]),os.environ["CREATORDNA_VIDEO_SUITE"])
        self.assertTrue(report["passed"],report)
        self.assertEqual(report["human_review"],"pending")
