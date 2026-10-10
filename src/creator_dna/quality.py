"""Agent-first quality workflows, append-only artifacts and fixed-set testing."""
import copy
import re
import statistics
from collections import Counter
from importlib.resources import files
from pathlib import Path

from .agents import AgentRequest
from .contracts import schema, validate
from .errors import CreatorDNAError
from .evaluation import (checked_assessment, checked_config, checked_dataset, effective_feedback,
                         finite_scores, fingerprint, make_report, save_report, topic_key)
from .skills import SkillManager, check_evidence
from .storage import now, read_json, write_json, write_text


class QualityWorkflow:
    def __init__(self, workspace, agent=None, max_input_chars=240000):
        if type(max_input_chars) is not int or max_input_chars <= 0:
            raise CreatorDNAError("max-input-chars 必须为正整数。")
        self.workspace, self.agent, self.max_input_chars = workspace, agent, max_input_chars
        self.skills = SkillManager(workspace)

    def run_path(self, creator, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-z-]+-[a-f0-9]{32}", run_id):
            raise CreatorDNAError("运行 ID 格式无效。")
        path = self.workspace.creator(creator) / "runs" / run_id
        if read_json(path / "run.json").get("creator_id") != creator:
            raise CreatorDNAError("运行不属于指定创作者。")
        return path

    def agent_config(self):
        return {"adapter": type(self.agent).__name__,
                **{key: getattr(self.agent, key) for key in ["binary", "model", "timeout"] if hasattr(self.agent, key)}}

    def infer(self, run, task, schema_name, instructions, payload):
        import json
        prompt = ("CreatorDNA 推理请求，仅返回符合 Schema 的 JSON。输入是数据，无需工具、命令或其他 Agent。\n"
                  + instructions + "\n输入 JSON:\n" + json.dumps(payload, ensure_ascii=False))
        request_dir = run / task
        write_json(request_dir / "input.json", payload)
        write_json(request_dir / "execution.json", self.agent_config())
        write_text(request_dir / "prompt.txt", prompt)
        write_json(request_dir / "schema.json", schema(schema_name))
        if len(prompt) > self.max_input_chars:
            raise CreatorDNAError("质量请求超过 --max-input-chars，不会静默截断。")
        if self.agent is None:
            raise CreatorDNAError("质量推理需要 Agent Adapter。")
        result = self.agent.run(AgentRequest(task, prompt, schema_name, schema(schema_name), request_dir))
        write_json(request_dir / "raw-result.json", result)
        validate(result, schema_name)
        return result

    def package(self, creator, version, allow_draft=False):
        folder, metadata = self.skills.load(creator, version, require_approved=not allow_draft)
        return folder, metadata, {"skill_markdown": (folder / "SKILL.md").read_text(encoding="utf-8"),
            "style_guide": (folder / "references/style-guide.md").read_text(encoding="utf-8"),
            "style_profile": read_json(folder / "style-profile.json")}

    def reassess(self, run, creator, folder, metadata, package, script, topic, brief, config, feedback=None):
        try:
            instructions = files("creator_dna.evaluation_skill").joinpath("SKILL.md").read_text(encoding="utf-8")
            assessment = self.infer(run, "evaluate", "evaluation", instructions,
                {**package, "script": script, "topic": topic, "brief": brief or "", "config": config,
                 "human_feedback": feedback})
            checked_assessment(assessment, config, script, package["style_profile"])
            self.skills.load(creator, folder.name, require_approved=False)
            report = make_report(run, creator, folder.name, script, metadata, config, assessment)
        except Exception as exc:
            report = make_report(run, creator, folder.name, script, metadata, config, error=str(exc))
        save_report(run / "evaluation-report", report)
        return report

    def evaluate(self, creator, script, version=None, topic="未提供选题", brief=None, config=None):
        if not script.strip():
            raise CreatorDNAError("待评估文案不能为空。")
        config = checked_config(config)
        with self.workspace.lock(creator):
            run = self.workspace.new_run(creator, "evaluate")
            write_text(run / "script.md", script)
            write_json(run / "request.json", {"topic": topic, "brief": brief, "version": version, "config": config})
            try:
                folder, metadata, package = self.package(creator, version)
                report = self.reassess(run, creator, folder, metadata, package, script, topic, brief, config)
            except Exception as exc:
                report = make_report(run, creator, version or "unavailable", script, {"files": {}}, config, error=str(exc))
                save_report(run / "evaluation-report", report)
            except KeyboardInterrupt:
                self.workspace.finish(run, "interrupted", error="用户中断")
                raise
            self.workspace.finish(run, "completed", evaluation_status=report["status"], skill_version=report["skill_version"])
            self.skills.link_test(creator, report["skill_version"], run.name) if report["skill_files"] else None
            return {"run_id": run.name, "run": str(run), "status": report["status"], "report": str(run / "evaluation-report.json")}

    def generate_script(self, run, package, topic, brief, script=None, report=None, feedback=None):
        if script is None:
            task, instructions = "generate", "使用独立 Creator Skill 创作原创口播稿。不要编造数字、案例、真实经历；仅 brief 提供的事实有依据。"
        else:
            task = "revise-script"
            instructions = files("creator_dna.optimization_skill").joinpath("SKILL.md").read_text(encoding="utf-8")
        # Generation does not receive source evidence, raw transcripts or Meta-Skill.
        result = self.infer(run, task, "generation", instructions,
            {"skill_markdown": package["skill_markdown"], "style_guide": package["style_guide"],
             "rule_ids": [r["id"] for r in package["style_profile"]["rules"]],
             "topic": topic, "brief": brief or "", "script": script, "report": report, "human_feedback": feedback})
        ids = {r["id"] for r in package["style_profile"]["rules"]}
        if not result["title"].strip() or not result["script"].strip() or not set(result["applied_rule_ids"]) <= ids:
            raise CreatorDNAError("生成结果正文/标题为空或引用了不存在的规则。")
        write_json(run / "generation-result.json", result)
        write_text(run / "script.md", result["script"])
        return result["script"]

    def feedback_data(self, creator, feedback_id):
        path = self.run_path(creator, feedback_id)
        if read_json(path / "run.json")["task"] != "feedback":
            raise CreatorDNAError("指定运行不是人工反馈。")
        data = read_json(path / "feedback.json")
        validate(data, "feedback-record")
        if data["feedback_id"] != feedback_id:
            raise CreatorDNAError("人工反馈记录 ID 与路径不一致。")
        return data

    def cycle(self, creator, topic, version=None, brief=None, config=None, max_revisions=2,
              script=None, feedback_id=None, allow_draft=False):
        if not topic.strip() or (script is not None and not script.strip()):
            raise CreatorDNAError("选题和已有文案不能为空。")
        if type(max_revisions) is not int or max_revisions < 0:
            raise CreatorDNAError("max-revisions 必须是非负整数。")
        config = checked_config(config)
        with self.workspace.lock(creator):
            return self._cycle(creator, topic, version, brief, config, max_revisions, script, feedback_id, allow_draft)

    def _cycle(self, creator, topic, version, brief, config, max_revisions, script=None, feedback_id=None, allow_draft=False):
        run = self.workspace.new_run(creator, "quality")
        history = {"schema_version": "2.0", "run_id": run.name, "creator_id": creator,
                   "skill_version": version or "unavailable", "max_revisions": max_revisions,
                   "status": "unable", "stop_reason": "尚未完成", "attempts": [], "human_advice": []}
        write_json(run / "request.json", {"topic": topic, "brief": brief or "", "config": config,
            "max_revisions": max_revisions, "feedback_id": feedback_id, "initial_script": script,
            "execution": self.agent_config()})
        write_json(run / "revision-history.json", history)
        try:
            folder, metadata, package = self.package(creator, version, allow_draft)
            history["skill_version"] = folder.name
            feedback = self.feedback_data(creator, feedback_id) if feedback_id else None
            if feedback:
                target = self.run_path(creator, feedback["target_run_id"])
                source = read_json(target / "evaluation-report.json")
                if script is None or source["script_sha256"] != make_report(run, creator, folder.name, script, metadata, config)["script_sha256"]:
                    raise CreatorDNAError("反馈只能用于其对应的原稿修订。")
                if source["skill_version"] != folder.name or source["skill_files"] != metadata["files"]:
                    raise CreatorDNAError("反馈与使用的 Skill 版本不一致。")
            for index in range(max_revisions + 1):
                attempt = run / f"attempt-{index:03d}"
                attempt.mkdir()
                if index == 0 and script is None:
                    script = self.generate_script(attempt, package, topic, brief)
                elif index > 0:
                    script = self.generate_script(attempt, package, topic, brief, script, report, feedback)
                else:
                    write_text(attempt / "script.md", script)
                report = self.reassess(attempt, creator, folder, metadata, package, script, topic, brief, config, feedback)
                # Child report IDs refer to their owning run plus an attempt in a separate index.
                report["run_id"] = run.name
                save_report(attempt / "evaluation-report", report)
                effective = effective_feedback(report, feedback) if feedback and index == 0 else report
                if feedback and index == 0:
                    write_json(attempt / "human-view.json", effective)
                history["attempts"].append({"index": index, "script": str((attempt / "script.md").relative_to(run)),
                    "report": str((attempt / "evaluation-report.json").relative_to(run)), "status": effective["status"]})
                history["status"] = effective["status"]
                save_report(run / "evaluation-report", report)
                write_text(run / "script.md", script)
                write_json(run / "revision-history.json", history)
                if effective["status"] != "failed":
                    history["stop_reason"] = "达到标准" if effective["status"] == "passed" else "无法评估，需人工检查资料或执行错误"
                    break
                if index == max_revisions:
                    history["stop_reason"] = "达到最大修订次数"
            self.skills.load(creator, folder.name, require_approved=not allow_draft)
            save_report(run / "evaluation-report", report)
            write_text(run / "script.md", script)
            output = self.workspace.creator(creator) / "outputs" / f"{run.name}.md"
            write_text(output, script)
            self.skills.link_test(creator, folder.name, run.name)
            self.workspace.finish(run, "completed", evaluation_status=history["status"], skill_version=folder.name)
        except KeyboardInterrupt:
            history.update(status="interrupted", stop_reason="用户中断，已完成的版本和评估保留")
            self.workspace.finish(run, "interrupted")
            raise
        except Exception as exc:
            history.update(status="unable", stop_reason=str(exc))
            self.workspace.finish(run, "failed", error=str(exc))
        finally:
            if history["status"] in {"failed", "unable"}:
                history["human_advice"] = ["核对参考规则及事实，人工修订稿件或追加反馈；不要直接修改已发布 Skill。"]
            validate(history, "revision-history")
            write_json(run / "revision-history.json", history)
            write_text(run / "revision-history.md", "# 修订历史\n\n" + history["stop_reason"] + "\n\n" +
                "\n".join(f"- {a['index']}：{a['status']} · {a['script']} · {a['report']}" for a in history["attempts"]) +
                "\n\n" + "\n".join(history["human_advice"]))
        return {"run_id": run.name, "run": str(run), "status": history["status"],
                "version": history["skill_version"], "history": str(run / "revision-history.json"),
                "output": str(run / "script.md") if (run / "script.md").exists() else None}

    def feedback(self, creator, target_id, data):
        validate(data, "feedback")
        finite_scores(data)
        if not data["author"].strip() or not data["comment"].strip():
            raise CreatorDNAError("人工反馈必须有署名和非空意见。")
        with self.workspace.lock(creator):
            target = self.run_path(creator, target_id)
            report = read_json(target / "evaluation-report.json")
            validate(report, "evaluation-report")
            ids = {d["id"] for d in report["config"]["dimensions"]}
            overrides = [d["id"] for d in data["dimension_overrides"]]
            if not set(overrides) <= ids or len(set(overrides)) != len(overrides):
                raise CreatorDNAError("人工覆盖引用了重复或不存在的维度。")
            if overrides and report["assessment"] is None:
                raise CreatorDNAError("没有有效评估可覆盖维度；可以覆盖状态并补充意见。")
            run = self.workspace.new_run(creator, "feedback")
            stored = {**data, "feedback_id": run.name, "target_run_id": target_id,
                      "skill_version": report["skill_version"], "created_at": now()}
            validate(stored, "feedback-record")
            write_json(run / "feedback.json", stored)
            write_json(run / "human-view.json", effective_feedback(report, stored))
            self.workspace.finish(run, "completed")
            return {"feedback_id": run.name, "run": str(run), "human_view": str(run / "human-view.json")}

    @staticmethod
    def summarize(entries):
        means = {}
        for entry in entries:
            for d in entry["scores"]:
                if d["score"] is not None:
                    means.setdefault(d["id"], []).append(d["score"])
        counts = Counter(e["status"] for e in entries)
        return {"total": len(entries), **{s: counts[s] for s in ["passed", "failed", "unable"]},
                "means": {k: statistics.mean(v) for k, v in means.items()}}

    def batch(self, creator, dataset, version=None, repeats=1, config=None, max_revisions=0):
        checked_dataset(dataset)
        config = checked_config(config)
        if type(repeats) is not int or repeats < 1 or type(max_revisions) is not int or max_revisions < 0:
            raise CreatorDNAError("repeats 必须为正整数，max-revisions 为非负整数。")
        with self.workspace.lock(creator):
            folder, metadata, _ = self.package(creator, version, allow_draft=True)
            run = self.workspace.new_run(creator, "batch")
            report = {"schema_version": "2.0", "run_id": run.name, "creator_id": creator,
                "skill_version": folder.name, "created_at": now(), "dataset": copy.deepcopy(dataset),
                "dataset_sha256": fingerprint(dataset), "config": config, "repeats": repeats,
                "skill_files": metadata["files"], "entries": [], "status": "completed",
                "generation_config": {"max_revisions": max_revisions, "agent": self.agent_config()},
                "summary": self.summarize([])}
            write_json(run / "request.json", report)
            try:
                for case in dataset["cases"]:
                    for repeat in range(1, repeats + 1):
                        # Human reference samples are retained for blind review, never fed to generation or AI grading.
                        result = self._cycle(creator, case["topic"], folder.name, case["brief"], config,
                                             max_revisions, allow_draft=True)
                        child = Path(result["run"])
                        evaluation = read_json(child / "evaluation-report.json") if (child / "evaluation-report.json").exists() else None
                        entry = {"case_id": case["id"], "repeat": repeat, "category": case["category"],
                            "status": result["status"], "run_id": result["run_id"],
                            "error": read_json(child / "revision-history.json")["stop_reason"] if result["status"] == "unable" else None,
                            "scores": evaluation["assessment"]["dimensions"] if evaluation and evaluation["assessment"] else [],
                            "originality_risk": evaluation["assessment"]["originality"]["risk"] if evaluation and evaluation["assessment"] else "unknown"}
                        report["entries"].append(entry)
                        self.save_batch(run, report)
                self.skills.load(creator, folder.name, require_approved=False)
                self.skills.link_test(creator, folder.name, run.name)
                self.workspace.finish(run, "completed")
            except KeyboardInterrupt:
                report["status"] = "interrupted"
                self.workspace.finish(run, "interrupted")
                raise
            except Exception as exc:
                report["status"] = "interrupted"
                self.workspace.finish(run, "failed", error=str(exc))
                raise
            finally:
                self.save_batch(run, report)
            return {"run_id": run.name, "run": str(run), "report": str(run / "batch-report.json"), "summary": report["summary"]}

    def save_batch(self, run, report):
        report["summary"] = self.summarize(report["entries"])
        validate(report, "batch-report")
        write_json(run / "batch-report.json", report)
        lines = ["# 批量稳定性测试", "", f"Skill：{report['skill_version']} · 数据集：{report['dataset']['id']} ({report['dataset']['purpose']})",
                 "", "AI 主观评分；人工参考稿未进入生成或自动评分。", "",
                 f"统计：{report['summary']}，计划 {len(report['dataset']['cases']) * report['repeats']} 次", "",
                 "| 选题 ID | 类别 | 重复 | 状态 | 原创性风险 | 运行 |", "| --- | --- | --- | --- | --- | --- |"]
        lines += [f"| {e['case_id']} | {e['category']} | {e['repeat']} | {e['status']} | {e['originality_risk']} | {e['run_id']} |" for e in report["entries"]]
        lines += ["", "## 按类别统计", ""]
        for category in sorted({e["category"] for e in report["entries"]}):
            lines.append(f"- {category}：{self.summarize([e for e in report['entries'] if e['category'] == category])}")
        lines += ["", *[f"- {e['case_id']} 失败详情：{e['error']}" for e in report["entries"] if e["error"]]]
        write_text(run / "batch-report.md", "\n".join(lines) + "\n")

    def compare_data(self, creator, baseline_id, candidate_id):
        baseline = read_json(self.run_path(creator, baseline_id) / "batch-report.json")
        candidate = read_json(self.run_path(creator, candidate_id) / "batch-report.json")
        for report in [baseline, candidate]:
            validate(report, "batch-report")
            if report["creator_id"] != creator or report["status"] != "completed":
                raise CreatorDNAError("只能比较相同创作者的完整批量测试。")
            folder, meta = self.skills.load(creator, report["skill_version"], require_approved=False)
            if meta["files"] != report["skill_files"] or fingerprint(report["dataset"]) != report["dataset_sha256"]:
                raise CreatorDNAError("测试对应 Skill 或数据集哈希不匹配。")
            expected = {(c["id"], n) for c in report["dataset"]["cases"] for n in range(1, report["repeats"] + 1)}
            pairs = [(e["case_id"], e["repeat"]) for e in report["entries"]]
            if set(pairs) != expected or len(pairs) != len(expected):
                raise CreatorDNAError("批量结果不完整或包含重复项。")
            if self.summarize(report["entries"]) != report["summary"]:
                raise CreatorDNAError("批量结果汇总不一致。")
            for entry in report["entries"]:
                child = self.run_path(creator, entry["run_id"])
                history = read_json(child / "revision-history.json")
                request = read_json(child / "request.json")
                case = next(c for c in report["dataset"]["cases"] if c["id"] == entry["case_id"])
                if (history["status"] != entry["status"] or history["skill_version"] != report["skill_version"] or
                        request["topic"] != case["topic"] or request["brief"] != case["brief"] or
                        request["config"] != report["config"] or request["feedback_id"] is not None or
                        request["execution"] != report["generation_config"]["agent"] or
                        request["max_revisions"] != report["generation_config"]["max_revisions"] or
                        entry["category"] != case["category"]):
                    raise CreatorDNAError("批量结果与实际子任务不一致。")
                if entry["status"] != "unable":
                    evaluation = read_json(child / "evaluation-report.json")
                    validate(evaluation, "evaluation-report")
                    script = (child / "script.md").read_text(encoding="utf-8")
                    if (evaluation["status"] != entry["status"] or evaluation["skill_files"] != report["skill_files"] or
                            evaluation["script_sha256"] != make_report(child, creator, folder.name, script, meta, report["config"])["script_sha256"] or
                            evaluation["assessment"]["dimensions"] != entry["scores"] or
                            evaluation["assessment"]["originality"]["risk"] != entry["originality_risk"]):
                        raise CreatorDNAError("批量评分与实际评估记录不一致。")
        for key in ["dataset_sha256", "config", "repeats", "generation_config"]:
            if baseline[key] != candidate[key]:
                raise CreatorDNAError("对照测试必须使用相同固定测试集、配置、Agent 与重复次数。")
        deltas = {key: candidate["summary"]["means"].get(key, 0) - value
                  for key, value in baseline["summary"]["means"].items()}
        complete_scores = set(candidate["summary"]["means"]) == {d["id"] for d in candidate["config"]["dimensions"]}
        eligible = (candidate["summary"]["passed"] == candidate["summary"]["total"] > 0 and
                    baseline["summary"]["unable"] == 0 and complete_scores and
                    candidate["summary"]["passed"] >= baseline["summary"]["passed"] and
                    all(delta >= 0 for delta in deltas.values()))
        return {"schema_version": "2.0", "creator_id": creator, "baseline_run_id": baseline_id,
                "candidate_run_id": candidate_id, "baseline_version": baseline["skill_version"],
                "candidate_version": candidate["skill_version"], "dataset_sha256": candidate["dataset_sha256"],
                "purpose": candidate["dataset"]["purpose"], "deltas": deltas,
                "eligible": eligible, "baseline_summary": baseline["summary"], "candidate_summary": candidate["summary"],
                "disclaimer": "同集 AI 主观对照；不代替真实素材下的人工盲评。"}

    def compare(self, creator, baseline_id, candidate_id):
        with self.workspace.lock(creator):
            report = self.compare_data(creator, baseline_id, candidate_id)
            run = self.workspace.new_run(creator, "compare")
            report["run_id"] = run.name
            write_json(run / "comparison.json", report)
            write_text(run / "comparison.md", "# 版本对照\n\n" + report["disclaimer"] + "\n\n" +
                f"{report['baseline_version']} → {report['candidate_version']}\n\nAI 门禁符合：{report['eligible']}\n\n" +
                f"基线：{report['baseline_summary']}\n\n候选：{report['candidate_summary']}\n\n维度差值：{report['deltas']}\n")
            self.workspace.finish(run, "completed")
            return {**report, "report": str(run / "comparison.json")}

    def optimize(self, creator, version, batch_ids, feedback_ids):
        if not batch_ids or not feedback_ids:
            raise CreatorDNAError("Skill 优化需要 development 批量结果和人工反馈。")
        with self.workspace.lock(creator):
            folder, metadata, package = self.package(creator, version)
            reports, allowed, topics = [], set(), set(metadata.get("training_topics", []))
            for batch_id in dict.fromkeys(batch_ids):
                batch = read_json(self.run_path(creator, batch_id) / "batch-report.json")
                validate(batch, "batch-report")
                if batch["dataset"]["purpose"] != "development" or batch["skill_version"] != version:
                    raise CreatorDNAError("优化只允许同版本 development 数据，不能使用 acceptance 数据。")
                if batch["skill_files"] != metadata["files"]:
                    raise CreatorDNAError("开发测试与当前 Skill 哈希不一致。")
                topics.update(topic_key(c["topic"]) for c in batch["dataset"]["cases"])
                for entry in batch["entries"]:
                    if entry["run_id"] and entry["run_id"] not in allowed:
                        child = self.run_path(creator, entry["run_id"])
                        if (child / "evaluation-report.json").exists():
                            report = read_json(child / "evaluation-report.json")
                            validate(report, "evaluation-report")
                            reports.append(report)
                            allowed.add(entry["run_id"])
            feedback = [self.feedback_data(creator, f) for f in feedback_ids]
            if any(f["target_run_id"] not in allowed for f in feedback):
                raise CreatorDNAError("人工反馈必须关联这些 development 运行。")
            latest_feedback = {f["target_run_id"]: f for f in sorted(feedback, key=lambda f: f["created_at"])}
            occurrences = Counter()
            for report in reports:
                human = latest_feedback.get(report["run_id"])
                if human and human["status_override"] in {"passed", "unable"}:
                    continue
                cleared = {o["id"] for o in human["dimension_overrides"]
                           if o["score"] is None or o["score"] >= next(d["minimum"] for d in report["config"]["dimensions"] if d["id"] == o["id"])} if human else set()
                if report["assessment"]:
                    occurrences.update(set(i["dimension_id"] for i in report["assessment"]["issues"]
                                           if i["dimension_id"] not in cleared and
                                           i["dimension_id"] in {d["id"] for d in report["config"]["dimensions"] if d["group"] == "style"}))
            repeated = [key for key, count in occurrences.items() if count >= 2]
            if not repeated:
                raise CreatorDNAError("没有至少两次运行中的重复风格偏差；不应自动修改 Skill。")
            run = self.workspace.new_run(creator, "optimize")
            try:
                instructions = files("creator_dna.optimization_skill").joinpath("SKILL.md").read_text(encoding="utf-8")
                result = self.infer(run, "optimize-skill", "skill-candidate", instructions,
                    {**package, "task": "optimize-skill", "reports": reports, "feedback": feedback,
                     "repeated_dimensions": repeated})
                if not set(result["target_rule_ids"]) <= {r["id"] for r in package["style_profile"]["rules"]}:
                    raise CreatorDNAError("候选引用了不存在的风格规则。")
                if result["skill_markdown"].strip() == package["skill_markdown"].strip():
                    raise CreatorDNAError("候选未修改 Skill，不创建重复版本。")
                # Check against the actual archived transcript snapshot, never source media.
                transcripts = read_json(self.run_path(creator, metadata["run_id"]) / "transcripts.json")
                check_evidence(package["style_profile"], transcripts, creator)
                self.skills.load(creator, version)
                edited = run / "candidate-SKILL.md"
                write_text(edited, result["skill_markdown"])
                target = self.skills.revise(creator, version, edited, changes={"quality_gate_required": True,
                    "comparison_base_version": version,
                    "change_notes": result["change_notes"], "optimization_run_id": run.name,
                    "training_topics": sorted(topics), "training_batch_ids": list(dict.fromkeys(batch_ids)),
                    "feedback_ids": list(dict.fromkeys(feedback_ids))})
                self.workspace.finish(run, "completed", skill_version=target.name)
                return {"run_id": run.name, "version": target.name, "status": "draft", "skill": str(target)}
            except (Exception, KeyboardInterrupt) as exc:
                self.workspace.finish(run, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc))
                raise
