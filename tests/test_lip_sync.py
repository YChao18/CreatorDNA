"""Deterministic and synthetic media engineering tests, not model quality evidence."""

import copy
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from creator_dna.cli import parser
from creator_dna.contracts import schema, validate
from creator_dna.errors import CreatorDNAError
from creator_dna.lip_sync import config, require_consent
from creator_dna.lip_sync_provider import MuseTalkProvider
from creator_dna.lip_sync_workflow import create_task, audio_input, gpu_lease
from creator_dna.portrait import UsageLedger, plan, rank_candidates
from creator_dna.storage import Workspace, digest_file, now, read_json, write_json
from creator_dna.tasks import TaskManager
from creator_dna.video import LocalVideoAnalyzer
from creator_dna.video_assets import VideoAssetWorkflow


def review_for(library):
    return {"schema_version": "3.0-04", "creator_id": library["creator_id"], "library_run_id": library["run_id"],
        "candidates": [{"candidate_id": c["candidate_id"], "source_sha256": s["sha256"],
            "approved": True, "reviewer": "SYNTHETIC TEST ONLY", "evidence": "synthetic fixture, no creator identity",
            "front_facing": True, "face_clear": True, "mouth_unobstructed": True,
            "moderate_head_motion": True, "natural_action": True, "quality_score": 90,
            "scene_id": "synthetic-scene", "action_key": c["candidate_id"], "start_pose": "neutral", "end_pose": "neutral",
            "safe_start": c["start"], "safe_end": c["end"]}
            for s in library["sources"] if s["status"] == "analyzed" for c in s["candidates"]]}


def minimal_library():
    return {"creator_id": "synthetic", "run_id": "synthetic-run", "sources": [
        {"status": "analyzed", "source_id": "s-1", "sha256": "a"*64, "path": "synthetic.mp4",
         "metadata": {"display_width": 720, "display_height": 1280},
         "frames": [{"frame_id": "f-1", "faces": [{"sharpness_proxy": 120}]}],
         "candidates": [{"candidate_id": "c-1", "start": 0, "end": 12, "duration": 12,
                         "suggested_categories": ["A"], "evidence_frame_ids": ["f-1"]},
                        {"candidate_id": "c-2", "start": 12, "end": 24, "duration": 12,
                         "suggested_categories": ["A"], "evidence_frame_ids": ["f-1"]}]}]}


class PortraitPlanTests(unittest.TestCase):
    def setUp(self):
        self.lib = minimal_library()
        self.review = review_for(self.lib)
        self.ranked = rank_candidates(self.lib, self.review)

    def test_schemas_config_and_cli_reject_cloud_unknown_and_bad_parameters(self):
        for name in ("lip-sync-config", "portrait-review", "portrait-consent", "portrait-usage", "lip-sync-manifest"):
            Draft202012Validator.check_schema(schema(name))
        for change in ({"provider": "cloud"}, {"batch_size": True}, {"batch_size": 0},
                       {"bbox_shift": 7}, {"float16": "true"}, {"fps": 30}):
            with self.subTest(change=change), self.assertRaises(CreatorDNAError):
                config({"repository": "models", **change})
        args = parser().parse_args(["lipsync", "plan", "--creator", "synthetic", "--library", "lib.json",
                                    "--review", "review.json", "--durations", "5", "8"])
        self.assertEqual(args.durations, [5, 8])

    def test_sampling_cannot_approve_mouth_pose_motion_or_identity(self):
        ranked = rank_candidates(self.lib)
        self.assertTrue(all(not c["eligible"] and c["quality_score"] is None for c in ranked))
        with self.assertRaisesRegex(CreatorDNAError, "Insufficient"):
            plan(ranked, [5])
        for key in ("mouth_unobstructed", "front_facing", "natural_action", "moderate_head_motion"):
            review = copy.deepcopy(self.review)
            review["candidates"][0][key] = False
            self.assertFalse(rank_candidates(self.lib, review)[1]["eligible"])

    def test_review_cannot_escape_candidate_range_hash_or_run(self):
        for field, value in (("safe_end", 99), ("source_sha256", "b"*64), ("quality_score", float("nan"))):
            review = copy.deepcopy(self.review)
            review["candidates"][0][field] = value
            with self.assertRaises(CreatorDNAError):
                rank_candidates(self.lib, review)
        review = copy.deepcopy(self.review)
        review["library_run_id"] = "wrong"
        with self.assertRaises(CreatorDNAError):
            rank_candidates(self.lib, review)

    def test_multiple_candidates_deterministic_no_loop_and_shortage(self):
        p = plan(self.ranked, [6, 6])
        self.assertEqual([c["candidate"]["candidate_id"] for c in p["clips"]], ["c-1", "c-2"])
        self.assertFalse(p["looping"])
        with self.assertRaisesRegex(CreatorDNAError, "Insufficient"):
            plan(self.ranked, [6, 6, 6])
        for duration in (4.9, 10.1, float("nan")):
            with self.assertRaises(CreatorDNAError):
                plan(self.ranked, [duration])

    def test_overlap_duplicate_source_and_repeated_action_are_not_new_material(self):
        self.ranked[1].update(start=2, end=14)
        with self.assertRaisesRegex(CreatorDNAError, "Insufficient"):
            plan(self.ranked, [6, 6])
        ranked = rank_candidates(self.lib, self.review)
        ranked[1]["review"]["action_key"] = ranked[0]["review"]["action_key"]
        with self.assertRaisesRegex(CreatorDNAError, "Insufficient"):
            plan(ranked, [6, 6])

    def test_cooldown_counts_interval_and_allows_later_explicit_reuse(self):
        event = plan(self.ranked[:1], [6])["clips"][0]["usage"]
        with self.assertRaises(CreatorDNAError):
            plan(self.ranked[:1], [6], [event], position=20, max_uses=2)
        self.assertEqual(plan(self.ranked[:1], [6], [event], position=40, max_uses=2)["clips"][0]["usage"]["usage_count"], 2)
        # Separate full videos use separate timeline ledgers, no inferred cooldown across projects.
        self.assertEqual(plan(self.ranked[:1], [6], [event], timeline="new-video")["clips"][0]["usage"]["usage_count"], 1)

    def test_visual_mismatch_requires_broll_hook(self):
        self.ranked[1]["review"]["scene_id"] = "other-scene"
        p = plan(self.ranked, [6, 6])
        self.assertEqual(p["b_roll_hooks"][0]["position"], 6)

    def test_usage_reservation_is_idempotent_and_prevents_queued_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = UsageLedger(Workspace(temp), "synthetic")
            clip = plan(self.ranked, [6])["clips"][0]
            options = {"timeline": "default", "position": 0, "cooldown": 30, "max_uses": 1}
            ledger.reserve("task-a", clip, self.ranked, options)
            ledger.finish("task-a", "failed_reserved_for_retry")
            ledger.reserve("task-a", clip, self.ranked, options)
            self.assertEqual(len(ledger.read()), 1)
            with self.assertRaises(CreatorDNAError):
                ledger.reserve("task-b", clip, self.ranked, options)

    def test_os_gpu_lock_releases_after_context(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(temp)
            with gpu_lease(workspace, 0):
                with self.assertRaisesRegex(CreatorDNAError, "GPUInUse"):
                    with gpu_lease(workspace, 0):
                        pass
            with gpu_lease(workspace, 0):
                pass


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe") and importlib.util.find_spec("PIL"),
                     "synthetic media engineering tests require local FFmpeg and Pillow")
class LipSyncMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source_dir = self.root / "source"
        source_dir.mkdir()
        self.video = source_dir / "原始素材 with spaces.mp4"
        self.audio = source_dir / "新音频.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=12",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.video)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                        "-c:a", "pcm_s16le", str(self.audio)], check=True)
        self.workspace = Workspace(self.root / "workspace")
        result = VideoAssetWorkflow(self.workspace, LocalVideoAnalyzer(face_detection=False,
                    segment_seconds=12, max_frames=2)).analyze(source_dir, "synthetic")
        self.library = Path(result["library"])
        self.review = self.workspace.root / "review.json"
        write_json(self.review, review_for(read_json(self.library)))
        self.settings = {"repository": str(self.root / "missing-model"), "python": sys.executable}
        self.consent = {"schema_version": "3.0-04", "creator_id": "synthetic", "authorized": True,
            "processing": "local", "confirmation": "synthetic fixture only", "confirmed_at": now(),
            "source_sha256": [digest_file(self.video)], "audio_sha256": [digest_file(self.audio)]}

    def create(self, **kwargs):
        return create_task(self.workspace, "synthetic", self.settings, audio=self.audio,
            library=self.library, review=self.review, consent=self.consent, duration=5, **kwargs)

    def test_unready_backend_fails_with_diagnostic_artifact_no_video_and_safe_resume(self):
        task = self.create()
        manager = TaskManager(self.workspace, heartbeat=.05)
        result = manager.run(task["task_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "LocalBackendNotReadySeeEnvironmentJSON")
        outputs = list(manager.folder(task["task_id"]).rglob("environment.json"))
        self.assertEqual(len(outputs), 1)
        self.assertFalse(read_json(outputs[0])["ready"])
        self.assertFalse(list(manager.folder(task["task_id"]).rglob("portrait.mp4")))
        self.assertEqual(manager.run(task["task_id"], resume=True)["status"], "failed")

    def test_input_mutation_after_queue_fails_before_model_execution(self):
        task = self.create()
        self.audio.write_bytes(b"changed")
        result = TaskManager(self.workspace).run(task["task_id"])
        self.assertEqual(result["error"]["type"], "InputChanged")

    def test_task_workspace_cannot_write_into_original_material_folder(self):
        with self.assertRaisesRegex(CreatorDNAError, "workspace"):
            create_task(Workspace(self.video.parent / "generated"), "synthetic", self.settings,
                audio=self.audio, library=self.library, review=self.review, consent=self.consent, duration=5)
        self.assertFalse((self.video.parent / "generated").exists())

    def test_consent_cannot_be_reused_for_different_person_audio_or_cloud(self):
        for field, value in (("creator_id", "other"), ("processing", "cloud"),
                             ("authorized", False), ("audio_sha256", ["b"*64])):
            consent = {**self.consent, field: value}
            with self.assertRaises(CreatorDNAError):
                require_consent(consent, "synthetic", digest_file(self.video), digest_file(self.audio))
        with self.assertRaisesRegex(CreatorDNAError, "AudioTooShort"):
            self.create(audio_start=2)

    def test_tts_manifest_bridge_checks_hash_and_rejects_path_escape(self):
        manifest = {"schema_version": "3.0-03", "provider": "qwen", "model": "synthetic",
            "voice_asset": "synthetic-only", "created_at": now(), "text": "synthetic text only",
            "audio": self.audio.name, "sha256": digest_file(self.audio), "canonical_audio": self.audio.name,
            "canonical_duration_seconds": 6, "timing_note": "synthetic engineering fixture",
            "metadata": {"duration_seconds": 6, "sample_rate": 44100, "channels": 1,
                         "codec": "pcm_s16le", "format": "wav", "bytes": self.audio.stat().st_size},
            "segments": [{"index": 0, "text": "synthetic", "text_start": 0, "text_end": 9,
                "audio": self.audio.name, "start_seconds": 0, "end_seconds": 6, "duration_seconds": 6,
                "timing_basis": "measured_audio_segment_boundaries", "word_timestamps": None, "provider_timestamps": None}]}
        path = self.audio.parent / "speech.json"
        write_json(path, manifest)
        self.assertEqual(audio_input(tts_manifest=path), self.audio.resolve())
        for changed in ({"sha256": "b"*64}, {"audio": "../escape.wav"}):
            write_json(path, {**manifest, **changed})
            with self.assertRaises(CreatorDNAError):
                audio_input(tts_manifest=path)

    def test_synthetic_pipeline_real_media_validation_and_ledger_not_model_evidence(self):
        env = {"PYTHONPATH": str(Path(__file__).parent)+os.pathsep+os.environ.get("PYTHONPATH", "")}
        with patch.dict(os.environ, env):
            manager = TaskManager(self.workspace, executors={"local_lip_sync": "lip_sync_fixtures:synthetic_pipeline"})
            # Create with the fixture handler explicitly; default CLI cannot select it.
            with patch("creator_dna.lip_sync_workflow.TaskManager", return_value=manager):
                task = self.create()
            result = manager.run(task["task_id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        path = next(Path(p) for p in result["output_files"] if Path(p).name == "portrait.json")
        manifest = read_json(path)
        self.assertTrue(manifest["backend"]["synthetic_only"])
        self.assertEqual(manifest["human_review"], "pending")
        self.assertTrue(manifest["verification"]["decode_ok"])
        self.assertEqual(manifest["verification"]["video_frames"], 125)
        self.assertEqual(UsageLedger(self.workspace, "synthetic").read()[0]["status"], "generated_pending_review")
        self.assertEqual(digest_file(self.video), self.consent["source_sha256"][0])
        self.assertEqual(manager.run(task["task_id"])["status"], "completed")
