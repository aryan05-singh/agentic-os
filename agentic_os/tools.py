"""Tool layer — what the agent can *do*.

Every tool is a plain function plus a JSON-schema definition (the shape the
Messages API expects). The kernel owns the loop; this module owns execution.

Safety model:
  * shell runs through an approval gate — interactive chat asks the human,
    unattended (scheduler) runs allow it only if config.autonomous_shell
  * file tools are confined to the workspace directory (path traversal
    outside it is rejected)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .memory import Memory

TOOL_DEFINITIONS = [
    {
        "name": "run_shell",
        "description": (
            "Run a shell command on the host and return combined stdout/stderr. "
            "Call this when the task requires inspecting or acting on the system "
            "(files, processes, git, network checks)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file inside the workspace directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write/overwrite a text file inside the workspace directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Store a fact in persistent memory so future sessions know it. "
            "Call this when the user shares a preference, decision, or durable fact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Short kebab-case topic"},
                "content": {"type": "string", "description": "The fact to store"},
            },
            "required": ["topic", "content"],
        },
    },
    {
        "name": "recall",
        "description": "Search persistent memory by keywords and return matching entries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "2-5 keywords"},
            },
            "required": ["query"],
        },
    },
]


class ToolBox:
    """Executes tool calls. `approve` is injected by the interface layer:
    the chat REPL passes a TTY prompt, the scheduler passes a policy check."""

    def __init__(self, config: dict, memory: Memory, approve: Callable[[str], bool]):
        self.config = config
        self.memory = memory
        self.approve = approve
        self.workspace: Path = config["workspace"]

    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise KeyError(f"unknown tool: {name}")
        return handler(**args)

    # -- handlers ----------------------------------------------------------

    def _tool_run_shell(self, command: str) -> str:
        if not self.approve(command):
            return "DENIED: the operator did not approve this command."
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.config["shell_timeout"],
        )
        out = (proc.stdout + proc.stderr).strip()
        return f"exit={proc.returncode}\n{out[:8000]}" if out else f"exit={proc.returncode}"

    def _resolve(self, path: str) -> Path:
        target = (self.workspace / path).resolve()
        if not target.is_relative_to(self.workspace.resolve()):
            raise ValueError(f"path escapes workspace: {path}")
        return target

    def _tool_read_file(self, path: str) -> str:
        return self._resolve(path).read_text()[:32000]

    def _tool_write_file(self, path: str, content: str) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"wrote {len(content)} chars to {target}"

    def _tool_remember(self, topic: str, content: str) -> str:
        row_id = self.memory.remember(topic, content)
        return f"stored memory #{row_id} [{topic}]"

    def _tool_recall(self, query: str) -> str:
        rows = self.memory.recall(query)
        if not rows:
            return "no matching memories"
        return "\n".join(f"[{r['topic']}] {r['content']}" for r in rows)
