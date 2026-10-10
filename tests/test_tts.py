"""Mock HTTP + synthetic WAV engineering tests; no real voice quality evidence."""

import copy
import io
import os
import shutil
import tempfile
import unittest
import wave
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from creator_dna.cli import main, parser
from creator_dna.contracts import validate
from creator_dna.storage import Workspace, digest_file, now, read_json, write_json
from creator_dna.tasks import TaskManager, stage
from creator_dna.tts import SynthesisReceipt, TTSError, config, make_provider
from creator_dna.tts_audio import split_text, wave_metadata
from creator_dna.tts_providers import HTTPTransport, NoRedirect
from creator_dna.tts_workflow import create_synthesis_task, create_voice_task, resolve_segment, synthesize_stage
from creator_dna.voice_assets import VoiceAssets


def settings(provider="qwen", **fields):
    return config({"provider": provider, "model": "qwen3-tts-vc-2026-01-22" if provider == "qwen" else "speech-2.8-hd", **fields})


def consent(provider):
    return {"provider": provider.name, "scope": provider.scope, "authorized": True,
            "cloud_processing": True, "confirmation": "Synthetic engineering test authorization only", "confirmed_at": now()}


def wav_bytes(seconds=.1, rate=24000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * round(seconds * rate))
    return buffer.getvalue()


def seed_asset(workspace, provider, creator="synthetic-creator"):
    assets = VoiceAssets(workspace, creator)
    data = {"schema_version": "3.0-03", "asset_id": "voice-" + "a"*32, "creator_id": creator,
            "provider": provider.name, "scope": provider.scope, "voice_id": "SyntheticVoice001",
            "models": [provider.settings["model"]], "created_at": now(), "source": {"synthetic_only": True},
            "status": "ready", "consent": consent(provider), "last_used_at": None}
    assets.save(data)
    return assets, data


class Context:
    def __init__(self, root, attempt=1):
        self.creator_id = "synthetic-creator"
        self.attempt_dir = root / "stages" / "tts" / f"attempt-{attempt:03d}"
        self.output_dir = self.attempt_dir / "outputs"
        self.output_dir.mkdir(parents=True)
        self.events = []
        self.cancel = False

    def check_cancelled(self):
        if self.cancel:
            raise TTSError("Cancelled")

    def report(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def log(self, *args, **kwargs):
        self.events.append((args, kwargs))


class ProviderTests(unittest.TestCase):
    def test_config_rejects_secrets_unknown_models_and_untrusted_endpoint(self):
        for value in [{"api_key": "never-persist"}, {"model": "guessed-model"}, {"base_url": "https://attacker.test"},
                      {"base_url": "http://dashscope.aliyuncs.com/api/v1"}, {"api_key_env": "../secret"},
                      {"max_attempts": 4}, {"segment_chars": True}, {"request_timeout": float("inf")}, {"format": "pcm"}]:
            with self.subTest(value=value), self.assertRaises(TTSError):
                make_provider({**settings(), **value})

    def test_capabilities_distinguish_models_emotions_and_unsupported_operations(self):
        q = make_provider(settings())
        self.assertEqual(q.capabilities().max_characters, 600)
        self.assertFalse(q.capabilities().timestamps)
        for field in ({"speed": 1.1}, {"emotion": "happy"}, {"subtitles": True}):
            with self.assertRaisesRegex(TTSError, "Unsupported"):
                make_provider(settings(**field))
        m = make_provider(settings("minimax", emotion="happy"))
        self.assertTrue(m.capabilities().timestamps)
        with self.assertRaises(TTSError):
            make_provider(settings("minimax", emotion="whisper"))
        self.assertIn("whisper", make_provider(settings("minimax", model="speech-2.6-hd")).capabilities().emotions)
        for action in (lambda: q.query_voice("id"), lambda: q.update_voice("id", "file"), lambda: m.query_task("job")):
            with self.assertRaisesRegex(TTSError, "Unsupported"):
                action()

    def test_qwen_exact_synthesis_contract_and_download_without_authorization(self):
        transport = Mock()
        transport.request.side_effect = [{"output": {"audio": {"url": "https://audio.example/result.wav"}}, "request_id": "r1"}, wav_bytes()]
        provider = make_provider(settings(), transport)
        receipt = provider.synthesize("全新文案", "qwen-voice")
        call = transport.request.call_args_list[0]
        self.assertTrue(call.args[1].endswith("/services/aigc/multimodal-generation/generation"))
        self.assertEqual(call.args[2], {"model": settings()["model"], "input": {"text": "全新文案", "voice": "qwen-voice"}})
        self.assertEqual(provider.download(receipt)[0], wav_bytes())
        self.assertFalse(transport.request.call_args.kwargs["authenticated"])

    def test_minimax_exact_hex_subtitle_contract_and_null_audio_failure(self):
        t = Mock()
        t.request.side_effect = [{"base_resp": {"status_code": 0}, "data": {"audio": wav_bytes().hex(), "status": 2,
                                "subtitle_file": "https://audio.example/subtitle.json"}}, b'[{"time_begin":0,"time_end":100,"text":"new"}]']
        p = make_provider(settings("minimax", speed=1.2, emotion="calm", subtitles=True), t)
        receipt = p.synthesize("new", "MiniMaxVoice001")
        body = t.request.call_args.args[2]
        self.assertEqual(body["voice_setting"], {"voice_id": "MiniMaxVoice001", "speed": 1.2, "emotion": "calm"})
        self.assertEqual(body["subtitle_type"], "sentence")
        audio, timestamps = p.download(receipt)
        self.assertEqual(audio, wav_bytes())
        self.assertEqual(timestamps[0]["time_end"], 100)
        t.request.side_effect = None
        t.request.return_value = {"base_resp": {"status_code": 0}, "data": None}
        with self.assertRaisesRegex(TTSError, "InvalidAudioResponse"):
            p.synthesize("new", "id")

    def test_clone_consent_precedes_all_network_and_no_invented_success(self):
        for service in ("qwen", "minimax"):
            t, p = Mock(), None
            p = make_provider(settings(service), t)
            for bad in (None, {}, {**consent(p), "authorized": False}, {**consent(p), "scope": "other"}):
                with self.assertRaisesRegex(TTSError, "ConsentRequired"):
                    p.create_voice("missing.wav", "Synthetic001", bad)
            t.request.assert_not_called()
        with tempfile.TemporaryDirectory() as temp, patch("creator_dna.tts_audio.validate_reference"):
            path = Path(temp) / "reference.wav"
            path.write_bytes(wav_bytes())
            t = Mock()
            t.request.return_value = {"output": {"voice": "qwen-voice", "target_model": settings()["model"]}}
            p = make_provider(settings(), t)
            self.assertEqual(p.create_voice(path, "Sample", consent(p))["status"], "ready")
            self.assertEqual(t.request.call_args.args[2]["input"]["action"], "create")
            t.request.return_value = {"output": {}}
            with self.assertRaises(TTSError):
                p.create_voice(path, "Sample", consent(p))
            t.request.side_effect = [{"base_resp": {"status_code": 0}, "file": {"file_id": 123}}, {"base_resp": {"status_code": 0}}]
            p = make_provider(settings("minimax"), t)
            result = p.create_voice(path, "Synthetic001", consent(p))
            self.assertEqual(result["status"], "pending_activation")
            self.assertEqual(t.request.call_args.args[2], {"file_id": 123, "voice_id": "Synthetic001"})

    def test_voice_management_contracts(self):
        t = Mock()
        t.request.return_value = {"output": {"voice_list": []}}
        p = make_provider(settings(), t)
        p.list_voices(2)
        self.assertEqual(t.request.call_args.args[2]["input"], {"action": "list", "page_index": 2, "page_size": 100})
        t.request.return_value = {"output": {"voice": "voice"}}
        p.delete_voice("voice")
        self.assertEqual(t.request.call_args.args[2]["input"], {"action": "delete", "voice": "voice"})
        t.request.return_value = {"base_resp": {"status_code": 0}, "voice_cloning": []}
        p = make_provider(settings("minimax"), t)
        self.assertTrue(p.list_voices()["inactive_voices_omitted"])
        p.delete_voice("voice")
        self.assertEqual(t.request.call_args.args[2], {"voice_type": "voice_cloning", "voice_id": "voice"})

    def test_documented_china_endpoint_and_retry_after_are_preserved(self):
        from creator_dna.tts_providers import retry_seconds
        p = make_provider(settings("minimax", base_url="https://api.minimax.cn", api_key_env="MINIMAX_CN_API_KEY"))
        self.assertIn("api.minimax.cn", p.scope)
        self.assertEqual(retry_seconds("120"), 120)
        self.assertEqual(retry_seconds("invalid"), 0)
        for payload in ({"base_resp": {"status_code": False}}, {"base_resp": {"status_code": []}}, {"base_resp": None}):
            t = Mock()
            t.request.return_value = payload
            with self.assertRaises(TTSError):
                make_provider(settings("minimax"), t).synthesize("new", "voice")

    def test_error_codes_and_rate_rejection_are_bounded_not_silent_retries(self):
        t = Mock()
        p = make_provider(settings("minimax"), t)
        for code, safe in [(1002, True), (1001, False), (1004, False), (1008, False), (20132, False), (1024, False)]:
            t.request.return_value = {"base_resp": {"status_code": code, "status_msg": "secret-echo"}}
            with self.assertRaises(TTSError) as exc:
                p.synthesize("new", "voice")
            self.assertEqual(exc.exception.rejected, safe)
            self.assertNotIn("secret-echo", str(exc.exception))

    def test_http_secret_redaction_missing_keys_timeout_and_redirects(self):
        key = "synthetic-test-key-not-real"
        with patch.dict(os.environ, {"TEST_TTS_KEY": key}), patch("creator_dna.tts_providers.build_opener") as opener:
            transport = HTTPTransport("TEST_TTS_KEY", 20)
            opener.return_value.open.side_effect = URLError(key)
            with self.assertRaises(TTSError) as exc:
                transport.request("POST", "https://api.minimax.io/v1/t2a_v2", {"text": "test"})
            self.assertNotIn(key, str(exc.exception))
            opener.return_value.open.side_effect = HTTPError("url", 429, key, {"Retry-After": "5"}, None)
            with self.assertRaises(TTSError) as exc:
                transport.request("POST", "https://api.minimax.io/v1/t2a_v2", {})
            self.assertTrue(exc.exception.rejected)
            self.assertEqual(exc.exception.retry_after, 5)
            opener.return_value.open.side_effect = None
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = ('{"message":"'+key+'"}').encode()
            with self.assertRaisesRegex(TTSError, "CredentialInResponse"):
                transport.request("POST", "https://api.minimax.io/v1/t2a_v2", {})
        with patch.dict(os.environ, {"TEST_TTS_KEY": ""}), patch("creator_dna.tts_providers.build_opener") as opener:
            with self.assertRaisesRegex(TTSError, "MissingAPIKey"):
                HTTPTransport("TEST_TTS_KEY", 20).request("POST", "https://api.minimax.io/v1/t2a_v2", {})
            opener.assert_not_called()
        with self.assertRaisesRegex(TTSError, "UnexpectedRedirect"):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.test")

    def test_split_preserves_offsets_limits_paragraph_pauses_and_unicode(self):
        text = "  全新选题。第二句内容！第三句。\n\n 下一段😀English-long-text-without-punctuation "
        segments = split_text(text, 8, .35)
        self.assertTrue(all(0 < len(s["text"]) <= 8 for s in segments))
        self.assertTrue(all(text[s["text_start"]:s["text_end"]] == s["text"] for s in segments))
        self.assertEqual(sum(s["pause_after"] > 0 for s in segments), 1)
        self.assertEqual(segments[-1]["pause_after"], 0)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "local media tools required")
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root / "workspace")
        self.transport = Mock()
        self.transport.request.side_effect = lambda method, url, *a, **k: (
            {"output": {"audio": {"url": "https://audio.example/test.wav"}}, "request_id": "synthetic-request"}
            if method == "POST" else wav_bytes())
        self.provider = make_provider(settings(segment_chars=6), self.transport)
        self.assets, self.asset = seed_asset(self.workspace, self.provider)
        self.params = {"workspace": str(self.workspace.root), "config": self.provider.settings,
                       "voice_asset": self.asset["asset_id"], "voice_id_snapshot": self.asset["voice_id"], "text": "全新文案一。\n第二段。"}
        self.pausing = patch("creator_dna.tts_workflow.pause", side_effect=lambda ctx, seconds: ctx.check_cancelled())
        self.pausing.start()

    def tearDown(self):
        self.pausing.stop()
        self.temp.cleanup()

    def run_stage(self, attempt=1):
        ctx = Context(self.root, attempt)
        result = synthesize_stage(ctx, self.params, self.provider)
        return ctx, result

    def post_count(self):
        return sum(c.args[0] == "POST" for c in self.transport.request.call_args_list)

    def test_actual_audio_assembly_manifest_measured_timing_and_safe_reuse(self):
        ctx, result = self.run_stage()
        manifest = read_json(ctx.output_dir / "speech.json")
        validate(manifest, "tts-manifest")
        self.assertEqual(result["segments"], 2)
        self.assertAlmostEqual(wave_metadata(ctx.output_dir / "speech.wav")["duration_seconds"], .55)
        self.assertAlmostEqual(manifest["segments"][1]["start_seconds"], .45)
        self.assertTrue(all(s["word_timestamps"] is None and s["provider_timestamps"] is None for s in manifest["segments"]))
        self.assertEqual(self.post_count(), 2)
        self.run_stage(2)
        self.assertEqual(self.post_count(), 2)
        self.assertEqual(self.assets.get(self.asset["asset_id"])["status"], "ready")

    def test_download_failure_resumes_from_receipt_without_paid_repeat(self):
        self.transport.request.side_effect = [{"output": {"audio": {"url": "https://audio.example/test.wav"}}}, TTSError("NetworkOutcomeUnknown")]
        with self.assertRaises(TTSError):
            self.run_stage()
        self.assertEqual(self.post_count(), 1)
        self.transport.request.side_effect = lambda method, url, *a, **k: ({"output": {"audio": {"url": "https://audio.example/test.wav"}}} if method == "POST" else wav_bytes())
        self.run_stage(2)
        self.assertEqual(self.post_count(), 2)  # only the previously unsent second segment

    def test_unknown_paid_outcome_stops_resume_without_network(self):
        self.transport.request.side_effect = TTSError("NetworkOutcomeUnknown")
        with self.assertRaises(TTSError):
            self.run_stage()
        with self.assertRaisesRegex(TTSError, "BillableOutcomeUnknown"):
            self.run_stage(2)
        self.assertEqual(self.post_count(), 1)

    def test_rate_limit_only_retry_budget_persists_across_resume(self):
        self.transport.request.side_effect = TTSError("HTTP429", rejected=True)
        with self.assertRaisesRegex(TTSError, "RequestBudgetExhausted"):
            self.run_stage()
        self.assertEqual(self.post_count(), 2)
        with self.assertRaisesRegex(TTSError, "RequestBudgetExhausted"):
            self.run_stage(2)
        self.assertEqual(self.post_count(), 2)

    def test_retry_rejection_then_success_and_cancellation_before_request(self):
        self.params["text"] = "全新。"
        self.transport.request.side_effect = [TTSError("HTTP429", rejected=True),
            {"output": {"audio": {"url": "https://audio.example/test.wav"}}}, wav_bytes()]
        self.run_stage()
        self.assertEqual(self.post_count(), 2)
        ctx = Context(self.root, 2)
        ctx.cancel = True
        with self.assertRaisesRegex(TTSError, "Cancelled"):
            synthesize_stage(ctx, self.params, self.provider)
        self.assertEqual(self.post_count(), 2)

    def test_corrupt_checkpoint_and_changed_text_refuse_paid_repeat(self):
        self.run_stage()
        checkpoint = self.root / "stages/tts/checkpoints/segment-0000.wav"
        checkpoint.write_bytes(b"corrupt")
        with self.assertRaisesRegex(TTSError, "CheckpointCorrupt"):
            self.run_stage(2)
        self.params["text"] += "修改"
        with self.assertRaisesRegex(TTSError, "CheckpointInputChanged"):
            self.run_stage(3)
        self.assertEqual(self.post_count(), 2)

    def test_provider_account_model_and_authorization_isolation(self):
        for mutate in (lambda a: a.update(provider="minimax"), lambda a: a.update(scope="different-account"),
                       lambda a: a.update(models=["different-model"]), lambda a: a["consent"].update(scope="other")):
            asset = copy.deepcopy(self.asset)
            mutate(asset)
            self.assets.save(asset)
            with self.assertRaises(TTSError):
                self.assets.usable(asset["asset_id"], self.provider)
        self.assertEqual(self.post_count(), 0)

    def test_reference_read_only_registration_dedup_and_source_tamper(self):
        source = self.root / "original.wav"
        source.write_bytes(wav_bytes(10))
        before = digest_file(source)
        ref = self.assets.add_reference(self.provider.settings, source=source, consent=consent(self.provider))
        self.assertEqual(digest_file(source), before)
        self.transport.request.side_effect = None
        self.transport.request.return_value = {"output": {"voice": "qwen-cloned", "target_model": settings()["model"]}}
        first = self.assets.create(ref["reference_id"], self.provider, "Sample")
        second = self.assets.create(ref["reference_id"], self.provider, "OtherName")
        self.assertEqual(first["voice_id"], second["voice_id"])
        self.assertEqual(self.post_count(), 1)
        (self.assets.root / "references" / ref["audio"]).write_bytes(b"changed")
        with self.assertRaisesRegex(TTSError, "ReferenceChanged"):
            self.assets.create(ref["reference_id"], self.provider, "Sample")

    def test_failed_clone_never_saves_valid_voice_and_never_auto_repeats(self):
        source = self.root / "original.wav"
        source.write_bytes(wav_bytes(10))
        ref = self.assets.add_reference(self.provider.settings, source=source, consent=consent(self.provider))
        self.transport.request.side_effect = TTSError("NetworkOutcomeUnknown")
        with self.assertRaises(TTSError):
            self.assets.create(ref["reference_id"], self.provider, "Sample")
        failed = next(a for a in self.assets.voices() if a["asset_id"] != self.asset["asset_id"])
        self.assertIsNone(failed["voice_id"])
        self.assertEqual(failed["status"], "outcome_unknown")
        with self.assertRaisesRegex(TTSError, "VoiceCreationNeedsReview"):
            self.assets.create(ref["reference_id"], self.provider, "Sample")
        self.assertEqual(self.post_count(), 1)

    def test_existing_voice_import_requires_real_listing_and_reuses_asset(self):
        self.transport.request.side_effect = None
        self.transport.request.return_value = {"output": {"voice_list": [{"voice": "existing-cloud-id", "target_model": settings()["model"]}], "total_count": 1}}
        first = self.assets.import_voice(self.provider, "existing-cloud-id", consent(self.provider))
        second = self.assets.import_voice(self.provider, "existing-cloud-id", consent(self.provider))
        self.assertEqual(first["asset_id"], second["asset_id"])
        with self.assertRaisesRegex(TTSError, "VoiceNotFound"):
            self.assets.import_voice(self.provider, "invented-id", consent(self.provider))
        self.assertEqual(len(self.assets.voices()), 2)

    def test_degraded_qwen_clone_requires_review_and_cannot_synthesize(self):
        source = self.root / "original.wav"
        source.write_bytes(wav_bytes(10))
        ref = self.assets.add_reference(self.provider.settings, source=source, consent=consent(self.provider))
        self.transport.request.side_effect = None
        self.transport.request.return_value = {"output": {"voice": "degraded-id", "target_model": settings()["model"], "fallback_mode": True}}
        asset = self.assets.create(ref["reference_id"], self.provider, "Sample")
        self.assertEqual(asset["status"], "review_required")
        with self.assertRaisesRegex(TTSError, "VoiceNotReady"):
            self.assets.usable(asset["asset_id"], self.provider)

    def test_minimax_activation_deadline_and_actual_reference_constraints(self):
        p = make_provider(settings("minimax"), self.transport)
        assets, asset = seed_asset(self.workspace, p, "second-creator")
        asset.update(status="pending_activation", activation_deadline="2020-01-01T00:00:00+00:00")
        assets.save(asset)
        with self.assertRaisesRegex(TTSError, "VoiceActivationExpired"):
            assets.usable(asset["asset_id"], p)
        source = self.root / "short.wav"
        source.write_bytes(wav_bytes(.1))
        with self.assertRaisesRegex(TTSError, "ReferenceRequirementsNotMet"):
            p.create_voice(source, "Synthetic001", consent(p))
        self.assertEqual(self.post_count(), 0)

    def test_create_task_is_network_free_and_cli_missing_key_fails_cleanly(self):
        state = create_synthesis_task(self.workspace, "synthetic-creator", self.asset["asset_id"], self.provider.settings, "新文案")
        self.assertEqual(state["status"], "pending")
        self.assertEqual(self.post_count(), 0)
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            manager = TaskManager(self.workspace, heartbeat=.05)
            final = manager.run(state["task_id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error"]["type"], "MissingAPIKey")
        self.assertEqual(final["output_files"], [])
        self.assertEqual(len(final["stages"][0]["attempts"]), 1)

    def test_human_resolution_releases_one_unknown_call_without_resetting_budget(self):
        manager = TaskManager(self.workspace)
        state = create_synthesis_task(self.workspace, "synthetic-creator", self.asset["asset_id"], self.provider.settings, "新文案")
        folder = manager.folder(state["task_id"])
        state.update(status="failed", error={"type": "BillableOutcomeUnknown", "message": "unknown", "retryable": False})
        write_json(folder / "status.json", state)
        path = folder / "stages/tts/checkpoints/segment-0000.json"
        write_json(path, {"status": "inflight", "attempts": 1})
        result = resolve_segment(manager, state["task_id"], 0, "Synthetic billable retry acceptance")
        self.assertEqual(result["attempts_used"], 1)
        self.assertEqual(read_json(path)["status"], "pending")

    def test_task_worker_mock_progress_outputs_cancel_and_timeout_recovery(self):
        with patch.dict(os.environ, {"PYTHONPATH": str(Path(__file__).parent) + os.pathsep + os.getenv("PYTHONPATH", "")}):
            manager = TaskManager(self.workspace, {"mock_cloud": "tts_fixtures:mock_cloud_tts"}, heartbeat=.05)
            task = manager.create([stage("tts", "mock_cloud", self.params, timeout=20, retry_safe=True)], "synthetic-creator", kind="synthetic-tts-test")
            observations = []
            result = manager.run(task["task_id"], observer=lambda s: observations.append(copy.deepcopy(s)))
            self.assertEqual(result["status"], "completed", result["error"])
            self.assertTrue(any("qwen3-tts-vc" in s["message"] for s in observations))
            self.assertTrue(any(s["progress"]["completed"] == 1 for s in observations))
            self.assertTrue(any(Path(p).name == "speech.wav" for p in result["output_files"]))
            params = {**self.params, "synthetic_request_delay": 10}
            for mode in ("cancel", "timeout"):
                spec = stage("tts", "mock_cloud", params, timeout=2.5 if mode == "timeout" else 20, retry_safe=True)
                task = manager.create([spec], "synthetic-creator", kind="synthetic-tts-test")
                def observer(s):
                    if mode == "cancel" and "请求段" in s["message"]:
                        manager.cancel(s["task_id"])
                stopped = manager.run(task["task_id"], observer=observer)
                self.assertEqual(stopped["status"], "cancelled" if mode == "cancel" else "failed")
                self.assertEqual(stopped["output_files"], [])
                recovered = manager.run(task["task_id"], resume=True)
                self.assertEqual(recovered["status"], "failed")
                self.assertEqual(recovered["error"]["type"], "BillableOutcomeUnknown")

    def test_select_video_library_candidate_without_changing_source(self):
        from creator_dna.video import LocalVideoAnalyzer
        from creator_dna.video_assets import VideoAssetWorkflow
        from creator_dna.tts_audio import tool
        folder = self.root / "materials"
        folder.mkdir()
        video = folder / "synthetic.mp4"
        tool(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=12",
              "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)])
        before = digest_file(video)
        analyzer = LocalVideoAnalyzer(max_frames=2, face_detection=False)
        result = VideoAssetWorkflow(self.workspace, analyzer).analyze(folder, "synthetic-creator")
        library = read_json(result["library"])
        candidate = library["sources"][0]["candidates"][0]
        ref = self.assets.add_reference(self.provider.settings, library=result["library"], candidate=candidate["candidate_id"], consent=consent(self.provider))
        self.assertEqual(ref["source"]["candidate_id"], candidate["candidate_id"])
        self.assertEqual(digest_file(video), before)
        self.assertEqual(read_json(result["library"]), library)

    def test_minimax_raw_timestamps_survive_audio_assembly_and_mp3_export(self):
        t = Mock()
        subtitle = [{"text": "全新。", "time_begin": 0, "time_end": 100, "timestamped_words": None}]
        t.request.side_effect = [{"base_resp": {"status_code": 0}, "trace_id": "mock-timestamp",
                                  "data": {"audio": wav_bytes().hex(), "status": 2, "subtitle_file": "https://synthetic.example/subtitle.json"}},
                                __import__("json").dumps(subtitle).encode()]
        provider = make_provider(settings("minimax", format="mp3", subtitles=True), t)
        assets, asset = seed_asset(self.workspace, provider)
        params = {**self.params, "text": "全新。", "config": provider.settings}
        ctx = Context(self.root, 1)
        synthesize_stage(ctx, params, provider)
        manifest = read_json(ctx.output_dir / "speech.json")
        self.assertEqual(manifest["segments"][0]["provider_timestamps"], subtitle)
        self.assertEqual(manifest["metadata"]["codec"], "mp3")
        self.assertTrue((ctx.output_dir / "speech.mp3").is_file())


class CLITests(unittest.TestCase):
    def test_tts_commands_parse_and_capabilities_need_no_account(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "qwen.json"
            write_json(path, settings())
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(main(["tts", "capabilities", "--config", str(path)]), 0)
            self.assertIn("qwen3-tts-vc-2026-01-22", stdout.getvalue())
        args = parser().parse_args(["tts", "synthesize", "--config", "config.json", "--creator", "creator", "--voice", "voice-id", "--script-file", "new.txt"])
        self.assertFalse(args.run)


if __name__ == "__main__":
    unittest.main()
