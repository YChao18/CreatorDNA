"""Deterministic evaluation contracts; no model or media operations."""
import copy
import hashlib
import json
import math
import unicodedata

from .contracts import validate
from .errors import CreatorDNAError
from .storage import now, write_json, write_text


DISCLAIMER = "AI 主观评价，不是客观风格相似度；原创性仅覆盖提供的证据，事实未经外部核验。"
DEFAULT_CONFIG = {
    "schema_version": "2.0",
    "dimensions": [
        {"id": "language", "group": "style", "description": "措辞、口语化和句式习惯", "minimum": 3},
        {"id": "structure", "group": "style", "description": "开头、展开、论证和结尾", "minimum": 3},
        {"id": "rhythm", "group": "style", "description": "文本信息密度、句长和转折", "minimum": 3},
        {"id": "emotion", "group": "style", "description": "语气、情感强度和人物表达", "minimum": 3},
        {"id": "identity", "group": "style", "description": "有证据的创作者典型表达组合", "minimum": 3},
        {"id": "content", "group": "quality", "description": "逻辑、自然度、可读性及事实依据", "minimum": 3},
    ],
    "max_originality_risk": "low",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def topic_key(topic):
    return "".join(unicodedata.normalize("NFKC", topic).casefold().split())


def finite_scores(value):
    if isinstance(value, dict):
        for item in value.values():
            finite_scores(item)
    elif isinstance(value, list):
        for item in value:
            finite_scores(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise CreatorDNAError("评分或配置包含非有限数值。")


def checked_config(config=None):
    config = copy.deepcopy(DEFAULT_CONFIG if config is None else config)
    validate(config, "evaluation-config")
    finite_scores(config)
    ids = [d["id"] for d in config["dimensions"]]
    if len(set(ids)) != len(ids) or any(not i.strip() for i in ids):
        raise CreatorDNAError("评估维度 ID 必须非空且唯一。")
    if {d["group"] for d in config["dimensions"]} != {"style", "quality"}:
        raise CreatorDNAError("评分配置必须分别包含 style 和 quality 维度。")
    return config


def checked_dataset(dataset):
    validate(dataset, "dataset")
    ids = [c["id"] for c in dataset["cases"]]
    topics = [topic_key(c["topic"]) for c in dataset["cases"]]
    if len(set(ids)) != len(ids) or len(set(topics)) != len(topics) or not all(topics) or any(not i.strip() for i in ids):
        raise CreatorDNAError("测试集 case ID、选题必须唯一，选题不能为空。")
    return dataset


def check_quote(evidence, script, rules):
    quote, kind, rule_id = evidence["quote"], evidence["kind"], evidence["rule_id"]
    if not quote.strip():
        raise CreatorDNAError("评估证据不能为空白。")
    if kind == "script":
        valid = rule_id is None and quote in script
    else:
        rule = rules.get(rule_id)
        valid = rule is not None and (quote in rule["instruction"] if kind == "rule" else
                                     any(quote in e["quote"] for e in rule["evidence"]))
    if not valid:
        raise CreatorDNAError("评估证据不是对应文案/规则/参考片段的原文。")


def checked_assessment(assessment, config, script, profile):
    validate(assessment, "evaluation")
    finite_scores(assessment)
    expected = {d["id"]: d for d in config["dimensions"]}
    actual = [d["id"] for d in assessment["dimensions"]]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise CreatorDNAError("评估维度缺失、重复或包含未配置项。")
    rules = {r["id"]: r for r in profile["rules"]}
    for dimension in assessment["dimensions"]:
        if dimension["group"] != expected[dimension["id"]]["group"]:
            raise CreatorDNAError("评估维度所属组不匹配。")
        evidence = dimension["evidence"]
        for e in evidence:
            check_quote(e, script, rules)
        kinds = {e["kind"] for e in evidence}
        if dimension["score"] is not None:
            if "script" not in kinds or (dimension["group"] == "style" and not kinds & {"rule", "reference"}):
                raise CreatorDNAError("可评分维度必须提供文案证据；风格评分还需要规则证据。")
        if dimension["score"] is not None and dimension["score"] < expected[dimension["id"]]["minimum"]:
            if not any(i["dimension_id"] == dimension["id"] for i in assessment["issues"]):
                raise CreatorDNAError("未达标维度缺少问题和改进建议。")
    for issue in assessment["issues"]:
        if issue["dimension_id"] not in expected and issue["dimension_id"] != "originality":
            raise CreatorDNAError("问题引用了不存在的评分维度。")
        for e in issue["evidence"]:
            check_quote(e, script, rules)
    for e in assessment["originality"]["evidence"]:
        check_quote(e, script, rules)
    if assessment["originality"]["risk"] in {"medium", "high"}:
        if not {"script", "reference"} <= {e["kind"] for e in assessment["originality"]["evidence"]}:
            raise CreatorDNAError("中高原创性风险需要文案和参考原文证据。")
    return assessment


def decide(assessment, config):
    if assessment is None:
        return "unable", ["没有有效 AI 评估。"]
    reasons = []
    limits = {d["id"]: d["minimum"] for d in config["dimensions"]}
    unable = []
    for d in assessment["dimensions"]:
        if d["score"] is None:
            unable.append(f"{d['id']} 无法评分：{d['reason']}")
        elif d["score"] < limits[d["id"]]:
            reasons.append(f"{d['id']}：{d['score']} < {limits[d['id']]}")
    risk = assessment["originality"]["risk"]
    ranks = {"low": 0, "medium": 1, "high": 2}
    if risk == "unknown":
        unable.append("原创性风险无法判断。")
    elif ranks[risk] > ranks[config["max_originality_risk"]]:
        reasons.append(f"原创性风险 {risk} 超过允许值 {config['max_originality_risk']}。")
    return ("unable", unable + reasons) if unable else ("failed", reasons) if reasons else ("passed", [])


def make_report(run, creator, version, script, metadata, config, assessment=None, error=None):
    status, reasons = decide(assessment, config)
    report = {"schema_version": "2.0", "run_id": run.name, "creator_id": creator,
              "skill_version": version, "created_at": now(),
              "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
              "skill_files": metadata["files"], "config": config, "status": status,
              "reasons": [error] if error else reasons, "assessment": assessment, "disclaimer": DISCLAIMER}
    validate(report, "evaluation-report")
    return report


def report_markdown(report):
    lines = ["# 口播评估报告", "", f"运行：{report['run_id']} · Skill：{report['skill_version']}", "",
             f"状态：{report['status']}", "", report["disclaimer"], "",
             *[f"- {reason}" for reason in report["reasons"]], ""]
    assessment = report["assessment"]
    if assessment:
        for group, title in [("style", "风格一致性"), ("quality", "内容质量")]:
            lines += [f"## {title}", ""]
            for d in assessment["dimensions"]:
                if d["group"] == group:
                    lines += [f"- {d['id']}：{d['score'] if d['score'] is not None else '无法评分'}/5；{d['reason']}"]
                    lines += [f"  - {e['kind']}（{e['rule_id'] or '正文'}）：{e['quote']}" for e in d["evidence"]]
            lines += [""]
        lines += ["## 原创性风险", "", f"{assessment['originality']['risk']}：{assessment['originality']['reason']}", ""]
        lines += [f"- {e['kind']}：{e['quote']}" for e in assessment["originality"]["evidence"]]
        lines += ["", "## 问题与建议", ""]
        for issue in assessment["issues"]:
            lines += [f"- {issue['dimension_id']}：{issue['description']}；建议：{issue['suggestion']}"]
            lines += [f"  - {e['kind']}：{e['quote']}" for e in issue["evidence"]]
        lines += ["", "## 局限", "", *[f"- {s}" for s in assessment["limitations"]]]
    return "\n".join(lines) + "\n"


def save_report(path, report):
    write_json(path.with_suffix(".json"), report)
    write_text(path.with_suffix(".md"), report_markdown(report))


def effective_feedback(report, feedback):
    """Return a separate human view, preserving the original AI report."""
    result = copy.deepcopy(report)
    if result["assessment"]:
        scores = {d["id"]: d for d in result["assessment"]["dimensions"]}
        for override in feedback["dimension_overrides"]:
            scores[override["id"]].update(score=override["score"], reason=override["reason"])
        result["status"], result["reasons"] = decide(result["assessment"], result["config"])
    if feedback["status_override"] is not None:
        result["status"] = feedback["status_override"]
    result["human_feedback"] = feedback
    return result
