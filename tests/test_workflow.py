import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from creator_dna.agents import AgentRequest, CodexAdapter, codex_command
from creator_dna.cli import main
from creator_dna.contracts import schema
from creator_dna.errors import CreatorDNAError
from creator_dna.input import scan
from creator_dna.media import MediaProcessor, check_segments, read_transcript
from creator_dna.skills import SkillManager, check_evidence, validate_skill
from creator_dna.storage import Workspace, digest_file, read_json, validate_id
from creator_dna.workflow import Workflow


def skill_markdown(creator):
    return f"---\nname: creator-{creator}\ndescription: 按此创作者口播风格创作新选题文案。\n---\n\n# 创作\n\n先设问，再解释并给行动建议。\n\n参照[风格规则](references/style-guide.md)。\n"


class FixtureAgent:
    """Test contract only; this is not an AI model or real style analysis."""

    def __init__(self):
        self.requests = []
        self.invalid_evidence = False
        self.fail = False

    def run(self, request):
        self.requests.append(request)
        if self.fail:
            raise CreatorDNAError("模拟模型失败")
        if request.task == "generate":
            return {"title": "合成测试稿", "script": "你有没有试过先整理文件，再选择工具？先从一个文件夹开始。",
                    "applied_rule_ids": ["r1"], "notes": ["这是合成测试输出，不表示模型质量。"]}
        data = json.loads(request.prompt.split("输入 JSON:\n", 1)[1])
        evidence = [{"source_id": t["source_id"], "segment_index": 0, "quote": t["segments"][0]["text"]}
                    for t in data["transcripts"]]
        if self.invalid_evidence:
            evidence[0]["quote"] = "输入中不存在的证据"
        profile = {"schema_version": "1.0", "creator_id": data["creator_id"], "sample_count": data["sample_count"],
                   "summary": "合成测试风格", "rules": [{"id": "r1", "dimension": "hook", "instruction": "先提出一个日常问题。",
                   "confidence": "medium", "evidence": evidence}], "limitations": ["合成数据，不能推断真实风格稳定性。"]}
        return {"style_profile": profile, "skill_markdown": skill_markdown(data["creator_id"])}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="CreatorDNA 测试 ")
        self.root = Path(self.temp.name)
        self.input = self.root / "素材"
        self.input.mkdir()
        (self.input / "01.txt").write_text("你有没有遇到过这样的情况？\n先做一件小事。\n", encoding="utf-8")
        (self.input / "02.txt").write_text("你有没有为工具太多感到困扰？\n先整理一个文件夹。\n", encoding="utf-8")
        self.workspace = Workspace(self.root / "数据")
        self.agent = FixtureAgent()
        self.workflow = Workflow(self.workspace, MediaProcessor(), self.agent)

    def tearDown(self):
        self.temp.cleanup()

    def distill(self):
        return self.workflow.distill(self.input, "alice")

    def test_full_workflow_preserves_sources_and_generation_is_independent(self):
        before = {p.name: digest_file(p) for p in self.input.iterdir()}
        result = self.distill()
        self.assertEqual(result["version"], "v001")
        self.assertEqual(before, {p.name: digest_file(p) for p in self.input.iterdir()})
        self.workflow.skills.approve("alice", "v001")
        shutil.rmtree(self.input)
        shutil.rmtree(self.workspace.creator("alice") / "transcripts")
        with patch("creator_dna.workflow.files", side_effect=AssertionError("generation must not read Meta-Skill")):
            generated = self.workflow.generate("alice", "文件整理")
        self.assertIn("合成测试稿", Path(generated["output"]).read_text(encoding="utf-8"))
        prompt = self.agent.requests[-1].prompt
        self.assertNotIn("先整理一个文件夹。", prompt)
        self.assertNotIn("source_sha256", prompt)
        self.assertEqual(read_json(Path(generated["run"]) / "run.json")["status"], "completed")

    def test_draft_requires_explicit_confirmation(self):
        self.distill()
        with self.assertRaisesRegex(CreatorDNAError, "尚无已确认"):
            self.workflow.generate("alice", "新选题")
        with self.assertRaisesRegex(CreatorDNAError, "草稿"):
            self.workflow.generate("alice", "新选题", "v001")

    def test_new_version_does_not_reactivate_draft(self):
        self.distill()
        self.workflow.skills.approve("alice", "v001")
        self.assertEqual(self.distill()["version"], "v002")
        generated = self.workflow.generate("alice", "新选题")
        self.assertEqual(generated["version"], "v001")

    def test_false_evidence_never_publishes(self):
        self.agent.invalid_evidence = True
        with self.assertRaisesRegex(CreatorDNAError, "不是对应片段"):
            self.distill()
        self.assertEqual(self.workflow.skills.versions("alice"), [])
        run = next((self.workspace.creator("alice") / "runs").iterdir())
        self.assertEqual(read_json(run / "run.json")["status"], "failed")
        self.assertTrue((run / "distillation-result.json").exists())
        self.assertFalse((self.workspace.creator("alice") / ".lock").exists())

    def test_evidence_must_address_real_segment(self):
        self.distill()
        profile = read_json(self.workspace.creator("alice") / "skills" / "v001" / "style-profile.json")
        transcripts = read_json(Path(self.agent.requests[0].directory).parent / "transcripts.json")
        profile["rules"][0]["evidence"][0]["segment_index"] = 999
        with self.assertRaisesRegex(CreatorDNAError, "不存在"):
            check_evidence(profile, transcripts, "alice")

    def test_model_failure_retains_cache_and_releases_lock(self):
        self.agent.fail = True
        with self.assertRaisesRegex(CreatorDNAError, "模拟模型失败"):
            self.distill()
        self.agent.fail = False
        result = self.distill()
        manifest = read_json(Path(result["run"]) / "manifest.json")
        self.assertTrue(all(source["cache_reused"] for source in manifest["sources"]))

    def test_interruption_marks_run_and_releases_lock(self):
        with patch.object(self.agent, "run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.distill()
        run = next((self.workspace.creator("alice") / "runs").iterdir())
        self.assertEqual(read_json(run / "run.json")["status"], "interrupted")
        self.assertFalse((self.workspace.creator("alice") / ".lock").exists())

    def test_duplicate_text_is_flagged_and_not_independent_high_confidence(self):
        (self.input / "02.txt").write_bytes((self.input / "01.txt").read_bytes())
        result = self.distill()
        manifest = read_json(Path(result["run"]) / "manifest.json")
        self.assertTrue(any("重复" in warning for warning in manifest["warnings"]))
        profile = read_json(Path(result["skill"]) / "style-profile.json")
        profile["rules"][0]["confidence"] = "high"
        transcripts = read_json(Path(result["run"]) / "transcripts.json")
        with self.assertRaisesRegex(CreatorDNAError, "内容不同"):
            check_evidence(profile, transcripts, "alice")

    def test_cache_changes_when_source_or_sidecar_changes(self):
        media = self.input / "03.mp4"
        media.write_bytes(b"synthetic media used only with a sidecar")
        sidecar = self.input / "03.txt"
        sidecar.write_text("第一版", encoding="utf-8")
        first = self.workflow.prepare(self.input, "alice")
        self.assertEqual(first["sample_count"], 3)
        second = self.workflow.prepare(self.input, "alice")
        self.assertTrue(all(s["cache_reused"] for s in read_json(Path(second["manifest"]))["sources"]))
        sidecar.write_text("第二版", encoding="utf-8")
        third = self.workflow.prepare(self.input, "alice")
        manifest = read_json(Path(third["manifest"]))
        item = next(s for s in manifest["sources"] if s["relative_path"] == "03.mp4")
        self.assertFalse(item["cache_reused"])
        data = read_json(self.workspace.creator("alice") / item["transcript"])
        self.assertEqual(data["segments"][0]["text"], "第二版")

    def test_skill_tampering_blocks_generation(self):
        self.distill()
        folder = self.workflow.skills.approve("alice", "v001")
        with (folder / "SKILL.md").open("a", encoding="utf-8") as f:
            f.write("意外修改")
        with self.assertRaisesRegex(CreatorDNAError, "被修改"):
            self.workflow.generate("alice", "新选题")
        self.assertEqual(len(self.agent.requests), 1)

    def test_revision_is_new_draft_and_exports_only_portable_files(self):
        self.distill()
        self.workflow.skills.approve("alice", "v001")
        edited = self.root / "修改稿.md"
        edited.write_text(skill_markdown("alice") + "\n用短句讲解。\n", encoding="utf-8")
        revised = self.workflow.skills.revise("alice", "v001", edited)
        self.assertEqual(revised.name, "v002")
        self.assertEqual(self.workflow.skills.load("alice")[0].name, "v001")
        self.workflow.skills.approve("alice", "v002")
        (revised / "references" / "untracked-private.txt").write_text("不得导出不在文件清单里的内容", encoding="utf-8")
        exported = self.workflow.skills.export("alice", "v002", self.root / "导出 Skill")
        self.assertEqual({p.relative_to(exported).as_posix() for p in exported.rglob("*") if p.is_file()},
                         {"SKILL.md", "references/style-guide.md"})
        with self.assertRaisesRegex(CreatorDNAError, "已存在"):
            self.workflow.skills.export("alice", "v002", exported)

    def test_multi_creator_data_is_isolated(self):
        self.distill()
        bob = self.workflow.distill(self.input, "bob")
        self.assertEqual(bob["version"], "v001")
        self.workflow.skills.approve("alice", "v001")
        with self.assertRaises(CreatorDNAError):
            self.workflow.skills.load("bob")

    def test_oversize_input_is_not_silently_truncated(self):
        with self.assertRaisesRegex(CreatorDNAError, "不会静默截断"):
            self.workflow.distill(self.input, "alice", max_input_chars=10)
        self.assertFalse(self.agent.requests)

    def test_missing_transcript_fails_without_publishing(self):
        (self.input / "03.mp4").write_bytes(b"test without sidecar")
        with self.assertRaisesRegex(CreatorDNAError, "没有同名转写稿"):
            self.workflow.prepare(self.input, "alice")
        self.assertTrue(list((self.workspace.creator("alice") / "transcripts").rglob("*.json")))

    def test_workspace_cannot_overlap_input(self):
        for root in [self.input, self.input / "outputs", self.root]:
            with self.assertRaisesRegex(CreatorDNAError, "互不包含"):
                Workflow(Workspace(root), MediaProcessor(), self.agent).prepare(self.input, "alice")
        self.assertEqual({p.name for p in self.input.iterdir()}, {"01.txt", "02.txt"})

    def test_lock_prevents_same_creator_concurrent_tasks(self):
        with self.workspace.lock("alice"):
            with self.assertRaisesRegex(CreatorDNAError, "已有任务"):
                self.workflow.prepare(self.input, "alice")
        self.workflow.prepare(self.input, "alice")

    def test_ambiguous_same_stem_fails_and_recursive_is_opt_in(self):
        nested = self.input / "sub"
        nested.mkdir()
        (nested / "03.txt").write_text("子文件夹", encoding="utf-8")
        self.assertEqual(len(scan(self.input, self.workspace.root)[1]), 2)
        self.assertEqual(len(scan(self.input, self.workspace.root, True)[1]), 3)
        (self.input / "01.srt").write_text("ignored", encoding="utf-8")
        with self.assertRaisesRegex(CreatorDNAError, "歧义"):
            scan(self.input, self.workspace.root)

    def test_id_and_version_path_traversal_are_rejected(self):
        for value in ["../outside", "CON", "con", "com1", "a/b", "a\\b", "", "x" * 49]:
            with self.assertRaises(CreatorDNAError):
                validate_id(value)
        self.distill()
        with self.assertRaises(CreatorDNAError):
            self.workflow.skills.load("alice", "../../outside", False)

    def test_external_skill_reference_is_rejected(self):
        for target in ["../secret.md", "https://example.org/guide", "D:/Videos/01.txt"]:
            with self.assertRaises(CreatorDNAError):
                validate_skill(skill_markdown("alice").replace("references/style-guide.md", target), "alice")

    def test_schema_definitions_are_valid_and_consistent(self):
        for name in ["distillation", "generation", "transcript", "style-profile"]:
            Draft202012Validator.check_schema(schema(name))
        expected = copy.deepcopy(schema("style-profile"))
        expected.pop("$schema")
        self.assertEqual(schema("distillation")["properties"]["style_profile"], expected)

    def test_srt_json_and_text_timestamp_validation(self):
        srt = self.root / "captions.srt"
        srt.write_text("1\n00:00:01,200 --> 00:00:02,500\n你好\n世界\n\n2\n00:00:03,000 --> 00:00:04,000\n结束\n", encoding="utf-8-sig")
        segments, language = read_transcript(srt)
        self.assertEqual(segments[0], {"start": 1.2, "end": 2.5, "text": "你好\n世界"})
        self.assertIsNone(language)
        for start, end in [(1, 1), (-1, 1), (None, 1), (True, 2), (float("nan"), 2)]:
            with self.assertRaises(CreatorDNAError):
                check_segments([{"start": start, "end": end, "text": "不合法"}])
        segments, _ = read_transcript(self.input / "01.txt")
        self.assertIsNone(segments[0]["start"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not available")
    def test_real_ffmpeg_extracts_audio_and_transcriber_interface(self):
        source = self.input / "audio.wav"
        subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=0.2", "-y", str(source)], check=True)
        original = digest_file(source)

        class StubTranscriber:
            fingerprint = "test-stub-v1"

            def transcribe(inner, audio):
                self.assertGreater(audio.stat().st_size, 44)
                return [{"start": 0.0, "end": 0.2, "text": "合成接口测试"}], "zh"

        workflow = Workflow(self.workspace, MediaProcessor(StubTranscriber()), None)
        result = workflow.prepare(self.input, "alice")
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(digest_file(source), original)

    def test_codex_adapter_uses_stdin_and_validates_final_file(self):
        directory = self.root / "推理请求 空格"
        response = {"title": "标题", "script": "这是正文", "applied_rule_ids": ["r1"], "notes": []}
        request = AgentRequest("generate", "中文提示 $() ` & ; 不经过 shell", "generation", schema("generation"), directory)

        def invoke(args, **kwargs):
            self.assertNotIn("shell", kwargs)
            self.assertIn("read-only", args)
            Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(response), encoding="utf-8")
            class Process:
                returncode = 0

                def communicate(inner, input, timeout):
                    self.assertEqual(input, request.prompt)
                    self.assertGreater(timeout, 0)
            return Process()

        with patch("creator_dna.agents.codex_command", return_value=["codex.exe"]), patch("creator_dna.agents.subprocess.Popen", side_effect=invoke):
            self.assertEqual(CodexAdapter().run(request), response)
        self.assertEqual((directory / "prompt.txt").read_text(encoding="utf-8"), request.prompt)

    def test_codex_timeout_stops_invocation_and_keeps_request(self):
        request = AgentRequest("generate", "保留请求", "generation", schema("generation"), self.root / "timeout")
        from unittest.mock import Mock
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired("codex", 1)
        with patch("creator_dna.agents.codex_command", return_value=["codex.exe"]), patch("creator_dna.agents.subprocess.Popen", return_value=process), patch.object(CodexAdapter, "stop_process") as stop:
            with self.assertRaisesRegex(CreatorDNAError, "超过 1 秒"):
                CodexAdapter(timeout=1).run(request)
            stop.assert_called_once_with(process)
        self.assertTrue((request.directory / "prompt.txt").exists())

    def test_cli_reports_actionable_input_error(self):
        with patch("sys.stderr") as stderr:
            code = main(["prepare", "--input", str(self.root / "missing"), "--workspace", str(self.workspace.root), "--asr", "none"])
        self.assertEqual(code, 1)
        self.assertIn("不存在", "".join(str(call) for call in stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
