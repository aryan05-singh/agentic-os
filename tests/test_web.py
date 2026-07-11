"""Web dashboard tests — real HTTP against ChatServer, fake Anthropic client."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from agentic_os.web import ChatServer, make_handler
from tests.test_kernel import FakeClient, response, text_block, tool_use_block


def make_config(tmp_path):
    return {
        "name": "testbot",
        "owner": "tester",
        "personality": "terse",
        "model": "claude-opus-4-8",
        "max_tokens": 16000,
        "effort": "high",
        "thinking": "adaptive",
        "thinking_budget": 4096,
        "workspace": tmp_path,
        "memory_db": tmp_path / "memory.db",
        "require_approval": True,
        "shell_timeout": 10,
        "tasks": [{"name": "brief", "every": "daily", "prompt": "do the brief"}],
    }


@pytest.fixture
def server(tmp_path):
    chat_server = ChatServer(make_config(tmp_path), client=FakeClient([]))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(chat_server))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield chat_server, base
    httpd.shutdown()


def get_json(url):
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def post(url, payload):
    req = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(req)


def read_events(resp):
    events = []
    for line in resp.read().decode().splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_state_reports_identity_memories_and_tasks(server):
    chat_server, base = server
    chat_server.memory.remember("editor", "prefers neovim")
    state = get_json(f"{base}/api/state")
    assert state["name"] == "testbot"
    assert state["memories"][0]["topic"] == "editor"
    assert state["tasks"] == [
        {"name": "brief", "every": "daily", "last_run": None, "due": True}
    ]


def test_todos_add_toggle_roundtrip(server):
    _, base = server
    with post(f"{base}/api/todos", {"text": "ship the globe"}) as resp:
        todo = json.loads(resp.read())
    assert todo["text"] == "ship the globe" and todo["done"] is False

    state = get_json(f"{base}/api/state")
    assert state["todos"] == [todo]

    post(f"{base}/api/todos/toggle", {"id": todo["id"]})
    assert get_json(f"{base}/api/state")["todos"][0]["done"] is True


def test_todos_reject_empty_and_unknown(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(f"{base}/api/todos", {"text": "  "})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        post(f"{base}/api/todos/toggle", {"id": 999})
    assert e.value.code == 404


def test_dashboard_page_served(server):
    _, base = server
    with urllib.request.urlopen(f"{base}/") as resp:
        assert resp.headers["Content-Type"].startswith("text/html")
        assert b"agentic-os" in resp.read()


def test_chat_streams_tokens_and_done(server):
    chat_server, base = server
    chat_server.kernel.client._responses = iter([response([text_block("hi there")])])
    events = read_events(post(f"{base}/api/chat", {"message": "hello"}))
    assert {"type": "token", "text": "hi there"} in events
    assert events[-1] == {"type": "done"}


def test_shell_approval_round_trip(server):
    chat_server, base = server
    chat_server.kernel.client._responses = iter(
        [
            response([tool_use_block("t1", "run_shell", {"command": "echo hi"})],
                     stop_reason="tool_use"),
            response([text_block("ran it")]),
        ]
    )

    events_holder = {}

    def do_chat():
        events_holder["events"] = read_events(
            post(f"{base}/api/chat", {"message": "run echo hi"})
        )

    chat_thread = threading.Thread(target=do_chat)
    chat_thread.start()

    # wait for the kernel to block on the approval gate, then allow it
    for _ in range(100):
        if chat_server._pending is not None:
            break
        threading.Event().wait(0.05)
    assert chat_server._pending["command"] == "echo hi"
    assert post(f"{base}/api/approve", {"allow": True})

    chat_thread.join(timeout=10)
    events = events_holder["events"]
    assert {"type": "approval", "command": "echo hi"} in events
    assert {"type": "token", "text": "ran it"} in events
    assert events[-1] == {"type": "done"}
