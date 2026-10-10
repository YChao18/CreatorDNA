import difflib
import re
import shutil
import uuid
from pathlib import Path

import yaml

from .contracts import validate
from .errors import CreatorDNAError
from .storage import digest_file, now, read_json, validate_version, write_json, write_text


def validate_skill(markdown, creator_id):
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.+)\Z", markdown.strip() + "\n", re.S)
    if not match:
        raise CreatorDNAError("Creator-Skill 须包含 YAML frontmatter 和非空正文。")
    try:
        metadata = yaml.safe_load(match[1])
    except yaml.YAMLError as exc:
        raise CreatorDNAError(f"Skill frontmatter 无效：{exc}") from exc
    if not isinstance(metadata, dict) or metadata.get("name") != f"creator-{creator_id}":
        raise CreatorDNAError(f"Skill name 须为 creator-{creator_id}。")
    if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
        raise CreatorDNAError("Skill 必须具有非空 description。")
    # This V1.0 portable package intentionally has one supported relative reference.
    links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", match[2])
    if any(link != "references/style-guide.md" for link in links):
        raise CreatorDNAError("Creator-Skill 仅能引用本包内 references/style-guide.md。")
    if "references/style-guide.md" not in links:
        raise CreatorDNAError("Creator-Skill 应引用 references/style-guide.md，确保规则随 Skill 一起携带。")
    if re.search(r"(?:[A-Za-z]:[\\/]|(?:\.\./)|file://|https?://|/workspace/)", match[2]):
        raise CreatorDNAError("Creator-Skill 不得依赖外部 URL、素材绝对路径或包外路径。")


def check_evidence(profile, transcripts, creator_id):
    validate(profile, "style-profile")
    if profile["creator_id"] != creator_id or profile["sample_count"] != len(transcripts):
        raise CreatorDNAError("风格结果的 creator_id 或 sample_count 与输入不一致。")
    by_source = {t["source_id"]: t for t in transcripts}
    ids = set()
    for rule in profile["rules"]:
        if rule["id"] in ids:
            raise CreatorDNAError("风格 rule ID 必须唯一。")
        ids.add(rule["id"])
        for evidence in rule["evidence"]:
            transcript = by_source.get(evidence["source_id"])
            index = evidence["segment_index"]
            if transcript is None or index >= len(transcript["segments"]):
                raise CreatorDNAError(f"规则 {rule['id']} 引用了不存在的素材或片段。")
            quote = evidence["quote"].strip()
            if not quote or quote not in transcript["segments"][index]["text"]:
                raise CreatorDNAError(f"规则 {rule['id']} 的证据不是对应片段的原文。")
        if rule["confidence"] == "high":
            independent = {tuple(s["text"] for s in by_source[e["source_id"]]["segments"])
                           for e in rule["evidence"]}
            if len(independent) < 2:
                raise CreatorDNAError(f"规则 {rule['id']} 的 high 置信度至少需要两个内容不同的素材支持。")


def style_guide(profile):
    lines = ["# 创作者风格规则", "", profile["summary"], ""]
    for rule in profile["rules"]:
        lines.extend([f"## {rule['id']} · {rule['dimension']}", "", rule["instruction"], "",
                      f"置信度：{rule['confidence']}。用于迁移表达规律，避免照搬例句。", ""])
    lines.extend(["## 样本局限", "", *[f"- {x}" for x in profile["limitations"]], ""])
    return "\n".join(lines)


class SkillManager:
    def __init__(self, workspace):
        self.workspace = workspace

    def versions(self, creator_id):
        root = self.workspace.creator(creator_id) / "skills"
        if not root.exists():
            return []
        return sorted([p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"v[0-9]{3,}", p.name)],
                      key=lambda p: int(p.name[1:]))

    def publish(self, creator_id, result, transcripts, run):
        profile, markdown = result["style_profile"], result["skill_markdown"]
        check_evidence(profile, transcripts, creator_id)
        validate_skill(markdown, creator_id)
        versions = self.versions(creator_id)
        version = f"v{int(versions[-1].name[1:]) + 1 if versions else 1:03d}"
        root = self.workspace.creator(creator_id) / "skills"
        stage = root / f".draft-{uuid.uuid4().hex}"
        stage.mkdir(parents=True)
        write_text(stage / "SKILL.md", markdown.rstrip() + "\n")
        write_text(stage / "references" / "style-guide.md", style_guide(profile))
        write_json(stage / "style-profile.json", profile)
        write_json(stage / "metadata.json", {"schema_version": "2.0", "creator_id": creator_id,
                   "version": version, "status": "draft", "created_at": now(), "run_id": run.name,
                   "skill_id": f"creator-{creator_id}", "modified_at": now(), "change_notes": "素材蒸馏",
                   "parent_version": versions[-1].name if versions else None, "test_run_ids": [],
                   "sources": [{"source_id": t["source_id"], "sha256": t["source_sha256"]} for t in transcripts],
                   "files": {name: digest_file(stage / name) for name in
                             ["SKILL.md", "references/style-guide.md", "style-profile.json"]}})
        target = root / version
        stage.rename(target)
        return target

    def load(self, creator_id, version=None, require_approved=True):
        root = self.workspace.creator(creator_id) / "skills"
        if version is None:
            active = root / "active.json"
            if not active.exists():
                raise CreatorDNAError("尚无已确认 Skill。先查看蒸馏结果，再运行 skills approve --creator ID --version v001。")
            pointer = read_json(active)
            if not isinstance(pointer, dict) or not isinstance(pointer.get("version"), str):
                raise CreatorDNAError("Skill 激活记录格式错误，需恢复有效版本指针。")
            version = pointer["version"]
        folder = root / validate_version(version)
        if not folder.is_dir() or folder.is_symlink():
            raise CreatorDNAError(f"Skill 版本不存在：{folder}")
        metadata = read_json(folder / "metadata.json")
        validate(metadata, "skill-metadata")
        if metadata.get("creator_id") != creator_id or metadata.get("version") != version:
            raise CreatorDNAError("Skill 元数据与创作者或版本不一致。")
        if require_approved and metadata.get("status") != "approved":
            raise CreatorDNAError("该版本仍为草稿，请先使用 skills approve 确认。")
        expected = {"SKILL.md", "references/style-guide.md", "style-profile.json"}
        if not isinstance(metadata.get("files"), dict) or set(metadata["files"]) != expected:
            raise CreatorDNAError("Skill 文件清单不完整。")
        for name in expected:
            path = folder / name
            if (not path.is_file() or path.is_symlink() or folder.resolve() not in path.resolve().parents
                    or digest_file(path) != metadata["files"][name]):
                raise CreatorDNAError(f"Skill 文件被修改或缺失：{path}。使用 skills revise 创建新版本并重新确认。")
        markdown = (folder / "SKILL.md").read_text(encoding="utf-8")
        validate_skill(markdown, creator_id)
        profile = read_json(folder / "style-profile.json")
        validate(profile, "style-profile")
        if profile["creator_id"] != creator_id:
            raise CreatorDNAError("风格资料与创作者不一致。")
        return folder, metadata

    def approve(self, creator_id, version, comparison_id=None, confirmation=None):
        folder, metadata = self.load(creator_id, version, require_approved=False)
        if metadata.get("quality_gate_required"):
            if not comparison_id or not confirmation or not confirmation.strip():
                raise CreatorDNAError("V2 候选必须提供 acceptance 对照运行 --comparison 和人工确认 --confirmation。")
            from .quality import QualityWorkflow
            from .evaluation import topic_key
            quality = QualityWorkflow(self.workspace)
            comparison = read_json(quality.run_path(creator_id, comparison_id) / "comparison.json")
            checked = quality.compare_data(creator_id, comparison["baseline_run_id"], comparison["candidate_run_id"])
            if (checked["purpose"] != "acceptance" or checked["candidate_version"] != version or
                    checked["baseline_version"] != metadata.get("comparison_base_version", metadata.get("parent_version")) or not checked["eligible"]):
                raise CreatorDNAError("候选未通过与父版本的独立 acceptance 对照。")
            self.load(creator_id, checked["baseline_version"])
            batch = read_json(quality.run_path(creator_id, checked["candidate_run_id"]) / "batch-report.json")
            topics = {topic_key(c["topic"]) for c in batch["dataset"]["cases"]}
            if topics & set(metadata.get("training_topics", [])):
                raise CreatorDNAError("验收选题与优化训练选题重叠，不能发布。")
            if "model" in batch["generation_config"]["agent"] and not batch["generation_config"]["agent"]["model"]:
                raise CreatorDNAError("发布对照需用 --model 固定模型；默认模型配置可能在两次运行之间改变。")
            metadata.update(comparison_run_id=comparison_id, human_confirmation=confirmation,
                            acceptance_dataset_sha256=checked["dataset_sha256"])
        metadata.update(status="approved", approved_at=now(), modified_at=now())
        write_json(folder / "metadata.json", metadata)
        previous = read_json(folder.parent / "active.json")["version"] if (folder.parent / "active.json").exists() else None
        write_json(folder.parent / "active.json", {"version": version, "previous_version": previous, "updated_at": now()})
        return folder

    def revise(self, creator_id, version, markdown_path, changes=None):
        folder, metadata = self.load(creator_id, version, require_approved=False)
        markdown = Path(markdown_path).read_text(encoding="utf-8-sig")
        validate_skill(markdown, creator_id)
        versions = self.versions(creator_id)
        new_version = f"v{int(versions[-1].name[1:]) + 1:03d}"
        target = folder.parent / new_version
        stage = folder.parent / f".draft-{uuid.uuid4().hex}"
        stage.mkdir()
        for name in metadata["files"]:
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / name, stage / name)
        write_text(stage / "SKILL.md", markdown.rstrip() + "\n")
        metadata.update(schema_version="2.0", version=new_version, status="draft", created_at=now(), modified_at=now(), parent_version=version,
                        skill_id=f"creator-{creator_id}", change_notes="人工修订", test_run_ids=[])
        metadata.pop("approved_at", None)
        for key in ["comparison_run_id", "human_confirmation", "acceptance_dataset_sha256"]:
            metadata.pop(key, None)
        if changes:
            metadata.update(changes)
        if metadata.get("quality_gate_required"):
            metadata.setdefault("comparison_base_version", version)
        metadata["files"]["SKILL.md"] = digest_file(stage / "SKILL.md")
        write_json(stage / "metadata.json", metadata)
        stage.rename(target)
        return target

    def link_test(self, creator_id, version, run_id):
        folder, metadata = self.load(creator_id, version, require_approved=False)
        tests = metadata.setdefault("test_run_ids", [])
        if run_id not in tests:
            tests.append(run_id)
            metadata["modified_at"] = now()
            write_json(folder / "metadata.json", metadata)

    def diff(self, creator_id, left, right):
        a, _ = self.load(creator_id, left, require_approved=False)
        b, _ = self.load(creator_id, right, require_approved=False)
        chunks = []
        for name in ["SKILL.md", "references/style-guide.md", "style-profile.json"]:
            chunks.extend(difflib.unified_diff((a / name).read_text(encoding="utf-8").splitlines(True),
                (b / name).read_text(encoding="utf-8").splitlines(True), fromfile=f"{left}/{name}", tofile=f"{right}/{name}"))
        return "".join(chunks)

    def rollback(self, creator_id, version):
        folder, _ = self.load(creator_id, version)
        active = folder.parent / "active.json"
        previous = read_json(active)["version"] if active.exists() else None
        run = self.workspace.new_run(creator_id, "rollback")
        write_json(run / "rollback.json", {"from_version": previous, "to_version": version, "created_at": now()})
        write_json(active, {"version": version, "previous_version": previous, "updated_at": now(), "rollback_run_id": run.name})
        self.workspace.finish(run, "completed")
        return folder

    def export(self, creator_id, version, destination):
        folder, _ = self.load(creator_id, version)
        destination = Path(destination).resolve()
        if destination.exists():
            raise CreatorDNAError(f"导出目标已存在：{destination}，请指定新目录。")
        if destination == self.workspace.root or self.workspace.root in destination.parents:
            raise CreatorDNAError("导出目标请放在 workspace 之外。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = destination.with_name(f".{destination.name}-{uuid.uuid4().hex}")
        stage.mkdir()
        shutil.copy2(folder / "SKILL.md", stage / "SKILL.md")
        (stage / "references").mkdir()
        shutil.copy2(folder / "references" / "style-guide.md", stage / "references" / "style-guide.md")
        stage.rename(destination)
        return destination
