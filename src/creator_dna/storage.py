import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import CreatorDNAError


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CreatorDNAError(f"无法读取 JSON {path}: {exc}") from exc


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        # Windows readers briefly deny replacement while holding status.json.
        # Keep the previous valid file visible; retry only this bounded OS race.
        for attempt in range(7):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 6:
                    raise
                time.sleep(min(0.01 * 2**attempt, 0.1))
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def native_path(path):
    """Absolute Windows extended path for deeply nested task artifacts."""
    path = Path(path).resolve()
    value = str(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\"+value[2:] if value.startswith("\\\\") else "\\\\?\\"+value
    return Path(value)


def validate_id(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", value) or value.split(".")[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        raise CreatorDNAError("creator ID 须为 1–48 位小写字母、数字或连字符，且不能是系统保留名称。")
    return value


def default_id(folder):
    folder = Path(folder).resolve()
    base = re.sub(r"[^a-z0-9]+", "-", folder.name.lower()).strip("-")[:35] or "creator"
    return validate_id(f"{base}-{hashlib.sha256(str(folder).encode()).hexdigest()[:8]}")


def validate_version(value):
    if not re.fullmatch(r"v[0-9]{3,}", value):
        raise CreatorDNAError("版本号格式应为 v001、v002 等。")
    return value


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def creator(self, creator_id):
        return self.root / "creators" / validate_id(creator_id)

    @contextmanager
    def lock(self, creator_id):
        folder = self.creator(creator_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ".lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise CreatorDNAError(f"该创作者已有任务运行：{path}。若进程已终止，请确认后删除残留锁文件。") from exc
        try:
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            yield
        finally:
            path.unlink(missing_ok=True)

    def new_run(self, creator_id, task):
        path = self.creator(creator_id) / "runs" / f"{task}-{uuid.uuid4().hex}"
        path.mkdir(parents=True)
        write_json(path / "run.json", {"schema_version": "1.0", "task": task,
                   "creator_id": creator_id, "status": "running", "started_at": now()})
        return path

    def finish(self, run, status, **fields):
        data = read_json(run / "run.json")
        data.update(status=status, finished_at=now(), **fields)
        write_json(run / "run.json", data)
