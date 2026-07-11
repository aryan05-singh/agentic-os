"""Intelligence layer — the only file that talks to the Anthropic API.

Kept separate so tests can inject a fake client and so the model/params live
in exactly one place.
"""

from __future__ import annotations

from anthropic import Anthropic


def make_client() -> Anthropic:
    # Zero-arg: resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an
    # `ant auth login` profile from the environment.
    return Anthropic()


def request_params(config: dict, system: str, messages: list, tools: list) -> dict:
    params = {
        "model": config["model"],
        "max_tokens": config["max_tokens"],
        "system": [
            {
                "type": "text",
                "text": system,
                # the system prompt is the stable prefix — cache it
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
        "tools": tools,
    }

    # Thinking mode depends on model generation:
    #   adaptive  — Opus 4.6+ / Sonnet 5 / Fable 5 (effort supported)
    #   extended  — older models (Haiku 4.5, Sonnet 4.5): fixed budget_tokens,
    #               and `effort` is rejected, so omit output_config
    #   off       — no thinking block at all (also omits effort for safety)
    mode = config["thinking"]
    if mode == "adaptive":
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": config["effort"]}
    elif mode == "extended":
        params["thinking"] = {
            "type": "enabled",
            "budget_tokens": config["thinking_budget"],
        }
    elif mode != "off":
        raise ValueError(f"unknown thinking mode: {mode!r} (adaptive | extended | off)")

    return params
