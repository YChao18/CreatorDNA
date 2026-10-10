"""Local, single-writer task engine with cancellable isolated stage workers."""

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .contracts import validate
from .errors import CreatorDNAError
from .storage import Workspace, digest_file, native_path, now, read_json, validate_id, write_json
from .task_progress import progress


STAGES = {
    "script": "文案生成与可选评估",
    "material_analysis": "素材分析", "tts": "TTS 语音合成", "lip_sync": "人像口型同步",
    "composition": "字幕与场景编排", "render": "视频渲染", "final_check": "成品检查",
}
TERMINAL = {"completed", "failed", "cancelled"}
BUILTINS = {name: f"creator_dna.task_executors:{name}" for name in
            ("local_chunks", "check_chunks", "ffmpeg_demo", "check_video", "analyze_video")}
BUILTINS.update(cloud_tts="creator_dna.tts_workflow:synthesize_stage",
                cloud_voice="creator_dna.tts_workflow:register_voice",
                local_lip_sync="creator_dna.lip_sync_workflow:generate_stage",
                check_lip_sync="creator_dna.lip_sync_workflow:check_stage")
BUILTINS.update(video_script="creator_dna.video_workflow:script_stage",
                video_tts="creator_dna.video_workflow:tts_stage",
                video_lipsync="creator_dna.video_workflow:lip_stage",
                video_composition="creator_dna.video_workflow:composition_stage",
                video_rebuild="creator_dna.video_workflow:rebuild_stage",
                video_render="creator_dna.render:render_stage",
                video_verify="creator_dna.render:verify_stage")


def stop_process_tree(process):
    """Workers own subprocesses; stop the whole group/tree, including FFmpeg."""
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def stage(stage_id, executor, params=None, *, timeout=600, max_attempts=1,
          retry_safe=False, retry_delay=0.2):
    return {"id": stage_id, "executor": executor, "params": params or {}, "timeout": timeout,
            "max_attempts": max_attempts, "retry_safe": retry_safe, "retry_delay": retry_delay}


class TaskManager:
    def __init__(self, workspace, executors=None, heartbeat=0.5):
        self.workspace = workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        self.root = native_path(self.workspace.root / "tasks")
        self.executors = {**BUILTINS, **(executors or {})}
        if not math.isfinite(heartbeat) or heartbeat <= 0:
            raise CreatorDNAError("heartbeat 须大于 0。")
        self.heartbeat = heartbeat

    def folder(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"task-[0-9a-f]{32}", task_id):
            raise CreatorDNAError("Task ID 格式无效。")
        folder = self.root / task_id
        if not folder.is_dir():
            raise CreatorDNAError(f"任务不存在：{task_id}")
        return folder

    @contextmanager
    def _lock(self, folder):
        path = folder / ".lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise _TaskBusy("任务已有执行进程或残留锁；不要并发运行。强制退出后须确认父/子进程均已停止再移除 .lock。") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "created_at": now()}, stream)
            yield
        finally:
            path.unlink(missing_ok=True)

    def create(self, stages, creator_id=None, materials=None, kind="custom"):
        if not stages or len({s["id"] for s in stages}) != len(stages):
            raise CreatorDNAError("任务须包含阶段且阶段 ID 不得重复。")
        plan = []
        for spec in stages:
            spec = dict(spec)
            if spec["id"] not in STAGES or spec["executor"] not in self.executors:
                raise CreatorDNAError("未知阶段或未注册执行器；未实现业务阶段不能作为空任务执行。")
            for key in ("timeout", "retry_delay"):
                value = spec[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise CreatorDNAError(f"{key} 须为非负有限数值。")
            if spec["timeout"] == 0 or isinstance(spec["max_attempts"], bool) or not isinstance(spec["max_attempts"], int) or not 1 <= spec["max_attempts"] <= 10:
                raise CreatorDNAError("timeout 须大于 0；max_attempts 须为 1–10 的整数。")
            if not isinstance(spec["retry_safe"], bool) or (spec["max_attempts"] > 1 and not spec["retry_safe"]):
                raise CreatorDNAError("自动重试须显式声明 retry_safe=true。")
            spec["handler"] = self.executors[spec["executor"]]
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", spec["handler"]):
                raise CreatorDNAError("执行器须为可导入的 module:function。")
            plan.append(spec)
        creator_id = validate_id(creator_id) if creator_id else None
        task_id = "task-" + uuid.uuid4().hex
        definition = {"schema_version": "3.0-02", "task_id": task_id, "kind": kind,
                      "creator_id": creator_id, "materials": materials or [], "stages": plan,
                      "created_at": now()}
        # Validate JSON serializability before creating an observable task.
        json.dumps(definition, allow_nan=False)
        folder = self.root / task_id
        state = {"schema_version": "3.0-02", "task_id": task_id, "kind": kind,
                 "creator_id": creator_id, "materials": materials or [],
                 "created_at": definition["created_at"], "started_at": None, "finished_at": None,
                 "run_started_at": None, "updated_at": now(), "status": "pending",
                 "current_stage": None, "current_object": None, "elapsed_seconds": 0,
                 "stage_elapsed_seconds": 0, "message": "任务已创建，等待执行", "error": None,
                 "last_error": None, "cancel_requested": False, "worker_pid": None,
                 "runner_pid": None, "progress": progress(), "overall_progress": progress(),
                 "output_files": [], "log_file": str(folder / "logs.jsonl"),
                 "stages": [{"id": s["id"], "name": STAGES[s["id"]], "status": "pending",
                             "started_at": None, "finished_at": None, "attempt": 0,
                             "attempts": [], "progress": progress(), "artifacts": [], "result": None}
                            for s in plan]}
        validate(state, "task-status")
        folder.mkdir(parents=True)
        write_json(folder / "task.json", definition)
        self._save(folder, state)
        self._log(folder, state, "created", state["message"])
        return state

    def _log(self, folder, state, event, message, level="info", data=None):
        index = next((s for s in state["stages"] if s["id"] == state["current_stage"]), None)
        record = {"timestamp": now(), "task_id": state["task_id"], "level": level,
                  "stage": state["current_stage"], "attempt": index["attempt"] if index else None,
                  "event": event, "message": message, "data": data or {}}
        with (folder / "logs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def _save(self, folder, state):
        state["updated_at"] = now()
        complete = sum(s["status"] == "completed" for s in state["stages"])
        if state["status"] == "completed":
            state["overall_progress"] = progress(len(state["stages"]), len(state["stages"]),
                                                  "stages", "all stages verified")
        elif state["kind"] != "video-production" and state["status"] not in {"failed", "cancelled"} and state["progress"]["percent"] is not None:
            fraction = state["progress"]["percent"] / 100 if state["current_stage"] and next(
                s for s in state["stages"] if s["id"] == state["current_stage"])["status"] != "completed" else 0
            state["overall_progress"] = progress(complete+fraction, len(state["stages"]), "stage_equivalents",
                                                  "equal stage weights; does not predict time remaining", True)
        else:
            state["overall_progress"] = progress(complete, unit="stages",
                                                  basis="completed stage count; current work total unknown")
        state["cancel_requested"] = (folder / "cancel.json").exists()
        validate(state, "task-status")
        write_json(folder / "status.json", state)

    def status(self, task_id):
        folder = self.folder(task_id)
        state = read_json(folder / "status.json")
        validate(state, "task-status")
        state["cancel_requested"] = (folder / "cancel.json").exists()
        return state

    def history(self, creator_id=None, status=None, limit=20):
        if limit < 1:
            raise CreatorDNAError("limit 须大于 0。")
        states = [self.status(p.name) for p in self.root.glob("task-*") if p.is_dir()] if self.root.exists() else []
        return sorted((s for s in states if (creator_id is None or s["creator_id"] == creator_id)
                       and (status is None or s["status"] == status)), key=lambda s: s["created_at"], reverse=True)[:limit]

    def logs(self, task_id, tail=100):
        if tail < 1:
            raise CreatorDNAError("tail 须大于 0。")
        from collections import deque
        path = self.folder(task_id) / "logs.jsonl"
        with path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in deque(stream, maxlen=tail) if line.strip()]

    def cancel(self, task_id):
        folder = self.folder(task_id)
        state = self.status(task_id)
        if state["status"] in TERMINAL:
            return state
        write_json(folder / "cancel.json", {"requested_at": now(), "reason": "用户取消"})
        # Running writer observes the marker. Pending cancellation can finish here.
        try:
            with self._lock(folder):
                state = self.status(task_id)
                if state["status"] not in TERMINAL:
                    state.update(status="cancelled", finished_at=now(), message="任务已取消",
                                 error={"type": "Cancelled", "message": "用户取消任务", "retryable": False})
                    self._log(folder, state, "cancelled", state["message"])
                    self._save(folder, state)
        except _TaskBusy:
            pass  # Only runner writes running state; cancellation marker is durable.
        return self.status(task_id)

    def _verify_checkpoints(self, state):
        for item in state["stages"]:
            if item["status"] == "completed":
                for artifact in item["artifacts"]:
                    path = Path(artifact["path"])
                    if not path.is_file() or path.is_symlink() or digest_file(path) != artifact["sha256"]:
                        raise CreatorDNAError(f"已完成阶段的文件缺失或改变，拒绝复用：{path}")

    def run(self, task_id, *, resume=False, observer=None):
        folder = self.folder(task_id)
        with self._lock(folder):
            state = self.status(task_id)
            definition = read_json(folder / "task.json")
            self._verify_checkpoints(state)
            if state["status"] == "completed":
                return state  # Idempotent, never overwrite completed deliverables.
            if state["status"] != "pending" and not resume:
                raise CreatorDNAError("非 pending 任务须显式 --resume；恢复只复用已校验完成的阶段。")
            remaining = [s for s in state["stages"] if s["status"] != "completed"]
            if resume and any(not spec["retry_safe"] for spec in definition["stages"] if spec["id"] in {s["id"] for s in remaining}):
                raise CreatorDNAError("未完成阶段未声明安全重复执行，不能恢复；请先核验外部副作用。")
            if resume:
                (folder / "cancel.json").unlink(missing_ok=True)
                for item in remaining:
                    # Preserve abrupt-interruption evidence, including old worker PID in logs.
                    if item["attempts"] and item["attempts"][-1]["status"] == "running":
                        item["attempts"][-1].update(status="failed", finished_at=now(), error={
                            "type": "Interrupted", "message": "上次执行未写出结束记录", "retryable": True})
                    item.update(status="pending", finished_at=None)
            baseline, tick = state["elapsed_seconds"], time.monotonic()
            state.update(status="running", started_at=state["started_at"] or now(), run_started_at=now(),
                         finished_at=None, error=None, runner_pid=os.getpid(), message="开始执行任务")
            self._log(folder, state, "resumed" if resume else "started", state["message"])
            def update():
                state["elapsed_seconds"] = round(baseline + time.monotonic()-tick, 3)
                self._save(folder, state)
                if observer:
                    observer(state)
            update()
            try:
                for spec, item in zip(definition["stages"], state["stages"]):
                    if item["status"] == "completed":
                        continue
                    state.update(current_stage=item["id"], current_object=None, progress=progress())
                    for retry in range(spec["max_attempts"]):
                        if (folder / "cancel.json").exists():
                            raise _Cancelled()
                        error = self._attempt(folder, definition, state, spec, item, update)
                        if error is None:
                            break
                        state.update(error=error, last_error=error)
                        if error["type"] == "Cancelled":
                            raise _Cancelled()
                        if not error["retryable"] or not spec["retry_safe"] or retry+1 >= spec["max_attempts"]:
                            raise _Failed(error)
                        state["message"] = f"阶段失败，准备重试 {retry+1}/{spec['max_attempts']-1}：{error['message']}"
                        self._log(folder, state, "retry", state["message"], "warning", error)
                        deadline = time.monotonic()+spec["retry_delay"]
                        while time.monotonic() < deadline:
                            if (folder / "cancel.json").exists():
                                raise _Cancelled()
                            update()
                            time.sleep(min(self.heartbeat, max(0, deadline-time.monotonic())))
                    state["error"] = None
                state.update(status="completed", message="全部阶段完成并校验")
            except (_Cancelled, KeyboardInterrupt):
                state.update(status="cancelled", message="任务已取消", error={
                    "type": "Cancelled", "message": "用户取消任务", "retryable": False})
                for item in state["stages"]:
                    if item["status"] in {"running", "failed"} and item["id"] == state["current_stage"]:
                        item.update(status="cancelled", finished_at=now())
            except _Failed as exc:
                state.update(status="failed", message=exc.error["message"], error=exc.error)
            except Exception as exc:
                state.update(status="failed", message=str(exc), error={
                    "type": type(exc).__name__, "message": str(exc), "retryable": False})
                self._log(folder, state, "engine_error", str(exc), "error")
            finally:
                state.update(finished_at=now(), worker_pid=None, runner_pid=None)
                state["output_files"] = [a["path"] for s in state["stages"] if s["status"] == "completed" for a in s["artifacts"]]
                self._log(folder, state, state["status"], state["message"], "error" if state["status"] == "failed" else "info")
                update()
            return state

    def _attempt(self, folder, definition, state, spec, item, update):
        item["attempt"] += 1
        attempt_dir = folder / "stages" / item["id"] / f"attempt-{item['attempt']:03d}"
        attempt_dir.mkdir(parents=True)
        inputs = {s["id"]: {"artifacts": s["artifacts"], "result": s["result"]}
                  for s in state["stages"] if s["status"] == "completed"}
        write_json(attempt_dir / "request.json", {"task_id": state["task_id"],
            "stage_id": item["id"], "timeout": spec["timeout"], "previous_attempts": item["attempts"],
            "creator_id": state["creator_id"], "materials": definition["materials"],
            "attempt": item["attempt"], "handler": spec["handler"], "params": spec["params"],
            "cancel_file": str(folder / "cancel.json"), "inputs": inputs})
        item.update(status="running", started_at=now(), finished_at=None, progress=progress())
        record = {"attempt": item["attempt"], "started_at": now(), "finished_at": None,
                  "status": "running", "error": None, "directory": str(attempt_dir)}
        item["attempts"].append(record)
        state.update(message=f"开始 {item['name']}，尝试 {item['attempt']}", progress=progress(), error=None,
                     stage_elapsed_seconds=0)
        self._log(folder, state, "stage_started", state["message"])
        update()
        process, error, position = None, None, 0
        started, last_save = time.monotonic(), 0
        try:
            with (attempt_dir / "stdout.log").open("wb") as stdout, (attempt_dir / "stderr.log").open("wb") as stderr:
                process = subprocess.Popen([sys.executable, "-m", "creator_dna.task_worker", str(attempt_dir)],
                                           stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                           start_new_session=os.name != "nt")
                state["worker_pid"] = process.pid
                while True:
                    state["stage_elapsed_seconds"] = round(time.monotonic()-started, 3)
                    events = attempt_dir / "events.jsonl"
                    if events.exists():
                        with events.open(encoding="utf-8") as stream:
                            stream.seek(position)
                            while True:
                                before = stream.tell()
                                line = stream.readline()
                                if not line or not line.endswith("\n"):
                                    position = before
                                    break
                                event = json.loads(line)
                                state["message"] = event["message"]
                                if event["event"] == "progress":
                                    p = event["progress"]
                                    # Recompute rather than trusting provider-supplied percent.
                                    p = progress(p["completed"], p["total"], p["unit"], p["basis"], p["estimated"])
                                    state.update(progress=p, current_object=event["current_object"])
                                    item["progress"] = p
                                self._log(folder, state, event["event"], event["message"], event.get("level", "info"), event)
                    if (folder / "cancel.json").exists():
                        error = {"type": "Cancelled", "message": "用户取消任务", "retryable": False}
                        break
                    if time.monotonic()-started >= spec["timeout"]:
                        error = {"type": "TaskTimeout", "message": f"{item['name']} 超过 {spec['timeout']} 秒超时边界", "retryable": True}
                        break
                    if time.monotonic()-last_save >= self.heartbeat:
                        update()
                        last_save = time.monotonic()
                    if process.poll() is not None:
                        # Read any final flushed events on the next iteration before exiting.
                        if not getattr(process, "_task_events_drained", False):
                            process._task_events_drained = True
                            continue
                        result_path = attempt_dir / "result.json"
                        if not result_path.exists():
                            error = {"type": "ExecutorExited", "message": f"执行器退出码 {process.returncode}，缺少结果；见 {attempt_dir / 'stderr.log'}", "retryable": False}
                        else:
                            result = read_json(result_path)
                            error = result.get("error")
                            if not error and (process.returncode != 0 or result.get("status") != "completed"):
                                error = {"type": "ExecutorExited", "message": f"执行器退出码 {process.returncode}", "retryable": False}
                        break
                    time.sleep(min(0.05, self.heartbeat))
            if error is None:
                output_dir = attempt_dir / "outputs"
                artifacts = []
                for path in sorted(output_dir.rglob("*")):
                    if path.is_symlink() or output_dir.resolve() not in path.resolve().parents:
                        raise CreatorDNAError("执行器输出包含链接或越界路径，拒绝发布。")
                    if path.is_file():
                        h, hashed = hashlib.sha256(), 0
                        with path.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024*1024), b""):
                                if (folder / "cancel.json").exists():
                                    raise _Cancelled()
                                if time.monotonic()-started >= spec["timeout"]:
                                    raise _Failed({"type": "TaskTimeout", "message": "输出校验超过阶段超时边界", "retryable": True})
                                h.update(chunk)
                                hashed += len(chunk)
                                if time.monotonic()-last_save >= self.heartbeat:
                                    state.update(current_object=path.name, message=f"校验输出文件：{path.name}",
                                                 stage_elapsed_seconds=round(time.monotonic()-started, 3),
                                                 progress=progress(hashed, unit="bytes", basis="artifact hashing; stage total unknown"))
                                    item["progress"] = state["progress"]
                                    update()
                                    last_save = time.monotonic()
                        artifacts.append({"path": str(path), "sha256": h.hexdigest(), "size": hashed})
                # Immutable attempt outputs + manifest checkpoint; no shared target overwrite.
                item.update(status="completed", finished_at=now(), artifacts=artifacts, result=result["metadata"])
                state["message"] = f"{item['name']} 完成，已校验 {len(artifacts)} 个输出文件"
                item["progress"] = state["progress"] = progress(1, 1, "stage", "executor exited successfully; artifacts hashed")
                self._log(folder, state, "stage_completed", state["message"])
        except (KeyboardInterrupt, _Cancelled):
            error = {"type": "Cancelled", "message": "用户取消任务", "retryable": False}
        except _Failed as exc:
            error = exc.error
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "retryable": False}
        finally:
            if process is not None and (process.poll() is None or error is not None):
                stop_process_tree(process)
            state["worker_pid"] = None
        record.update(status=("cancelled" if error["type"] == "Cancelled" else "failed") if error else "completed",
                      finished_at=now(), error=error)
        if error:
            item.update(status="cancelled" if error["type"] == "Cancelled" else "failed", finished_at=now())
            self._log(folder, state, "stage_failed", error["message"], "error", error)
        state["output_files"] = [a["path"] for s in state["stages"] if s["status"] == "completed" for a in s["artifacts"]]
        update()
        return error


class _Cancelled(Exception):
    pass


class _TaskBusy(CreatorDNAError):
    pass


class _Failed(Exception):
    def __init__(self, error):
        self.error = error
