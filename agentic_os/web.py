"""Web interface layer — a local dashboard anyone can open in a browser.

    python -m agentic_os.web --config config.yaml --port 8321

Same philosophy as the rest of the OS: no frameworks. A stdlib ThreadingHTTPServer
serves one page and three JSON endpoints:

    GET  /            the dashboard (chat + memory + scheduled tasks)
    POST /api/chat    {"message": ...} -> SSE stream of token/approval/done events
    POST /api/approve {"allow": bool}  -> resolves a pending shell approval
    GET  /api/state   agent identity, recent memories, task schedule

Shell approval works exactly like the CLI's y/N gate, but over HTTP: the kernel
blocks mid-turn, the browser shows an Allow/Deny dialog, and the answer flows
back through /api/approve. One conversation, one turn at a time.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import load_config
from .kernel import Kernel
from .llm import make_client
from .memory import Memory
from .scheduler import INTERVALS

APPROVAL_TIMEOUT = 300  # seconds before a pending shell approval auto-denies

STATIC_DIR = Path(__file__).parent / "static"


class ChatServer:
    """Owns the kernel, the memory, and the single active conversation."""

    def __init__(self, config: dict, client=None):
        self.config = config
        self.memory = Memory(config["memory_db"])
        self.kernel = Kernel(
            client or make_client(),
            config,
            self.memory,
            approve=self._approve,
            on_text=self._emit_token,
        )
        self.lock = threading.Lock()  # serializes turns + all memory access
        self._sse = None              # writer for the chat currently streaming
        self._pending: dict | None = None  # {"command", "event", "allow"}

    # -- SSE plumbing (only ever called from the thread holding self.lock) --

    def _send(self, event: dict) -> None:
        if self._sse is not None:
            self._sse(event)

    def _emit_token(self, text: str) -> None:
        self._send({"type": "token", "text": text})

    def _approve(self, command: str) -> bool:
        if not self.config["require_approval"]:
            return True
        pending = {"command": command, "event": threading.Event(), "allow": False}
        self._pending = pending
        self._send({"type": "approval", "command": command})
        granted = pending["event"].wait(APPROVAL_TIMEOUT) and pending["allow"]
        self._pending = None
        return granted

    def resolve_approval(self, allow: bool) -> bool:
        pending = self._pending
        if pending is None:
            return False
        pending["allow"] = allow
        pending["event"].set()
        return True

    # -- request-facing operations --

    def chat(self, message: str, sse_writer) -> None:
        with self.lock:
            self._sse = sse_writer
            try:
                self.kernel.run_turn(message)
                self._send({"type": "done"})
            except Exception as e:  # noqa: BLE001 — surface the failure to the page
                self._send({"type": "error", "text": str(e)})
            finally:
                self._sse = None

    def state(self) -> dict:
        with self.lock:
            memories = self.memory.recent(limit=20)
        state_path = self.config["workspace"] / ".scheduler_state.json"
        last_runs = json.loads(state_path.read_text()) if state_path.exists() else {}
        now = time.time()
        tasks = []
        for task in self.config["tasks"]:
            interval = INTERVALS.get(task.get("every", "daily"), 86400)
            last = last_runs.get(task["name"], 0)
            tasks.append(
                {
                    "name": task["name"],
                    "every": task.get("every", "daily"),
                    "last_run": last or None,
                    "due": now - last >= interval,
                }
            )
        return {
            "name": self.config["name"],
            "model": self.config["model"],
            "memories": memories,
            "tasks": tasks,
        }


def make_handler(server: ChatServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                body = (STATIC_DIR / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json(server.state())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/api/chat":
                message = self._read_body().get("message", "").strip()
                if not message:
                    self._json({"error": "empty message"}, 400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def write_event(event: dict) -> None:
                    try:
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # browser went away; let the turn finish quietly

                server.chat(message, write_event)
            elif self.path == "/api/approve":
                allow = bool(self._read_body().get("allow"))
                resolved = server.resolve_approval(allow)
                self._json({"resolved": resolved})
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="agentic-os web dashboard")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", type=int, default=8321)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    config = load_config(args.config)
    chat_server = ChatServer(config)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(chat_server))
    print(f"{config['name']} dashboard: http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        chat_server.memory.close()


if __name__ == "__main__":
    main()
