"""Opt-in real Codex integration; uses a copied approved package, never fixture responses.

Requires an installed/logged-in Codex CLI and an approved source Skill. Consumes
3 calls at most for the revision smoke test (1 evaluation + 1 revision + 1 evaluation);
--batch adds 4 calls. Scores are observations, never acceptance assertions.
"""
import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

from creator_dna.agents import CodexAdapter
from creator_dna.contracts import validate
from creator_dna.quality import QualityWorkflow
from creator_dna.skills import SkillManager
from creator_dna.storage import Workspace, digest_file, read_json, write_json


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-workspace", type=Path, required=True)
    parser.add_argument("--creator", required=True)
    parser.add_argument("--version")
    parser.add_argument("--output", type=Path, help="必须不存在的新目录；默认 workspace/integration-UUID")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--batch", action="store_true")
    args = parser.parse_args()
    source = Workspace(args.source_workspace)
    folder, metadata = SkillManager(source).load(args.creator, args.version)
    before = {name: digest_file(folder / name) for name in [*metadata["files"], "metadata.json"]}
    target = args.output or Path("workspace") / f"integration-{uuid.uuid4().hex}"
    if target.exists():
        parser.error("输出目录必须不存在，防止覆盖数据。")
    workspace = Workspace(target)
    destination = workspace.creator(args.creator) / "skills" / folder.name
    # Only copy the portable package and metadata, not video, ASR cache or previous runs.
    destination.mkdir(parents=True)
    for name in [*metadata["files"], "metadata.json"]:
        (destination / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / name, destination / name)
    write_json(destination.parent / "active.json", {"version": folder.name})

    class CountingCodex(CodexAdapter):
        calls = 0

        def run(self, request):
            self.calls += 1
            print(f"真实 Agent 调用 {self.calls}：{request.task}", flush=True)
            return super().run(request)

    agent = CountingCodex(args.codex_bin, args.model, args.timeout)
    quality = QualityWorkflow(workspace, agent)
    # Explicitly synthetic, intentionally generic input; not a real creator sample.
    script = "今天讨论文件整理。文件需要整理，工具也需要整理。做好文件管理可以让工作更顺利。大家要重视这个问题。"
    result = quality.cycle(args.creator, "为什么给工作文件固定位置更容易找回", folder.name,
                           brief="日常方法建议，无需数据或研究引用；素材和起始稿均为合成演示。",
                           max_revisions=1, script=script)
    summary = {"material_type": "source package selected by caller; initial draft is synthetic",
               "quality_run": result, "calls": agent.calls, "real_creator_quality_verified": False}
    if args.batch:
        dataset = {"schema_version": "2.0", "id": "integration-synthetic-development", "purpose": "development", "cases": [
            {"id": "regular", "topic": "为什么把待办写清楚比换工具更有用", "category": "regular",
             "brief": "只给日常建议，不编造研究或数据。合成演示选题。", "human_reference": None},
            {"id": "cross", "topic": "为什么阳台养花先观察光照再买花盆", "category": "cross_topic",
             "brief": "用假设场景解释选择顺序，避免具体植物养护事实。合成演示选题。", "human_reference": None},
        ]}
        summary["batch"] = quality.batch(args.creator, dataset, folder.name)
        batch = read_json(Path(summary["batch"]["report"]))
        validate(batch, "batch-report")
    summary["calls"] = agent.calls
    summary["source_unchanged"] = before == {name: digest_file(folder / name) for name in before}
    summary["engineering_integration_ok"] = result["status"] in {"passed", "failed"} and summary["source_unchanged"]
    if args.batch:
        summary["engineering_integration_ok"] &= batch["summary"]["unable"] == 0
    write_json(workspace.root / "integration-summary.json", summary)
    print(json.dumps({"summary": str(workspace.root / "integration-summary.json"), **summary}, ensure_ascii=False, indent=2))
    return 0 if summary["engineering_integration_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
