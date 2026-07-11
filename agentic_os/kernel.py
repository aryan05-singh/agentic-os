"""Kernel — the agent loop.

Owns the request -> tool_use -> tool_result -> repeat cycle (a deliberate
from-scratch manual loop, so the whole control flow is visible in one file),
plus system-prompt assembly with the memory digest.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .llm import request_params
from .memory import Memory
from .tools import TOOL_DEFINITIONS, ToolBox

MAX_ITERATIONS = 25


def build_system_prompt(config: dict, memory: Memory) -> str:
    return (
        f"You are {config['name']}, {config['owner']}'s personal agentic OS.\n"
        f"{config['personality']}\n\n"
        f"Workspace directory: {config['workspace']}\n"
        f"Today: {datetime.now():%A, %B %d, %Y}\n\n"
        "You have persistent memory. Recent memories:\n"
        f"{memory.digest()}\n\n"
        "When the user shares a durable fact, preference, or decision, store it "
        "with the remember tool. Use recall before claiming you don't know "
        "something about the user. Be concise; lead with the outcome."
    )


class Kernel:
    def __init__(
        self,
        client,
        config: dict,
        memory: Memory,
        approve: Callable[[str], bool],
        on_text: Callable[[str], None] | None = None,
    ):
        self.client = client
        self.config = config
        self.memory = memory
        self.toolbox = ToolBox(config, memory, approve)
        self.on_text = on_text or (lambda _: None)
        self.messages: list = []

    def run_turn(self, user_input: str) -> str:
        """One user turn: loops until Claude stops calling tools. Returns the
        final text and keeps self.messages updated for multi-turn chat."""
        self.messages.append({"role": "user", "content": user_input})
        system = build_system_prompt(self.config, self.memory)
        final_text: list[str] = []

        for _ in range(MAX_ITERATIONS):
            params = request_params(self.config, system, self.messages, TOOL_DEFINITIONS)
            with self.client.messages.stream(**params) as stream:
                for text in stream.text_stream:
                    self.on_text(text)
                response = stream.get_final_message()

            # Preserve the full content (incl. thinking/tool_use blocks) so
            # the next request in this turn has valid history.
            self.messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if block.type == "text":
                    final_text.append(block.text)

            if response.stop_reason == "refusal":
                final_text.append("(request declined by safety systems)")
                break
            if response.stop_reason == "pause_turn":
                continue  # server-side pause: re-send as-is, it resumes
            if response.stop_reason == "max_tokens":
                final_text.append("(output truncated at max_tokens)")
                break
            if response.stop_reason != "tool_use":
                break  # end_turn — done

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    output = self.toolbox.execute(block.name, dict(block.input))
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": output}
                    )
                except Exception as e:  # noqa: BLE001 — model should see the failure
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"tool error: {e}",
                            "is_error": True,
                        }
                    )
            # all results for parallel calls go back in ONE user message
            self.messages.append({"role": "user", "content": results})
        else:
            final_text.append("(stopped: hit max tool iterations)")

        return "\n".join(t for t in final_text if t).strip()
