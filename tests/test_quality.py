"""Synthetic engineering fixtures only; no claim of real style quality."""
import copy
import json
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from creator_dna.cli import main, parser
from creator_dna.contracts import schema, validate
from creator_dna.errors import CreatorDNAError
from creator_dna.evaluation import DEFAULT_CONFIG, checked_config, fingerprint
from creator_dna.media import MediaProcessor
from creator_dna.quality import QualityWorkflow
from creator_dna.storage import Workspace, digest_file, read_json
from creator_dna.workflow import Workflow
from test_workflow import FixtureAgent, skill_markdown


def config():
    data = copy.deepcopy(DEFAULT_CONFIG)
    data["dimensions"] = [data["dimensions"][0], data["dimensions"][-1]]
    return data


def dataset(purpose="development", topic="合成开发选题"):
    return {"schema_version": "2.0", "id": "synthetic-" + purpose, "purpose": purpose,
            "cases": [{"id": "case-1", "topic": topic, "category": "regular", "brief": "",
                       "human_reference": "合成人工参考稿，仅用于测试数据隔离。"}]}


def feedback(status=None):
    return {"schema_version": "2.0", "author": "合成测试署名", "comment": "合成测试反馈，开头可以更直接。",
            "status_override": status, "dimension_overrides": []}


class QualityFixture(FixtureAgent):
    """A controllable contract fixture, NOT real Agent reasoning."""
    def __init__(self):
        super().__init__()
        self.scores = []
        self.default_score = 4
        self.invalid = None
        self.fail_generation = 0
        self.evaluation_calls = 0

    def run(self, request):
        if request.task not in {"evaluate", "revise-script", "optimize-skill"}:
            if request.task == "generate" and self.fail_generation:
                self.requests.append(request)
                self.fail_generation -= 1
                raise CreatorDNAError("合成子任务失败")
            return super().run(request)
        self.requests.append(request)
        data = json.loads(request.prompt.split("输入 JSON:\n", 1)[1])
        if request.task == "revise-script":
            return {"title": "合成修订稿", "script": "你有没有考虑先整理？先做一件小事，然后选择工具。",
                    "applied_rule_ids": ["r1"], "notes": ["合成工程测试"]}
        if request.task == "optimize-skill":
            return {"skill_markdown": skill_markdown("alice") + "\n设问应紧扣选题。\n", "change_notes": "合成优化说明",
                    "diagnosis": "合成重复偏差", "target_rule_ids": ["r1"]}
        self.evaluation_calls += 1
        if self.invalid == "agent":
            raise CreatorDNAError("合成评估失败")
        score = self.scores.pop(0) if self.scores else self.default_score
        script_e = {"kind": "script", "rule_id": None, "quote": data["script"][:6]}
        rule = data["style_profile"]["rules"][0]
        rule_e = {"kind": "rule", "rule_id": rule["id"], "quote": rule["instruction"]}
        dimensions, issues = [], []
        for d in data["config"]["dimensions"]:
            evidence = [script_e, rule_e] if d["group"] == "style" else [script_e]
            dimensions.append({"id": d["id"], "group": d["group"], "score": score,
                               "reason": "合成测试理由", "evidence": copy.deepcopy(evidence)})
            if score is not None and score < d["minimum"]:
                issues.append({"dimension_id": d["id"], "description": "合成偏差", "suggestion": "合成修订建议",
                               "evidence": copy.deepcopy(evidence)})
        result = {"schema_version": "2.0", "dimensions": dimensions,
                  "originality": {"risk": "low", "reason": "合成判断", "evidence": []},
                  "issues": issues, "limitations": ["合成测试不能证明真实模型风格质量。"]}
        if self.invalid == "quote":
            result["dimensions"][0]["evidence"][0]["quote"] = "不存在于正文的引文"
        if self.invalid == "rule":
            result["dimensions"][0]["evidence"][1]["rule_id"] = "missing"
        if self.invalid == "missing":
            result.pop("limitations")
        if self.invalid == "duplicate":
            result["dimensions"].append(copy.deepcopy(result["dimensions"][0]))
        if self.invalid == "group":
            result["dimensions"][0]["group"] = "quality"
        if self.invalid == "risk":
            result["originality"]["risk"] = "high"
        return result


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="CreatorDNA V2 测试 ")
        self.root = Path(self.temp.name)
        self.source = self.root / "sources"
        self.source.mkdir()
        (self.source / "01.txt").write_text("你有没有遇到工具太多？\n先做一件小事。", encoding="utf-8")
        self.workspace = Workspace(self.root / "data")
        self.agent = QualityFixture()
        self.v1 = Workflow(self.workspace, MediaProcessor(), self.agent)
        self.v1.distill(self.source, "alice")
        self.v1.skills.approve("alice", "v001")
        self.quality = QualityWorkflow(self.workspace, self.agent)

    def tearDown(self):
        self.temp.cleanup()

    def cycle(self, **kwargs):
        return self.quality.cycle("alice", "合成新选题", config=config(), **kwargs)

    def report(self, result):
        return read_json(Path(result["run"]) / "evaluation-report.json")

    def candidate(self, gated=True):
        edit = self.root / "edited.md"
        edit.write_text(skill_markdown("alice") + "\n短句。\n", encoding="utf-8")
        return self.v1.skills.revise("alice", "v001", edit, {"quality_gate_required": gated,
            "training_topics": ["合成开发选题"], "change_notes": "合成候选"}).name

    def batch(self, version="v001", purpose="acceptance", topic="合成验收选题", repeats=1, **kwargs):
        return self.quality.batch("alice", dataset(purpose, topic), version, repeats, config(), **kwargs)

    def test_evaluation_contract_and_source_preservation(self):
        before = digest_file(self.source / "01.txt")
        result = self.quality.evaluate("alice", "你有没有考虑整理工具？", config=config())
        report = self.report(result)
        self.assertEqual(result["status"], "passed")
        validate(report, "evaluation-report")
        self.assertEqual(before, digest_file(self.source / "01.txt"))
        markdown = (Path(result["run"]) / "evaluation-report.md").read_text(encoding="utf-8")
        self.assertIn("不是客观风格相似度", markdown)
        self.assertIn("内容质量", markdown)
        self.assertIn(result["run_id"], read_json(self.workspace.creator("alice") / "skills/v001/metadata.json")["test_run_ids"])

    def test_bad_agent_results_are_unable_and_retained(self):
        for invalid in ["quote", "rule", "missing", "duplicate", "group", "risk", "agent"]:
            with self.subTest(invalid=invalid):
                self.agent.invalid = invalid
                result = self.quality.evaluate("alice", "你有没有考虑整理工具？", config=config())
                self.assertEqual(result["status"], "unable")
                self.assertIsNone(self.report(result)["assessment"])
                if invalid != "agent":
                    self.assertTrue((Path(result["run"]) / "evaluate/raw-result.json").exists())

    def test_missing_skill_returns_unable_report(self):
        result = self.quality.evaluate("bob", "合成稿件", config=config())
        self.assertEqual(result["status"], "unable")
        self.assertTrue(self.report(result)["reasons"])

    def test_null_scores_stop_without_revising(self):
        self.agent.default_score = None
        result = self.cycle(max_revisions=4)
        self.assertEqual(result["status"], "unable")
        self.assertEqual(self.agent.evaluation_calls, 1)
        self.assertEqual(len(read_json(Path(result["history"]))["attempts"]), 1)

    def test_failed_report_gives_evidence_and_advice(self):
        self.agent.default_score = 2
        result = self.cycle(max_revisions=0)
        report = self.report(result)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(report["assessment"]["issues"][0]["suggestion"])
        self.assertTrue(read_json(Path(result["history"]))["human_advice"])

    def test_bounded_revisions_and_pass_early(self):
        self.agent.scores = [1, 2, 4]
        result = self.cycle(max_revisions=5)
        history = read_json(Path(result["history"]))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(history["attempts"]), 3)
        self.assertEqual([a["status"] for a in history["attempts"]], ["failed", "failed", "passed"])
        self.assertNotEqual((Path(result["run"]) / history["attempts"][0]["script"]).read_text(encoding="utf-8"),
                            (Path(result["run"]) / history["attempts"][1]["script"]).read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.v1.skills.versions("alice")], ["v001"])

    def test_revision_limit_failure_and_agent_failure_preserve_last_script(self):
        self.agent.default_score = 1
        result = self.cycle(max_revisions=1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(read_json(Path(result["history"]))["attempts"]), 2)
        original = self.agent.run
        def fail_revision(request):
            if request.task == "revise-script":
                raise CreatorDNAError("合成修订失败")
            return original(request)
        with patch.object(self.agent, "run", side_effect=fail_revision):
            result = self.cycle(max_revisions=2)
        self.assertEqual(result["status"], "unable")
        self.assertTrue(Path(result["output"]).exists())

    def test_manual_stop_keeps_history_and_unlocks(self):
        with patch.object(self.agent, "run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.cycle()
        run = next(p for p in (self.workspace.creator("alice") / "runs").iterdir() if p.name.startswith("quality-"))
        self.assertEqual(read_json(run / "revision-history.json")["status"], "interrupted")
        self.assertFalse((self.workspace.creator("alice") / ".lock").exists())

    def test_feedback_is_append_only_override_and_applies_only_to_same_script(self):
        result = self.cycle(max_revisions=0)
        original = digest_file(Path(result["run"]) / "evaluation-report.json")
        fb = self.quality.feedback("alice", result["run_id"], feedback("failed"))
        self.assertEqual(read_json(Path(fb["human_view"]))["status"], "failed")
        self.assertEqual(original, digest_file(Path(result["run"]) / "evaluation-report.json"))
        script = Path(result["output"]).read_text(encoding="utf-8")
        revised = self.cycle(script=script, feedback_id=fb["feedback_id"], max_revisions=1)
        self.assertEqual(len(read_json(Path(revised["history"]))["attempts"]), 2)
        bad = self.cycle(script="另一篇稿件", feedback_id=fb["feedback_id"])
        self.assertEqual(bad["status"], "unable")
        second = self.quality.feedback("alice", result["run_id"], feedback())
        self.assertNotEqual(fb["feedback_id"], second["feedback_id"])

    def test_batch_continues_on_failure_and_hides_manual_sample(self):
        self.agent.fail_generation = 1
        result = self.batch(repeats=3)
        report = read_json(Path(result["report"]))
        self.assertEqual(report["summary"]["unable"], 1)
        self.assertEqual(report["summary"]["passed"], 2)
        for request in self.agent.requests[1:]:
            self.assertNotIn("合成人工参考稿", request.prompt)

    def test_batch_interrupt_preserves_partial_results(self):
        original = self.agent.run
        def interrupt(request):
            if request.task == "generate" and self.agent.evaluation_calls == 1:
                raise KeyboardInterrupt
            return original(request)
        with patch.object(self.agent, "run", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.batch(repeats=3)
        run = next(p for p in (self.workspace.creator("alice") / "runs").iterdir() if p.name.startswith("batch-"))
        report = read_json(run / "batch-report.json")
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(report["summary"]["total"], 1)

    def test_version_compare_gate_publish_diff_and_rollback(self):
        version = self.candidate()
        baseline = self.batch()
        candidate = self.batch(version)
        compared = self.quality.compare("alice", baseline["run_id"], candidate["run_id"])
        self.assertTrue(compared["eligible"])
        with self.assertRaisesRegex(CreatorDNAError, "人工确认"):
            self.v1.skills.approve("alice", version)
        self.v1.skills.approve("alice", version, compared["run_id"], "合成测试人工确认")
        self.assertEqual(self.v1.skills.load("alice")[0].name, version)
        self.assertIn("短句", self.v1.skills.diff("alice", "v001", version))
        self.v1.skills.rollback("alice", "v001")
        self.assertEqual(self.v1.skills.load("alice")[0].name, "v001")
        self.assertEqual(len(self.v1.skills.versions("alice")), 2)

    def test_gate_rejects_overfit_development_and_degradation(self):
        version = self.candidate()
        for purpose, topic in [("development", "未见过的选题"), ("acceptance", "合成开发选题")]:
            a = self.batch(purpose=purpose, topic=topic)
            b = self.batch(version, purpose, topic)
            comparison = self.quality.compare("alice", a["run_id"], b["run_id"])
            with self.assertRaises(CreatorDNAError):
                self.v1.skills.approve("alice", version, comparison["run_id"], "合成确认")
        a = self.batch()
        self.agent.default_score = 3
        b = self.batch(version)
        comparison = self.quality.compare("alice", a["run_id"], b["run_id"])
        self.assertFalse(comparison["eligible"])
        with self.assertRaises(CreatorDNAError):
            self.v1.skills.approve("alice", version, comparison["run_id"], "合成确认")

    def test_comparison_rejects_mismatched_set_and_tampered_summary(self):
        a = self.batch()
        b = self.batch(topic="不同选题")
        with self.assertRaises(CreatorDNAError):
            self.quality.compare("alice", a["run_id"], b["run_id"])
        report = read_json(Path(a["report"]))
        report["summary"]["passed"] = 900
        Path(a["report"]).write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(CreatorDNAError):
            self.quality.compare("alice", a["run_id"], a["run_id"])

    def test_optimization_requires_repeated_failures_feedback_and_no_acceptance(self):
        self.agent.default_score = 2
        dev = self.batch(purpose="development", topic="合成开发选题", repeats=2)
        entries = read_json(Path(dev["report"]))["entries"]
        fb = self.quality.feedback("alice", entries[0]["run_id"], feedback())
        result = self.quality.optimize("alice", "v001", [dev["run_id"]], [fb["feedback_id"]])
        self.assertEqual(result["version"], "v002")
        meta = read_json(Path(result["skill"]) / "metadata.json")
        self.assertTrue(meta["quality_gate_required"])
        self.assertEqual(meta["parent_version"], "v001")
        self.assertEqual(self.v1.skills.load("alice")[0].name, "v001")
        acceptance = self.batch(purpose="acceptance", repeats=2)
        with self.assertRaisesRegex(CreatorDNAError, "acceptance"):
            self.quality.optimize("alice", "v001", [acceptance["run_id"]], [fb["feedback_id"]])
        single = self.batch(purpose="development", repeats=1)
        single_entry = read_json(Path(single["report"]))["entries"][0]
        f = self.quality.feedback("alice", single_entry["run_id"], feedback())
        with self.assertRaisesRegex(CreatorDNAError, "至少两次"):
            self.quality.optimize("alice", "v001", [single["run_id"], single["run_id"]], [f["feedback_id"]])

    def test_schema_config_and_portable_skill_resources(self):
        for name in ["evaluation", "evaluation-config", "evaluation-report", "feedback", "feedback-record", "dataset", "skill-candidate", "batch-report", "revision-history", "skill-metadata"]:
            Draft202012Validator.check_schema(schema(name))
        for package in ["creator_dna.evaluation_skill", "creator_dna.optimization_skill"]:
            self.assertTrue(files(package).joinpath("SKILL.md").read_text(encoding="utf-8").startswith("---"))
        for package, names in [("evaluation_skill", ["evaluation", "evaluation-config"]),
                               ("optimization_skill", ["generation", "skill-candidate"])]:
            for name in names:
                resource = files("creator_dna." + package).joinpath("references", name + ".schema.json")
                self.assertEqual(json.loads(resource.read_text(encoding="utf-8")), schema(name))
        bad = config()
        bad["dimensions"].append(copy.deepcopy(bad["dimensions"][0]))
        with self.assertRaises(CreatorDNAError):
            checked_config(bad)
        bad = config()
        bad["dimensions"][0]["minimum"] = float("nan")
        with self.assertRaises(CreatorDNAError):
            checked_config(bad)
        for maximum in [-1, True]:
            with self.assertRaises(CreatorDNAError):
                self.cycle(max_revisions=maximum)
        with self.assertRaises(CreatorDNAError):
            self.quality.feedback("alice", "../outside", feedback())

    def test_cli_v1_and_v2_entries_and_config_conflict(self):
        for args in [["quality", "--creator", "alice", "--topic", "合成"],
                     ["evaluate", "--creator", "alice", "--script-file", "s.md"],
                     ["batch", "--creator", "alice", "--dataset", "d.json"],
                     ["skills", "rollback", "--creator", "alice", "--version", "v001"],
                     ["generate", "--creator", "alice", "--topic", "合成"]]:
            parser().parse_args(args)
        destination = self.root / "config.json"
        with patch("sys.stdout"), patch("sys.stderr"):
            self.assertEqual(main(["evaluation-config", "--destination", str(destination)]), 0)
            self.assertEqual(main(["evaluation-config", "--destination", str(destination)]), 1)

    def test_legacy_metadata_works_and_malformed_metadata_fails(self):
        folder = self.workspace.creator("alice") / "skills/v001"
        meta = read_json(folder / "metadata.json")
        legacy = {k: meta[k] for k in ["schema_version", "creator_id", "version", "status", "created_at", "run_id", "files"]}
        legacy["schema_version"] = "1.0"
        (folder / "metadata.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(self.v1.skills.load("alice")[0].name, "v001")
        legacy.pop("files")
        (folder / "metadata.json").write_text(json.dumps(legacy), encoding="utf-8")
        result = self.quality.evaluate("alice", "合成稿件", config=config())
        self.assertEqual(result["status"], "unable")
        self.assertIn("skill-metadata", self.report(result)["reasons"][0])

    def test_candidate_descendant_keeps_gate_and_resets_test_links(self):
        candidate = self.candidate()
        edit = self.root / "edited-again.md"
        edit.write_text(skill_markdown("alice") + "\n明确短句功能。\n", encoding="utf-8")
        self.batch(candidate)
        descendant = self.v1.skills.revise("alice", candidate, edit)
        meta = read_json(descendant / "metadata.json")
        self.assertTrue(meta["quality_gate_required"])
        self.assertEqual(meta["test_run_ids"], [])
        self.assertEqual(meta["training_topics"], ["合成开发选题"])
        with self.assertRaises(CreatorDNAError):
            self.v1.skills.approve("alice", descendant.name)
        baseline = self.batch()
        revised = self.batch(descendant.name)
        comparison = self.quality.compare("alice", baseline["run_id"], revised["run_id"])
        self.v1.skills.approve("alice", descendant.name, comparison["run_id"], "合成审核")
        self.assertEqual(self.v1.skills.load("alice")[0].name, descendant.name)

    def test_compare_rejects_edited_child_script_and_unpinned_model_publish(self):
        candidate = self.candidate()
        self.agent.model = None
        a = self.batch()
        b = self.batch(candidate)
        comparison = self.quality.compare("alice", a["run_id"], b["run_id"])
        with self.assertRaisesRegex(CreatorDNAError, "固定模型"):
            self.v1.skills.approve("alice", candidate, comparison["run_id"], "合成确认")
        child = read_json(Path(b["report"]))["entries"][0]["run_id"]
        (self.quality.run_path("alice", child) / "script.md").write_text("改过的正文", encoding="utf-8")
        with self.assertRaisesRegex(CreatorDNAError, "实际评估"):
            self.quality.compare("alice", a["run_id"], b["run_id"])

    def test_runtime_failure_is_isolated_and_oversize_request_never_calls_agent(self):
        original = self.agent.run
        fired = False
        def runtime_failure(request):
            nonlocal fired
            if request.task == "generate" and not fired:
                fired = True
                raise RuntimeError("自定义 Provider 的执行错误")
            return original(request)
        with patch.object(self.agent, "run", side_effect=runtime_failure):
            result = self.batch(repeats=2)
        self.assertEqual(result["summary"]["unable"], 1)
        self.assertEqual(result["summary"]["passed"], 1)
        calls = len(self.agent.requests)
        result = QualityWorkflow(self.workspace, self.agent, max_input_chars=10).evaluate("alice", "合成稿件", config=config())
        self.assertEqual(result["status"], "unable")
        self.assertEqual(len(self.agent.requests), calls)

    def test_originality_has_independent_failure_status(self):
        original = self.agent.run
        def copied(request):
            result = original(request)
            if request.task == "evaluate":
                data = json.loads(request.prompt.split("输入 JSON:\n", 1)[1])
                rule = data["style_profile"]["rules"][0]
                result["originality"] = {"risk": "high", "reason": "合成复制风险",
                    "evidence": [{"kind": "script", "rule_id": None, "quote": data["script"]},
                                 {"kind": "reference", "rule_id": rule["id"], "quote": rule["evidence"][0]["quote"]}]}
            return result
        with patch.object(self.agent, "run", side_effect=copied):
            result = self.quality.evaluate("alice", "你有没有遇到工具太多？", config=config())
        self.assertEqual(result["status"], "failed")
        self.assertTrue(all(d["score"] >= 3 for d in self.report(result)["assessment"]["dimensions"]))
        self.assertIn("原创性", self.report(result)["reasons"][0])

    def test_human_override_blocks_self_reinforcing_skill_optimization(self):
        self.agent.default_score = 2
        dev = self.batch(purpose="development", repeats=2)
        entry = read_json(Path(dev["report"]))["entries"][0]
        fb = self.quality.feedback("alice", entry["run_id"], feedback("passed"))
        with self.assertRaisesRegex(CreatorDNAError, "至少两次"):
            self.quality.optimize("alice", "v001", [dev["run_id"]], [fb["feedback_id"]])

    def test_cli_dispatches_complete_quality_and_evaluation_flow(self):
        import io
        common = ["--creator", "alice", "--workspace", str(self.workspace.root)]
        config_file = self.root / "cli-config.json"
        config_file.write_text(json.dumps(config()), encoding="utf-8")
        with patch("creator_dna.cli.CodexAdapter", return_value=self.agent):
            stream = io.StringIO()
            with patch("sys.stdout", stream):
                code = main(["quality", *common, "--topic", "合成 CLI 选题", "--config", str(config_file), "--max-revisions", "0"])
            self.assertEqual(code, 0)
            result = json.loads(stream.getvalue())
            self.assertEqual(result["status"], "passed")
            stream = io.StringIO()
            with patch("sys.stdout", stream):
                code = main(["evaluate", *common, "--script-file", result["output"], "--config", str(config_file)])
            self.assertEqual(code, 0)
            evaluated = json.loads(stream.getvalue())
            self.assertEqual(evaluated["status"], "passed")
            self.assertNotEqual(result["run_id"], evaluated["run_id"])


if __name__ == "__main__":
    unittest.main()
