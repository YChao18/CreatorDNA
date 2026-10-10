"""Local engineering validation; synthetic media is not style/model evidence."""

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from creator_dna.cli import main, parser
from creator_dna.contracts import schema, validate
from creator_dna.errors import CreatorDNAError
from creator_dna.storage import digest_file, read_json, write_json
from creator_dna.task_cli import TerminalProgress
from creator_dna.task_progress import FFmpegProgressParser, progress
from creator_dna.tasks import STAGES, TaskManager, stage


class ProgressTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows replacement sharing race")
    def test_atomic_write_retries_transient_sharing_error_and_preserves_old_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "status.json"
            write_json(target, {"status": "pending"})
            replace = os.replace
            calls = []
            def race(source, destination):
                calls.append(True)
                if len(calls) <= 2:
                    raise PermissionError("synthetic sharing violation")
                replace(source, destination)
            with patch("creator_dna.storage.os.replace", side_effect=race):
                write_json(target, {"status": "running"})
            self.assertEqual(len(calls), 3)
            self.assertEqual(read_json(target)["status"], "running")
            with patch("creator_dna.storage.os.replace", side_effect=PermissionError("synthetic persistent denial")) as mocked:
                with self.assertRaises(PermissionError):
                    write_json(target, {"status": "failed"})
                self.assertEqual(mocked.call_count, 7)
            self.assertEqual(read_json(target)["status"], "running")
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_native_ffmpeg_time_units_fallback_and_not_completion(self):
        p = FFmpegProgressParser(10)
        self.assertIsNone(p.feed("out_time_us=2500000"))
        self.assertEqual(p.feed("progress=continue")["percent"], 25)
        p.feed("out_time_ms=4000000")
        self.assertEqual(p.feed("progress=continue")["percent"], 40)
        p.feed("out_time=00:00:06.500000")
        self.assertEqual(p.feed("progress=end")["percent"], 65)  # Not coerced to 100.
        p.feed("out_time_us=-10")
        self.assertEqual(p.feed("progress=continue")["percent"], 65)
        p.feed("out_time_us=20000000")
        self.assertEqual(p.feed("progress=end")["percent"], 100)

    def test_unknown_duration_and_invalid_parameters(self):
        p = FFmpegProgressParser()
        p.feed("out_time_us=1000000")
        data = p.feed("progress=continue")
        self.assertEqual(data["completed"], 1)
        self.assertIsNone(data["percent"])
        for bad in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaises(CreatorDNAError):
                FFmpegProgressParser(bad)
        for args in ((2, 1), (-1, None), (0, 0), (None, 1), (float("nan"), None)):
            with self.assertRaises(CreatorDNAError):
                progress(*args)
        self.assertIsNone(progress(completed=2, unit="chunks")["percent"])
        Draft202012Validator.check_schema(schema("task-status"))


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"PYTHONPATH": str(Path(__file__).parent) + os.pathsep + os.environ.get("PYTHONPATH", "")})
        self.env.start()
        self.manager = TaskManager(self.root / "workspace", executors={name: f"task_fixtures:{name}" for name in
                                   ("unknown_progress", "fail_once", "hang_with_child", "invalid_result", "ffmpeg_bad_filter")}, heartbeat=0.05)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def create(self, specs=None):
        return self.manager.create(specs or [stage("material_analysis", "local_chunks", {"chunks": 4, "delay": 0.07}, retry_safe=True),
                                            stage("final_check", "check_chunks", retry_safe=True)], "synthetic-creator", kind="synthetic-test")

    def test_completed_progress_stage_switch_persistence_and_idempotency(self):
        task = self.create()
        self.assertEqual(task["status"], "pending")
        observations = []
        state = self.manager.run(task["task_id"], observer=lambda s: observations.append(copy.deepcopy(s)))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["overall_progress"]["percent"], 100)
        self.assertFalse(state["overall_progress"]["estimated"])
        self.assertTrue(any(0 < s["progress"]["percent"] < 100 for s in observations if s["progress"]["percent"] is not None))
        self.assertEqual({s["current_stage"] for s in observations}, {None, "material_analysis", "final_check"})
        self.assertTrue(any(s["overall_progress"]["estimated"] for s in observations))
        before = {p: (digest_file(p), Path(p).stat().st_mtime_ns) for p in state["output_files"]}
        self.assertEqual(TaskManager(self.root / "workspace").status(task["task_id"]), state)
        self.assertEqual(self.manager.run(task["task_id"]), state)
        self.assertEqual(before, {p: (digest_file(p), Path(p).stat().st_mtime_ns) for p in state["output_files"]})
        self.assertTrue(state["started_at"] and state["finished_at"] and state["elapsed_seconds"] > 0)
        validate(state, "task-status")
        logs = self.manager.logs(task["task_id"], 200)
        self.assertIn("stage_completed", {l["event"] for l in logs})
        self.assertTrue(all(set(l) == {"timestamp", "task_id", "level", "stage", "attempt", "event", "message", "data"} for l in logs))
        self.assertEqual(len(self.manager.history("synthetic-creator", "completed")), 1)

    def test_unknown_provider_has_live_elapsed_and_no_fabricated_percentage(self):
        task = self.create([stage("tts", "unknown_progress", retry_safe=True)])
        observations = []
        state = self.manager.run(task["task_id"], observer=lambda s: observations.append(copy.deepcopy(s)))
        running = [s for s in observations if s["current_object"] == "synthetic-job-001" and s["stages"][0]["status"] == "running"]
        self.assertGreater(len(running), 2)
        self.assertTrue(all(s["progress"]["percent"] is None and s["overall_progress"]["percent"] is None for s in running))
        self.assertGreater(running[-1]["elapsed_seconds"], running[0]["elapsed_seconds"])
        self.assertTrue(any(s["progress"]["completed"] == 1 for s in running))
        self.assertEqual(state["status"], "completed")
        display = io.StringIO()
        TerminalProgress(display)(running[-1])
        self.assertIn("未知百分比", display.getvalue())
        self.assertIn("开始时间", display.getvalue())

    def test_retry_is_bounded_and_failed_outputs_are_not_published(self):
        task = self.create([stage("material_analysis", "local_chunks", {"chunks": 2, "delay": 0, "fail_until": 1},
                                  retry_safe=True, max_attempts=2)])
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["stages"][0]["attempt"], 2)
        self.assertEqual(state["last_error"]["type"], "SyntheticTransientError")
        self.assertTrue(all("attempt-002" in p for p in state["output_files"]))
        self.assertIn("retry", {l["event"] for l in self.manager.logs(task["task_id"])})
        task = self.create([stage("material_analysis", "local_chunks", {"fail_until": 10}, retry_safe=True, max_attempts=2)])
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["stages"][0]["attempt"], 2)
        self.assertEqual(state["output_files"], [])
        task = self.create([stage("tts", "fail_once", retry_safe=True, max_attempts=3)])
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["stages"][0]["attempt"], 1)  # Non-retryable errors stop.

    def test_resume_preserves_completed_checkpoint_and_rejects_tampering(self):
        task = self.create([stage("material_analysis", "local_chunks", {"chunks": 2, "delay": 0}, retry_safe=True),
                            stage("final_check", "fail_once", retry_safe=True)])
        failed = self.manager.run(task["task_id"])
        self.assertEqual(failed["status"], "failed")
        before = failed["stages"][0]["artifacts"]
        with self.assertRaises(CreatorDNAError):
            self.manager.run(task["task_id"])
        complete = self.manager.run(task["task_id"], resume=True)
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(complete["stages"][0]["attempt"], 1)
        self.assertEqual(complete["stages"][0]["artifacts"], before)
        self.assertEqual(complete["stages"][1]["attempt"], 2)
        self.assertGreaterEqual(complete["elapsed_seconds"], failed["elapsed_seconds"])
        Path(before[0]["path"]).write_bytes(b"tampered synthetic output")
        with self.assertRaisesRegex(CreatorDNAError, "缺失或改变"):
            self.manager.run(task["task_id"], resume=True)

    def test_cancel_pending_running_and_cancelled_resume(self):
        task = self.create()
        self.assertEqual(self.manager.cancel(task["task_id"])["status"], "cancelled")
        complete = self.manager.run(task["task_id"], resume=True)
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(self.manager.cancel(task["task_id"])["status"], "completed")
        task = self.create([stage("tts", "unknown_progress", {"delay": 3}, retry_safe=True)])
        requested = []
        def cancel(s):
            if s["current_object"] and not requested:
                requested.append(TaskManager(self.root / "workspace").cancel(s["task_id"]))
        state = self.manager.run(task["task_id"], observer=cancel)
        self.assertTrue(requested[0]["cancel_requested"])
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["stages"][0]["status"], "cancelled")
        self.assertEqual(state["output_files"], [])
        self.assertFalse((self.manager.folder(task["task_id"]) / ".lock").exists())

    def test_timeout_and_cancellation_stop_subprocess_tree(self):
        for cancel in (False, True):
            task = self.create([stage("tts", "hang_with_child", timeout=1.5, retry_safe=True)])
            requested = []
            def observe(s):
                if cancel and s["current_object"] and not requested:
                    requested.append(True)
                    self.manager.cancel(s["task_id"])
            state = self.manager.run(task["task_id"], observer=observe)
            self.assertEqual(state["status"], "cancelled" if cancel else "failed")
            self.assertEqual(state["error"]["type"], "Cancelled" if cancel else "TaskTimeout")
            attempt = Path(state["stages"][0]["attempts"][0]["directory"])
            pid = read_json(attempt / "child.json")["pid"]
            if os.name == "nt":
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    code = ctypes.c_ulong()
                    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                    ctypes.windll.kernel32.CloseHandle(handle)
                    self.assertNotEqual(code.value, 259)  # STILL_ACTIVE
            else:
                # A killed but not yet reaped orphan may briefly remain a zombie.
                stat = Path(f"/proc/{pid}/stat")
                if stat.exists():
                    self.assertEqual(stat.read_text().split()[2], "Z")

    def test_validation_lock_and_invalid_result(self):
        self.assertEqual(len(STAGES), 7)
        for specs in ([stage("tts", "unimplemented")], [stage("tts", "local_chunks", max_attempts=2)],
                      [stage("tts", "local_chunks", timeout=float("nan"))]):
            with self.assertRaises(CreatorDNAError):
                self.create(specs)
        with self.assertRaises(CreatorDNAError):
            self.manager.status("../escape")
        task = self.create([stage("tts", "invalid_result")])
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["error"]["type"], "InvalidResult")
        with self.assertRaisesRegex(CreatorDNAError, "安全重复"):
            self.manager.run(task["task_id"], resume=True)
        with self.manager._lock(self.manager.folder(task["task_id"])):
            with self.assertRaisesRegex(CreatorDNAError, "执行进程"):
                self.manager.run(task["task_id"], resume=True)

    def test_cancel_during_retry_delay_does_not_launch_next_attempt(self):
        task = self.create([stage("material_analysis", "local_chunks", {"fail_until": 10},
                                  max_attempts=3, retry_safe=True, retry_delay=1)])
        requested = []
        def cancel(s):
            if s["error"] and not requested:
                requested.append(True)
                self.manager.cancel(s["task_id"])
        state = self.manager.run(task["task_id"], observer=cancel)
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["stages"][0]["attempt"], 1)
        self.assertEqual(state["output_files"], [])

    def test_cli_create_run_status_history_logs_and_exit_codes(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", errors):
            self.assertEqual(main(["tasks", "create", "--workspace", str(self.root / "workspace"), "--chunks", "2", "--delay", "0", "--run"]), 0)
        state = json.loads(output.getvalue())
        for op, suffix in (("status", ["--task", state["task_id"], "--watch"]),
                           ("logs", ["--task", state["task_id"], "--tail", "4"]), ("history", ["--limit", "2"]),
                           ("cancel", ["--task", state["task_id"]])):
            with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(main(["tasks", op, "--workspace", str(self.root / "workspace"), *suffix]), 0)
        task = self.create([stage("tts", "invalid_result")])
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(["tasks", "run", "--task", task["task_id"], "--workspace", str(self.root / "workspace")]), 1)
            self.assertEqual(main(["tasks", "create", "--delay", "nan", "--workspace", str(self.root / "workspace")]), 1)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要本地 FFmpeg / FFprobe")
class FFmpegTaskTests(unittest.TestCase):
    setUp = TaskTests.setUp
    tearDown = TaskTests.tearDown
    create = TaskTests.create
    def test_real_ffmpeg_progress_switch_and_decode(self):
        task = self.create([stage("render", "ffmpeg_demo", {"duration": 1.2}, retry_safe=True, timeout=30),
                            stage("final_check", "check_video", retry_safe=True, timeout=30)])
        observations = []
        state = self.manager.run(task["task_id"], observer=lambda s: observations.append(copy.deepcopy(s)))
        self.assertEqual(state["status"], "completed", state["error"])
        self.assertTrue(state["stages"][1]["result"]["decode_ok"])
        self.assertTrue(any(s["progress"]["unit"] == "media_seconds" and 0 < s["progress"]["percent"] < 100 for s in observations))
        self.assertTrue(any("render" == s["current_stage"] for s in observations))
        self.assertTrue(any("final_check" == s["current_stage"] for s in observations))
        self.assertTrue(any(p.endswith("synthetic-demo.mp4") for p in state["output_files"]))

    def test_successful_exit_without_artifact_rejected(self):
        task = self.create([stage("render", "ffmpeg_demo", {"duration": 0.2, "ffmpeg": sys.executable}, retry_safe=True)])
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error"]["type"], "InvalidArtifact")
        self.assertEqual(state["output_files"], [])

    def test_real_ffmpeg_failure_leaves_no_completed_outputs(self):
        task = self.create([stage("render", "ffmpeg_bad_filter", retry_safe=True, max_attempts=2)])
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error"]["type"], "FFmpegError")
        self.assertEqual(state["stages"][0]["attempt"], 1)
        self.assertEqual(state["output_files"], [])

    def test_real_ffmpeg_cancel_and_timeout_keep_partial_files_unpublished(self):
        for cancel in (True, False):
            task = self.create([stage("render", "ffmpeg_demo", {"duration": 3}, timeout=2 if cancel else 0.6,
                                      retry_safe=True), stage("final_check", "check_video", retry_safe=True)])
            requested = []
            def observe(s):
                if cancel and s["progress"]["completed"] and not requested:
                    requested.append(True)
                    self.manager.cancel(s["task_id"])
            state = self.manager.run(task["task_id"], observer=observe)
            self.assertEqual(state["status"], "cancelled" if cancel else "failed")
            self.assertEqual(state["error"]["type"], "Cancelled" if cancel else "TaskTimeout")
            self.assertEqual(state["stages"][1]["status"], "pending")
            self.assertEqual(state["output_files"], [])
            self.assertIsNone(state["overall_progress"]["percent"])

    def test_real_analysis_adapter_source_readonly_and_snapshot(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("需要 Pillow")
        source = self.root / "input"
        source.mkdir()
        video = source / "synthetic.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                        "testsrc2=s=160x90:r=25:d=0.6", "-c:v", "mpeg4", str(video)], check=True, timeout=30)
        before = digest_file(video), video.stat().st_mtime_ns
        args = parser().parse_args(["tasks", "create", "--kind", "analyze-video", "--input", str(source),
                                    "--workspace", str(self.root / "workspace"), "--max-frames", "3", "--no-face-detection"])
        from creator_dna.cli import execute
        task = execute(args)
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["status"], "completed", state["error"])
        self.assertEqual(state["stages"][0]["result"]["analysis_status"], "completed")
        self.assertEqual(before, (digest_file(video), video.stat().st_mtime_ns))
        self.assertFalse((self.root / "workspace/creators").exists())
        self.assertTrue(any(p.endswith("library.json") for p in state["output_files"]))
        task = execute(args)
        video.write_bytes(b"synthetic changed video")
        state = self.manager.run(task["task_id"])
        self.assertEqual(state["error"]["type"], "SourceChanged")
        self.assertEqual(state["output_files"], [])
