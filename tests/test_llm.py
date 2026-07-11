"""request_params thinking-mode tests — pure function, no API calls."""

import pytest

from agentic_os.llm import request_params


def make_config(**overrides):
    config = {
        "model": "claude-opus-4-8",
        "max_tokens": 16000,
        "effort": "high",
        "thinking": "adaptive",
        "thinking_budget": 4096,
    }
    return {**config, **overrides}


def test_adaptive_sets_thinking_and_effort():
    params = request_params(make_config(), "sys", [], [])
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "high"}


def test_extended_uses_budget_and_omits_effort():
    params = request_params(make_config(thinking="extended"), "sys", [], [])
    assert params["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert "output_config" not in params  # effort is rejected on pre-4.6 models


def test_off_omits_thinking_and_effort():
    params = request_params(make_config(thinking="off"), "sys", [], [])
    assert "thinking" not in params
    assert "output_config" not in params


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown thinking mode"):
        request_params(make_config(thinking="bogus"), "sys", [], [])


def test_system_prompt_is_cached_prefix():
    params = request_params(make_config(), "sys", [], [])
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
