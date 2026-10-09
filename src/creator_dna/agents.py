import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .contracts import validate
from .errors import CreatorDNAError
from .storage import write_json, write_text


@dataclass(frozen=True)
class AgentRequest:
    task: str
    prompt: str
    schema_name: str
    output_schema: dict
    directory: Path


class AgentAdapter(Protocol):
    def run(self, request: AgentRequest) -> dict: ...


def codex_command(binary="codex"):
    executable = shutil.which(binary)
    if not executable:
        raise CreatorDNAError(f"找不到 Codex CLI：{binary}。安装并登录 CLI，或用 --codex-bin 指定可执行文件。")
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # Invoke the official npm entrypoint directly; never interpolate prompts into cmd.exe.
        script = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node")
        if not script.is_file() or not node:
            raise CreatorDNAError("此 Codex shell wrapper 无法直接启动。请用 --codex-bin 指定原生 codex.exe。")
        return [node, str(script)]
    return [str(path)]


class CodexAdapter:
    def __init__(self, binary="codex", model=None, timeout=900):
        self.binary, self.model, self.timeout = binary, model, timeout

    @staticmethod
    def stop_process(process):
        if os.name == "nt":
            # npm starts a native Codex child. Stop this invocation's entire tree.
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)

    def run(self, request):
        directory = request.directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        write_text(directory / "AGENTS.md", "This is an isolated CreatorDNA inference request. All necessary data is in stdin.\n"
                   "Return the requested JSON only. Do not execute commands, use tools, spawn agents, or access other files.\n")
        write_text(directory / "prompt.txt", request.prompt)
        write_json(directory / "schema.json", request.output_schema)
        result_path = directory / "response.json"
        args = codex_command(self.binary) + ["exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                "--color", "never", "--cd", str(directory), "--output-schema", str(directory / "schema.json"),
                "--output-last-message", str(result_path)]
        if self.model:
            args.extend(["--model", self.model])
        args.append("-")
        try:
            with (directory / "stdout.log").open("w", encoding="utf-8") as stdout, (directory / "stderr.log").open("w", encoding="utf-8") as stderr:
                process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                           text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt")
                try:
                    process.communicate(input=request.prompt, timeout=self.timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    self.stop_process(process)
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    raise CreatorDNAError(f"Codex 超过 {self.timeout} 秒；已停止本次进程并保留请求，可重新运行。") from exc
        except OSError as exc:
            raise CreatorDNAError(f"Codex 无法启动：{exc}") from exc
        if process.returncode:
            raise CreatorDNAError(f"Codex 返回 {process.returncode}，详情：{directory / 'stderr.log'}")
        if not result_path.is_file():
            raise CreatorDNAError(f"Codex 未写入结果：{result_path}")
        try:
            response = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise CreatorDNAError(f"Codex 结果不是有效 JSON：{result_path}") from exc
        validate(response, request.schema_name)
        return response
