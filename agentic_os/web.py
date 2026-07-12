"""Web interface layer — a local dashboard anyone can open in a browser.

    python -m agentic_os.web --config config.yaml --port 8321

Same philosophy as the rest of the OS: no frameworks. A stdlib ThreadingHTTPServer
serves one page and three JSON endpoints:

    GET  /                 the dashboard (chat + memory + tasks + content)
    POST /api/chat         {"message": ...} -> SSE stream of token/approval/done events
    POST /api/approve      {"allow": bool}  -> resolves a pending shell approval
    GET  /api/state        identity, memories, todos, tasks, content, settings, queue
    POST /api/todos        {"text": ...}    -> add a today-task
    POST /api/todos/toggle {"id": ...}      -> toggle a today-task done/undone
    POST /api/content      {"topic": ...}   -> generate a short post, store + return it

Recent Mail is backed by a real (read-only) Gmail OAuth connection when
gmail_client_id/secret are set in config; Expenses stays an honest empty-state
panel (parsing spend out of email is out of scope). See:

    GET  /api/gmail/connect   redirect to Google's consent screen
    GET  /api/gmail/callback  OAuth redirect target, saves the token

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
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .gmail import GmailClient
from .kernel import Kernel
from .llm import make_client, request_params
from .memory import Memory
from .scheduler import INTERVALS

APPROVAL_TIMEOUT = 300  # seconds before a pending shell approval auto-denies

STATIC_DIR = Path(__file__).parent / "static"


class ChatServer:
    """Owns the kernel, the memory, and the single active conversation."""

    def __init__(self, config: dict, client=None, base_url: str = "http://127.0.0.1:8321"):
        self.config = config
        self.memory = Memory(config["memory_db"])
        self.gmail = GmailClient(config, base_url)
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

    # -- todos (Tasks · Today panel) — a plain JSON file in the workspace --

    @property
    def _todos_path(self) -> Path:
        return self.config["workspace"] / "todos.json"

    def _load_todos(self) -> list[dict]:
        if self._todos_path.exists():
            return json.loads(self._todos_path.read_text())
        return []

    def add_todo(self, text: str) -> dict:
        with self.lock:
            todos = self._load_todos()
            todo = {
                "id": max((t["id"] for t in todos), default=0) + 1,
                "text": text,
                "done": False,
                "created_at": int(time.time()),
            }
            todos.append(todo)
            self._todos_path.write_text(json.dumps(todos))
        return todo

    def toggle_todo(self, todo_id: int) -> bool:
        with self.lock:
            todos = self._load_todos()
            for todo in todos:
                if todo["id"] == todo_id:
                    todo["done"] = not todo["done"]
                    self._todos_path.write_text(json.dumps(todos))
                    return True
        return False

    # -- content system — one-shot post generation, separate from chat history --

    @property
    def _content_path(self) -> Path:
        return self.config["workspace"] / "content.json"

    def _load_content(self) -> list[dict]:
        if self._content_path.exists():
            return json.loads(self._content_path.read_text())
        return []

    def generate_content(self, topic: str) -> dict:
        with self.lock:
            system = (
                f"You write short, punchy social posts for {self.config['owner']}. "
                "Return only the post text: no preamble, no hashtag spam, under 280 characters."
            )
            messages = [{"role": "user", "content": f"Write a post about: {topic}"}]
            params = request_params(self.config, system, messages, [])
            with self.kernel.client.messages.stream(**params) as stream:
                for _ in stream.text_stream:
                    pass
                response = stream.get_final_message()
            text = "".join(b.text for b in response.content if b.type == "text").strip()

            posts = self._load_content()
            post = {
                "id": max((p["id"] for p in posts), default=0) + 1,
                "topic": topic,
                "text": text,
                "created_at": int(time.time()),
            }
            posts.insert(0, post)
            self._content_path.write_text(json.dumps(posts[:20]))
        return post

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

    def _gmail_state(self) -> dict:
        info = {"configured": self.gmail.configured, "connected": self.gmail.connected, "messages": []}
        if info["connected"]:
            try:
                info["messages"] = self.gmail.recent()
            except Exception as e:  # noqa: BLE001 — surface to the page, don't crash /api/state
                info["error"] = str(e)
        return info

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
            "todos": self._load_todos(),
            "content": self._load_content(),
            "gmail": self._gmail_state(),
            "busy": self._sse is not None,
            "settings": {
                "owner": self.config["owner"],
                "model": self.config["model"],
                "thinking": self.config["thinking"],
                "effort": self.config["effort"],
                "workspace": str(self.config["workspace"]),
                "require_approval": self.config["require_approval"],
                "autonomous_shell": self.config["autonomous_shell"],
                "shell_timeout": self.config["shell_timeout"],
                "browser_timeout": self.config["browser_timeout"],
            },
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

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/" or path == "/index.html":
                body = (STATIC_DIR / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/state":
                self._json(server.state())
            elif path == "/api/gmail/connect":
                if not server.gmail.configured:
                    self._json({"error": "gmail_client_id/gmail_client_secret not set in config"}, 400)
                    return
                self._redirect(server.gmail.auth_url())
            elif path == "/api/gmail/callback":
                code = parse_qs(parsed.query).get("code", [None])[0]
                if code:
                    try:
                        server.gmail.handle_callback(code)
                    except Exception:  # noqa: BLE001 — land back on the dashboard either way
                        pass
                self._redirect("/")
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
            elif self.path == "/api/todos":
                text = self._read_body().get("text", "").strip()
                if not text:
                    self._json({"error": "empty task"}, 400)
                    return
                self._json(server.add_todo(text))
            elif self.path == "/api/todos/toggle":
                todo_id = self._read_body().get("id")
                if server.toggle_todo(todo_id):
                    self._json({"toggled": todo_id})
                else:
                    self._json({"error": "no such task"}, 404)
            elif self.path == "/api/content":
                topic = self._read_body().get("topic", "").strip()
                if not topic:
                    self._json({"error": "empty topic"}, 400)
                    return
                try:
                    self._json(server.generate_content(topic))
                except Exception as e:  # noqa: BLE001 — surface to the page
                    self._json({"error": str(e)}, 500)
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
    chat_server = ChatServer(config, base_url=f"http://{args.host}:{args.port}")
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
