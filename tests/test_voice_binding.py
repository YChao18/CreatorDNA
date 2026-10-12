"""Folder routing and credential isolation; synthetic voice IDs, no paid calls."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from creator_dna.cli import main, parser
from creator_dna.configuration import load_environment
from creator_dna.errors import CreatorDNAError
from creator_dna.storage import Workspace, digest_file, read_json, write_json
from creator_dna.tts import TTSError, make_provider
from creator_dna.tts_workflow import create_synthesis_task
from creator_dna.voice_assets import VoiceAssets


def provider_and_asset(workspace, creator, voice_id):
    transport = Mock()
    transport.request.return_value = {"base_resp": {"status_code": 0}, "voice_cloning": [{"voice_id": voice_id}]}
    provider = make_provider({"provider": "minimax", "model": "speech-2.8-hd"}, transport)
    assets = VoiceAssets(workspace, creator)
    consent = {"provider": provider.name, "scope": provider.scope, "authorized": True,
               "cloud_processing": True, "confirmation": "SYNTHETIC ROUTING TEST ONLY", "confirmed_at": "synthetic"}
    return provider, assets, assets.import_voice(provider, voice_id, consent)


class FolderVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root / "workspace")
        self.folder = self.root / "materials" / "first"
        self.folder.mkdir(parents=True)
        (self.folder / "reference.txt").write_text("SYNTHETIC SOURCE", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_two_folders_route_separately_and_queued_tasks_pin_the_voice(self):
        second = self.root / "materials" / "second"
        second.mkdir()
        p, a, v = provider_and_asset(self.ws, "first", "synthetic_first")
        p2, a2, v2 = provider_and_asset(self.ws, "second", "synthetic_second")
        before = {f.name: digest_file(f) for f in self.folder.iterdir()}
        a.bind(self.folder, v["asset_id"], p)
        a2.bind(second, v2["asset_id"], p2)
        with patch("creator_dna.tts_workflow.make_provider", return_value=p), patch("creator_dna.tts_workflow.dependencies"):
            queued = create_synthesis_task(self.ws, "first", None, p.settings, "合成路由测试")
        spec = read_json(self.ws.root / "tasks" / queued["task_id"] / "task.json")["stages"][0]
        self.assertEqual(spec["params"]["voice_id_snapshot"], "synthetic_first")
        _, _, replacement = provider_and_asset(self.ws, "first", "synthetic_replacement")
        a.bind(self.folder, replacement["asset_id"], p)
        self.assertEqual(a.default_voice(p)["voice_id"], "synthetic_replacement")
        self.assertEqual(a2.default_voice(p2)["voice_id"], "synthetic_second")
        self.assertEqual(spec["params"]["voice_asset"], v["asset_id"])
        self.assertEqual(before, {f.name: digest_file(f) for f in self.folder.iterdir()})

    def test_missing_binding_and_wrong_account_never_fall_back(self):
        p, a, v = provider_and_asset(self.ws, "first", "synthetic_first")
        with self.assertRaisesRegex(CreatorDNAError, "尚未绑定"):
            a.default_voice(p)
        a.bind(self.folder, v["asset_id"], p)
        other = make_provider({**p.settings, "api_key_env": "ANOTHER_ACCOUNT"}, Mock())
        with self.assertRaisesRegex(TTSError, "ProviderOrAccountMismatch"):
            a.default_voice(other)
        other_model = make_provider({**p.settings, "model": "speech-2.8-turbo"}, Mock())
        with self.assertRaisesRegex(TTSError, "VoiceModelMismatch"):
            a.default_voice(other_model)

    def test_duplicate_folder_manifest_change_and_source_overlap_are_rejected(self):
        p, a, v = provider_and_asset(self.ws, "first", "synthetic_first")
        a.bind(self.folder, v["asset_id"], p)
        p2, a2, v2 = provider_and_asset(self.ws, "second", "synthetic_second")
        with self.assertRaisesRegex(CreatorDNAError, "已绑定其他"):
            a2.bind(self.folder, v2["asset_id"], p2)
        write_json(self.ws.creator("first") / "manifest.json", {"input_folder": str(self.root / "different-folder")})
        with self.assertRaisesRegex(CreatorDNAError, "manifest 不一致"):
            a.default_voice(p)
        isolated = VoiceAssets(Workspace(self.folder / "nested-output"), "first")
        with self.assertRaisesRegex(CreatorDNAError, "互不包含"):
            isolated.check_binding_folder(self.folder)
        self.assertFalse((self.folder / "nested-output").exists())

    def test_cli_bind_imports_real_id_contract_then_synthesize_can_omit_voice(self):
        p, _, _ = provider_and_asset(self.ws, "first", "synthetic_cli")
        cfg = self.root / "tts.json"
        write_json(cfg, p.settings)
        import io
        with patch("creator_dna.tts_cli.make_provider", return_value=p), patch("sys.stdout", io.StringIO()):
            result = main(["tts", "bind", "--workspace", str(self.ws.root), "--creator", "first",
                           "--input", str(self.folder), "--config", str(cfg), "--voice-id", "synthetic_cli",
                           "--authorize", "--cloud-processing", "--confirmation", "SYNTHETIC ROUTING TEST"])
        self.assertEqual(result, 0)
        self.assertEqual(VoiceAssets(self.ws, "first").binding()["voice_id"], "synthetic_cli")
        args = parser().parse_args(["tts", "synthesize", "--creator", "first", "--config", str(cfg), "--script-file", "script.txt"])
        self.assertIsNone(args.voice)


class EnvironmentTests(unittest.TestCase):
    def test_nested_project_dotenv_loads_without_overriding_process_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text("CREATORDNA_TEST_NEW=synthetic_file\nCREATORDNA_TEST_EXISTING=synthetic_file\n", encoding="utf-8")
            (root / "nested").mkdir()
            cwd = Path.cwd()
            with patch.dict(os.environ, {"CREATORDNA_TEST_EXISTING": "synthetic_process"}, clear=True):
                try:
                    os.chdir(root / "nested")
                    load_environment()
                    self.assertEqual(os.environ["CREATORDNA_TEST_NEW"], "synthetic_file")
                    self.assertEqual(os.environ["CREATORDNA_TEST_EXISTING"], "synthetic_process")
                finally:
                    os.chdir(cwd)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires local FFmpeg fixture")
class VideoBindingTests(unittest.TestCase):
    def test_new_video_task_pins_folder_default_without_cloud_synthesis(self):
        from render_fixtures import fixture
        from creator_dna.video_workflow import create_video_task
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ws = Workspace(root / "workspace")
            f = fixture(root, ws, duration=6)
            p, a, v = provider_and_asset(ws, "synthetic-render", "synthetic_video")
            a.bind(f["video"].parent, v["asset_id"], p)
            cfg = read_json(f["workflow"])
            cfg.pop("speech")
            cfg.pop("timing")
            tts = root / "tts.json"
            write_json(tts, p.settings)
            cfg.update(tts=str(tts), lipsync="lip.json", portrait={"image": str(f["assets"].parent / "synthetic-source" / "blue.png")},
                       portrait_authorization={"creator_id": "synthetic-render", "authorized": True,
                           "processing": "local", "scope": "selected_portraits_with_workflow_speech",
                           "source_sha256": [digest_file(f["video"])], "confirmation": "SYNTHETIC ONLY", "confirmed_at": "synthetic"})
            write_json(root / "lip.json", {"repository": str(root / "missing-model")})
            write_json(f["workflow"], cfg)
            task = create_video_task(ws, "synthetic-render", f["workflow"], script=f["text"])
            definition = read_json(ws.root / "tasks" / task["task_id"] / "task.json")
            resolved = definition["stages"][1]["params"]["config"]
            self.assertEqual(resolved["voice_asset"], v["asset_id"])
            self.assertEqual(resolved["voice_id_snapshot"], "synthetic_video")
            self.assertEqual(task["status"], "pending")


if __name__ == "__main__":
    unittest.main()
