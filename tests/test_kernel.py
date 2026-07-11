"""Kernel loop tests with a fake Anthropic client — no real API calls."""

from types import SimpleNamespace

from agentic_os.kernel import Kernel
from agentic_os.memory import Memory


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(block_id, name, args):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=args)


class FakeStream:
    def __init__(self, message):
        self._message = message
        self.text_stream = iter(
            b.text for b in message.content if b.type == "text"
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class FakeClient:
    """Yields one scripted response per API call, records the params."""

    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls = []
        outer = self

        class Messages:
            def stream(self, **params):
                # snapshot: the kernel mutates its messages list after the call
                outer.calls.append({**params, "messages": list(params["messages"])})
                return FakeStream(next(outer._responses))

        self.messages = Messages()


def make_kernel(tmp_path, responses, approve=lambda _: True):
    config = {
        "name": "testbot",
        "owner": "tester",
        "personality": "terse",
        "model": "claude-opus-4-8",
        "max_tokens": 16000,
        "effort": "high",
        "thinking": "adaptive",
        "thinking_budget": 4096,
        "workspace": tmp_path,
        "shell_timeout": 10,
    }
    memory = Memory(tmp_path / "memory.db")
    return Kernel(FakeClient(responses), config, memory, approve)


def response(content, stop_reason="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def test_plain_turn(tmp_path):
    kernel = make_kernel(tmp_path, [response([text_block("hello there")])])
    assert kernel.run_turn("hi") == "hello there"


def test_tool_loop_executes_and_feeds_result_back(tmp_path):
    responses = [
        response([tool_use_block("t1", "remember", {"topic": "x", "content": "y"})],
                 stop_reason="tool_use"),
        response([text_block("stored it")]),
    ]
    kernel = make_kernel(tmp_path, responses)
    assert kernel.run_turn("remember x=y") == "stored it"

    # the tool result went back as a single user message
    second_call = kernel.client.calls[1]
    tool_result_msg = second_call["messages"][-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert "stored memory #1" in tool_result_msg["content"][0]["content"]

    # and it actually persisted
    assert kernel.memory.recall("x")


def test_denied_shell_returns_denial_to_model(tmp_path):
    responses = [
        response([tool_use_block("t1", "run_shell", {"command": "rm -rf /"})],
                 stop_reason="tool_use"),
        response([text_block("ok, not running it")]),
    ]
    kernel = make_kernel(tmp_path, responses, approve=lambda _: False)
    kernel.run_turn("clean up my disk")
    tool_result = kernel.client.calls[1]["messages"][-1]["content"][0]
    assert "DENIED" in tool_result["content"]


def test_tool_error_is_reported_not_raised(tmp_path):
    responses = [
        response([tool_use_block("t1", "read_file", {"path": "../../etc/passwd"})],
                 stop_reason="tool_use"),
        response([text_block("that path is off limits")]),
    ]
    kernel = make_kernel(tmp_path, responses)
    kernel.run_turn("read that file")
    tool_result = kernel.client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "escapes workspace" in tool_result["content"]
