"""Repeatable local engine acceptance; only synthetic files/media, no models."""

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from creator_dna.storage import write_json
from creator_dna.task_cli import TerminalProgress
from creator_dna.tasks import TaskManager, stage, stop_process_tree


def main():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="V3.0-02 本地工程验证（不代表创作者或云模型质量）")
    parser.add_argument("--workspace", type=Path, default=Path("workspace/v3-02-validation"))
    args = parser.parse_args()
    manager = TaskManager(args.workspace, heartbeat=0.1)
    evidence = {"synthetic_only": True, "cloud_calls": 0, "tasks": [], "observations": []}
    display = TerminalProgress()

    def observe(state):
        display(state)
        evidence["observations"].append({key: state[key] for key in
            ("task_id", "status", "current_stage", "current_object", "elapsed_seconds", "progress", "overall_progress", "message")})

    def execute(name, specs, expected):
        task = manager.create(specs, "v3-task-synthetic", kind=name)
        print(f"\n{name}: {task['task_id']}", file=sys.stderr)
        result = manager.run(task["task_id"], observer=observe)
        assert result["status"] == expected, result
        assert manager.status(task["task_id"])["status"] == expected
        evidence["tasks"].append({"case": name, "task_id": task["task_id"], "status": result["status"],
                                  "status_file": str(manager.folder(task["task_id"]) / "status.json"),
                                  "log_file": result["log_file"], "outputs": result["output_files"]})
        return result

    execute("local-demo", [stage("material_analysis", "local_chunks", {"chunks": 8, "delay": 0.1}, retry_safe=True),
                           stage("final_check", "check_chunks", retry_safe=True)], "completed")
    execute("ffmpeg-demo", [stage("render", "ffmpeg_demo", {"duration": 2}, timeout=30, retry_safe=True),
                            stage("final_check", "check_video", timeout=30, retry_safe=True)], "completed")
    retried = execute("bounded-retry", [stage("material_analysis", "local_chunks", {"chunks": 2, "delay": 0, "fail_until": 1},
                                            retry_safe=True, max_attempts=2)], "completed")
    assert retried["stages"][0]["attempt"] == 2
    assert all("attempt-002" in p for p in retried["output_files"])
    failed = execute("failure", [stage("material_analysis", "local_chunks", {"fail_until": 10},
                                       retry_safe=True, max_attempts=2)], "failed")
    assert not failed["output_files"] and failed["stages"][0]["attempt"] == 2
    timeout = execute("timeout", [stage("material_analysis", "local_chunks", {"chunks": 10, "delay": 1},
                                       timeout=0.8, retry_safe=True)], "failed")
    assert timeout["error"]["type"] == "TaskTimeout"

    # Run and cancel through two independent CLI processes, preserving terminal logs.
    task = manager.create([stage("material_analysis", "local_chunks", {"chunks": 30, "delay": 0.2}, retry_safe=True)],
                          "v3-task-synthetic", kind="cross-process-cancel")
    folder = manager.folder(task["task_id"])
    command = [sys.executable, "-m", "creator_dna", "tasks"]
    common = ["--workspace", str(manager.workspace.root), "--task", task["task_id"]]
    with (folder / "cli-result.json").open("wb") as stdout, (folder / "terminal.log").open("wb") as stderr:
        process = subprocess.Popen([*command, "run", *common, "--interval", "0.1"], stdout=stdout, stderr=stderr,
                                   start_new_session=sys.platform != "win32")
        try:
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                state = manager.status(task["task_id"])
                if state["progress"]["completed"] and state["status"] == "running":
                    break
                if process.poll() is not None:
                    raise RuntimeError("CLI 执行进程提前结束")
                time.sleep(0.05)
            else:
                raise RuntimeError("CLI 进度更新超时")
            cancelled = subprocess.run([*command, "cancel", *common], capture_output=True, text=True,
                                       encoding="utf-8", check=True, timeout=10)
            assert json.loads(cancelled.stdout)["cancel_requested"]
            assert process.wait(timeout=10) == 130
        finally:
            if process.poll() is None:
                stop_process_tree(process)
    result = manager.status(task["task_id"])
    assert result["status"] == "cancelled" and not result["output_files"]
    evidence["tasks"].append({"case": "cross-process-cancel", "task_id": task["task_id"], "status": result["status"],
                              "status_file": str(folder / "status.json"), "log_file": result["log_file"],
                              "terminal_log": str(folder / "terminal.log"), "outputs": []})
    summary = manager.workspace.root / f"validation-{uuid.uuid4().hex}.json"
    write_json(summary, evidence)
    print(json.dumps({"summary": str(summary), "cases": len(evidence["tasks"]), "synthetic_only": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
