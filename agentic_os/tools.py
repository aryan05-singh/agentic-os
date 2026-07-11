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
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from .browser import BrowserSession
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
    {
        "name": "fetch_url",
        "description": (
            "Fetch a web page and return its readable text (scripts/styles stripped). "
            "Call this when the user shares a URL or the answer needs current web content. "
            "For pages that need JavaScript, clicking, or form input, use the browser_* "
            "tools instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL to fetch"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_goto",
        "description": (
            "Open a URL in a real headless browser and return a snapshot: title, "
            "visible text, and a numbered list of interactive elements. The browser "
            "session persists across calls, so you can navigate, then click and type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) URL to open"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": (
            "Click an element on the current browser page. Target is an element "
            "number from the latest snapshot (e.g. '3') or a Playwright selector "
            "(e.g. 'text=Sign in'). Returns the post-click snapshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element number from the snapshot, or a selector",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "browser_type",
        "description": (
            "Type into an input on the current browser page (clears it first). "
            "Target is an element number from the latest snapshot or a selector. "
            "Set press_enter to submit. Returns the post-typing snapshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Element number from the snapshot, or a selector",
                },
                "text": {"type": "string", "description": "Text to type"},
                "press_enter": {
                    "type": "boolean",
                    "description": "Press Enter after typing (submit)",
                },
            },
            "required": ["target", "text"],
        },
    },
    {
        "name": "browser_read",
        "description": (
            "Re-read the current browser page and return a fresh snapshot with "
            "numbered interactive elements. Use after the page changes on its own."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_screenshot",
        "description": (
            "Save a full-page screenshot of the current browser page into the "
            "workspace and return its path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename inside the workspace (default screenshot.png)",
                },
            },
        },
    },
]


class _TextExtractor(HTMLParser):
    """Collapse an HTML document to its visible text."""

    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


class ToolBox:
    """Executes tool calls. `approve` is injected by the interface layer:
    the chat REPL passes a TTY prompt, the scheduler passes a policy check."""

    def __init__(self, config: dict, memory: Memory, approve: Callable[[str], bool]):
        self.config = config
        self.memory = memory
        self.approve = approve
        self.workspace: Path = config["workspace"]
        self._browser: BrowserSession | None = None

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

    def _tool_fetch_url(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"only http(s) URLs are allowed: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "agentic-os/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(2_000_000)
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        text = raw.decode(charset, errors="replace")
        if "html" in content_type or text.lstrip()[:1] == "<":
            extractor = _TextExtractor()
            extractor.feed(text)
            text = "\n".join(extractor.parts)
        return text[:12000]

    # -- browser -------------------------------------------------------------

    @property
    def browser(self) -> BrowserSession:
        if self._browser is None:
            self._browser = BrowserSession(
                self.workspace, timeout=self.config["browser_timeout"]
            )
        return self._browser

    def close(self):
        if self._browser is not None:
            self._browser.close()
            self._browser = None

    def _tool_browser_goto(self, url: str) -> str:
        return self.browser.goto(url)

    def _tool_browser_click(self, target: str) -> str:
        return self.browser.click(target)

    def _tool_browser_type(self, target: str, text: str, press_enter: bool = False) -> str:
        return self.browser.type_text(target, text, press_enter)

    def _tool_browser_read(self) -> str:
        return self.browser.snapshot()

    def _tool_browser_screenshot(self, filename: str = "screenshot.png") -> str:
        return self.browser.screenshot(filename)
