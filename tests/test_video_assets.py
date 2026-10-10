"""Synthetic engineering tests; no evidence of real creator style or lip-sync quality."""

import copy
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from creator_dna.cli import main, parser
from creator_dna.contracts import schema
from creator_dna.errors import CreatorDNAError
from creator_dna.storage import Workspace, digest_file, read_json
from creator_dna.video import LocalVideoAnalyzer, metadata_from_probe, scan_videos
from creator_dna.video_assets import VideoAssetWorkflow, classify, validate_library


def synthetic_probe():
    return {"streams": [{"index": 2, "codec_type": "video", "width": 720, "height": 576,
                         "sample_aspect_ratio": "16:15", "display_aspect_ratio": "4:3",
                         "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001",
                         "codec_name": "h264", "duration": "2.002", "side_data_list": [{"rotation": -90}]},
                        {"index": 3, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2}],
            "format": {"duration": "2.002", "format_name": "mov,mp4"}}


class VideoContractTests(unittest.TestCase):
    def test_rotation_sar_and_exact_rational_fps(self):
        m = metadata_from_probe(synthetic_probe())
        self.assertEqual(m["width"], 720)
        self.assertEqual(m["sample_aspect_ratio"], "16/15")
        self.assertEqual(m["display_aspect_ratio"], "3/4")
        self.assertEqual((m["display_width"], m["display_height"]), (576, 768))
        self.assertAlmostEqual(m["fps"], 30000/1001)
        self.assertEqual(m["rotation_degrees"], 270)
        self.assertEqual(m["audio_streams"][0]["sample_rate"], 48000)
        self.assertFalse(m["variable_frame_rate_hint"])

    def test_missing_audio_unknown_sar_nominal_fallback_and_vfr_hint(self):
        raw = synthetic_probe()
        raw["streams"] = raw["streams"][:1]
        raw["streams"][0].update(sample_aspect_ratio="N/A", avg_frame_rate="0/0")
        m = metadata_from_probe(raw)
        self.assertEqual(m["audio_streams"], [])
        self.assertTrue(m["sample_aspect_ratio_assumed"])
        self.assertEqual(m["avg_frame_rate"], None)
        self.assertEqual(m["nominal_frame_rate"], "30000/1001")
        raw["streams"][0]["avg_frame_rate"] = "25/1"
        self.assertTrue(metadata_from_probe(raw)["variable_frame_rate_hint"])

    def test_invalid_metadata_and_attached_cover_rejected(self):
        for change in [{"width": 0}, {"height": 0}, {"disposition": {"attached_pic": 1}}]:
            raw = synthetic_probe()
            raw["streams"][0].update(change)
            with self.assertRaises(CreatorDNAError):
                metadata_from_probe(raw)
        raw = synthetic_probe()
        raw["streams"][0]["duration"] = "NaN"
        raw["format"]["duration"] = "N/A"
        with self.assertRaises(CreatorDNAError):
            metadata_from_probe(raw)

    def test_schema_and_cli_parameter_validation(self):
        Draft202012Validator.check_schema(schema("video-library"))
        args = parser().parse_args(["analyze-video", "--input", "foo", "--no-face-detection"])
        self.assertEqual(args.max_frames, 120)
        for kwargs in [{"max_frames": 0}, {"max_frames": True}, {"max_frames": 2001},
                       {"scene_threshold": 1}, {"scene_threshold": float("nan")},
                       {"sample_interval": 0}, {"segment_seconds": -1}, {"timeout": 0}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(CreatorDNAError):
                LocalVideoAnalyzer(**kwargs)

    def test_missing_dependencies_and_timeout_are_actionable(self):
        analyzer = LocalVideoAnalyzer(ffprobe="creator-dna-missing-ffprobe")
        with self.assertRaisesRegex(CreatorDNAError, "找不到"):
            analyzer.dependencies()
        with patch("creator_dna.video.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 1)):
            with self.assertRaisesRegex(CreatorDNAError, "执行失败"):
                analyzer.command(["ffprobe"])

    def test_candidate_is_always_reviewable_and_never_claims_mouth_visible(self):
        segment = {"candidate_id": "synthetic", "shot_id": "synthetic", "start": 0, "end": 2, "duration": 2}
        frame = {"frame_id": "synthetic-face", "time": 1, "brightness": 128,
                 "faces": [{"x": 0.3, "y": 0.2, "width": 0.4, "height": 0.4}]}
        c = classify(segment, [frame], {})
        self.assertEqual(c["suggested_categories"], ["A", "B"])
        self.assertIsNone(c["category"])
        self.assertIsNone(c["confidence"])
        self.assertEqual(c["person_visibility"], "unknown")
        self.assertIsNone(c["manual_checks"]["mouth_visibility"]["value"])
        self.assertEqual(c["sampled_face_detection_ratio"], 1)
        c = classify(segment, [], {})
        self.assertIsNone(c["sampled_face_detection_ratio"])
        self.assertTrue(c["quality_issues"])
        frame["brightness"] = 0
        self.assertEqual(classify(segment, [frame], {})["suggested_categories"], ["E"])

    def test_source_folder_separation_and_same_stem_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, workspace = root / "source", root / "workspace"
            source.mkdir()
            (source / "same.mp4").write_bytes(b"synthetic")
            (source / "same.mov").write_bytes(b"synthetic")
            (source / "same.txt").write_text("synthetic", encoding="utf-8")
            (source / "same.wav").write_bytes(b"synthetic")
            self.assertEqual(len(scan_videos(source, workspace)[1]), 2)
            with self.assertRaises(CreatorDNAError):
                scan_videos(source, source / "outputs")
            (source / "sub").mkdir()
            (source / "sub/third.MP4").write_bytes(b"synthetic")
            self.assertEqual(len(scan_videos(source, workspace, recursive=True)[1]), 3)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要本地 FFmpeg / FFprobe")
class LocalVideoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import PIL  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("需要 Pillow")
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "synthetic-input"
        cls.source.mkdir()
        # Red -> blue is a known sharp edit, not a real-world scene/creator example.
        cls.ffmpeg(["-f", "lavfi", "-i", "color=red:s=320x180:r=25:d=1",
                    "-f", "lavfi", "-i", "color=blue:s=320x180:r=25:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a:0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.source / "landscape.mp4")])
        cls.ffmpeg(["-f", "lavfi", "-i", "color=green:s=180x320:r=30000/1001:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.source / "portrait.mp4")])
        (cls.source / "broken.mov").write_bytes(b"synthetic invalid video")
        cls.before = {p.name: (digest_file(p), p.stat().st_mtime_ns) for p in cls.source.iterdir()}
        cls.workspace = Workspace(cls.root / "workspace")
        cls.result = VideoAssetWorkflow(cls.workspace, LocalVideoAnalyzer(sample_interval=0.5, max_frames=12)).analyze(cls.source, "synthetic")
        cls.library = read_json(cls.result["library"])

    @classmethod
    def ffmpeg(cls, args):
        result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_mixed_batch_continues_and_preserves_originals(self):
        self.assertEqual(self.result["status"], "partial")
        self.assertEqual(self.library["summary"]["analyzed_count"], 2)
        self.assertEqual(self.library["summary"]["failed_count"], 1)
        self.assertAlmostEqual(self.library["summary"]["total_duration"], 4.002, places=2)
        self.assertEqual(self.library["summary"]["format_distribution"], {".mov": 1, ".mp4": 2})
        self.assertEqual(self.before, {p.name: (digest_file(p), p.stat().st_mtime_ns) for p in self.source.iterdir()})
        self.assertFalse(any(p.suffix.lower() in {".mp4", ".mov"} for p in Path(self.result["run"]).rglob("*")))
        self.assertEqual(read_json(Path(self.result["run"]) / "run.json")["status"], "partial")

    def test_known_cut_and_candidate_timestamps(self):
        source = next(s for s in self.library["sources"] if s["relative_path"] == "landscape.mp4")
        self.assertEqual(len(source["cuts"]), 1)
        self.assertAlmostEqual(source["cuts"][0], 1, places=2)
        self.assertEqual([(s["start"], s["end"]) for s in source["shots"]], [(0, 1), (1, 2)])
        for candidate in source["candidates"]:
            self.assertTrue(candidate["evidence_frame_ids"])
            self.assertEqual(candidate["status"], "pending_review")
            self.assertIsNone(candidate["category"])
        self.assertEqual(source["metadata"]["audio_streams"][0]["sample_rate"], 48000)
        self.assertEqual(source["metadata"]["video_codec"], "h264")

    def test_separate_output_templates_for_ratio_and_fps(self):
        templates = self.library["output_templates"]
        self.assertEqual(len(templates), 2)
        self.assertEqual({(t["width"], t["height"]) for t in templates}, {(320, 180), (180, 320)})
        self.assertEqual({t["frame_rate"] for t in templates}, {"25", "30000/1001"})
        self.assertEqual(sum(t["preferred_by_duration"] for t in templates), 1)

    def test_contact_sheets_and_report_reference_real_artifacts(self):
        from PIL import Image
        run = Path(self.result["run"])
        for source in self.library["sources"]:
            for contact in source.get("contacts", []):
                with Image.open(run / contact) as image:
                    self.assertEqual(image.width, 960)
                    image.verify()
            for frame in source.get("frames", []):
                self.assertTrue((run / frame["path"]).is_file())
            times = [f["time"] for f in source.get("frames", [])]
            self.assertEqual(len(times), len(set(times)))
        markdown = Path(self.result["report"]).read_text(encoding="utf-8")
        self.assertIn("固定场景数量：未知", markdown)
        self.assertIn("待人工审核", markdown)
        self.assertIn("broken.mov", markdown)
        self.assertEqual(len(read_json(self.result["review"])["candidates"]), self.library["summary"]["candidate_count"])

    def test_schema_and_evidence_invariants_reject_invalid_library(self):
        validate_library(self.library)
        for kind in ["time", "evidence", "schema"]:
            bad = copy.deepcopy(self.library)
            source = next(s for s in bad["sources"] if s["status"] == "analyzed")
            if kind == "time":
                source["candidates"][0]["end"] = 999
            elif kind == "evidence":
                source["candidates"][0]["evidence_frame_ids"].append("invented")
            else:
                source["candidates"][0]["category"] = "A"
            with self.subTest(kind=kind), self.assertRaises(CreatorDNAError):
                validate_library(bad)

    def test_sampling_limit_and_long_shot_subdivision(self):
        folder = self.root / "limited"
        folder.mkdir(exist_ok=True)
        shutil.copyfile(self.source / "portrait.mp4", folder / "synthetic.mp4")
        result = VideoAssetWorkflow(self.workspace, LocalVideoAnalyzer(max_frames=1, segment_seconds=0.4,
                                                                      face_detection=False)).analyze(folder, "limited")
        source = read_json(result["library"])["sources"][0]
        self.assertEqual(len(source["frames"]), 1)
        self.assertTrue(source["sampling_limited"])
        self.assertGreater(len(source["candidates"]), 1)
        self.assertTrue(all(c["duration"] <= 0.4+1e-6 for c in source["candidates"]))
        self.assertTrue(any(not c["evidence_frame_ids"] for c in source["candidates"]))

    def test_contact_sheet_pagination(self):
        folder = self.root / "pages"
        folder.mkdir(exist_ok=True)
        shutil.copyfile(self.source / "portrait.mp4", folder / "synthetic.mp4")
        result = VideoAssetWorkflow(self.workspace, LocalVideoAnalyzer(sample_interval=0.05, max_frames=48,
                                                                      face_detection=False)).analyze(folder, "pages")
        source = read_json(result["library"])["sources"][0]
        self.assertGreater(len(source["frames"]), 24)
        self.assertEqual(len(source["contacts"]), 2)
        self.assertTrue(all((Path(result["run"]) / p).exists() for p in source["contacts"]))

    def test_nonzero_start_pts_remains_relative_to_source_start(self):
        folder = self.root / "offset"
        folder.mkdir(exist_ok=True)
        self.ffmpeg(["-i", str(self.source / "landscape.mp4"), "-c", "copy", "-output_ts_offset", "5",
                     str(folder / "offset.mp4")])
        result = VideoAssetWorkflow(self.workspace, LocalVideoAnalyzer(sample_interval=0.5, max_frames=8,
                                                                      face_detection=False)).analyze(folder, "offset")
        self.assertEqual(result["status"], "completed")
        source = read_json(result["library"])["sources"][0]
        self.assertAlmostEqual(source["cuts"][0], 1, places=2)
        self.assertEqual(source["frames"][0]["time"], 0)
        self.assertTrue(all(0 <= f["time"] < 2 for f in source["frames"]))

    def test_rotated_anamorphic_file_real_probe_and_frame(self):
        folder = self.root / "rotated"
        folder.mkdir(exist_ok=True)
        base = self.root / "anamorphic.mp4"
        self.ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=240x180:r=25:d=1", "-vf", "setsar=2/1",
                     "-c:v", "libx264", str(base)])
        self.ffmpeg(["-display_rotation", "90", "-i", str(base), "-c", "copy", str(folder / "rotation.mp4")])
        result = VideoAssetWorkflow(self.workspace, LocalVideoAnalyzer(max_frames=2, face_detection=False)).analyze(folder, "rotation")
        source = read_json(result["library"])["sources"][0]
        self.assertEqual(source["metadata"]["display_aspect_ratio"], "3/8")
        self.assertEqual(source["metadata"]["sample_aspect_ratio"], "2")
        self.assertEqual((source["metadata"]["display_width"], source["metadata"]["display_height"]), (180, 480))
        from PIL import Image
        with Image.open(Path(result["run"]) / source["frames"][0]["path"]) as frame:
            self.assertAlmostEqual(frame.width/frame.height, 3/8, delta=0.01)

    def test_failed_run_does_not_replace_latest_and_cli_returns_failure(self):
        failed = self.root / "only-broken"
        failed.mkdir(exist_ok=True)
        (failed / "broken.mp4").write_bytes(b"synthetic invalid")
        latest = self.workspace.creator("synthetic") / "video-assets/latest.json"
        before = digest_file(latest)
        result = VideoAssetWorkflow(self.workspace).analyze(failed, "synthetic")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(Path(result["report"]).exists())
        self.assertEqual(digest_file(latest), before)
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(["analyze-video", "--input", str(failed), "--workspace", str(self.workspace.root),
                                   "--creator", "cli-failure"]), 1)

    def test_cli_dispatch_and_source_change_never_publish_mixed_assets(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            code = main(["analyze-video", "--input", str(self.source), "--creator", "cli-test",
                         "--workspace", str(self.workspace.root), "--max-frames", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "partial")
        analyzer = LocalVideoAnalyzer(max_frames=1)
        with patch("creator_dna.video.digest_file", side_effect=["a"*64, "b"*64, "c"*64, "d"*64, "e"*64]):
            result = VideoAssetWorkflow(self.workspace, analyzer).analyze(self.source, "changed")
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.workspace.creator("changed") / "video-assets/latest.json").exists())

    def test_interrupted_run_retains_checkpoint_and_releases_lock(self):
        analyzer = LocalVideoAnalyzer()
        with patch.object(analyzer, "analyze", side_effect=[CreatorDNAError("synthetic failure"), KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                VideoAssetWorkflow(self.workspace, analyzer).analyze(self.source, "interrupted")
        creator = self.workspace.creator("interrupted")
        run = next((creator / "runs").iterdir())
        self.assertEqual(read_json(run / "run.json")["status"], "interrupted")
        self.assertEqual(len(read_json(run / "partial-library.json")["sources"]), 1)
        self.assertFalse((creator / ".lock").exists())


if __name__ == "__main__":
    unittest.main()
