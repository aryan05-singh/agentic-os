"""Config loading — one YAML file drives the whole OS (same philosophy as
linux-guardian: behavior lives in config, code stays generic)."""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULTS = {
    "name": "jarvis",
    "owner": "you",
    "personality": "A sharp, concise personal assistant. No corporate filler.",
    "model": "claude-opus-4-8",
    "max_tokens": 16000,
    "effort": "high",
    "thinking": "adaptive",    # adaptive | extended | off (extended for pre-4.6 models)
    "thinking_budget": 4096,   # only used when thinking: extended (must be < max_tokens)
    "workspace": "~/agentic-os-workspace",
    "memory_db": None,  # defaults to <workspace>/memory.db
    "require_approval": True,   # ask before shell commands in interactive chat
    "autonomous_shell": False,  # allow shell in unattended scheduled tasks
    "shell_timeout": 120,
    "browser_timeout": 20,      # seconds per browser action (playwright)
    "tasks": [],
}


def load_config(path: str | Path) -> dict:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    cfg = {**DEFAULTS, **raw}

    workspace = Path(cfg["workspace"]).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    cfg["workspace"] = workspace

    memory_db = cfg["memory_db"] or workspace / "memory.db"
    cfg["memory_db"] = Path(memory_db).expanduser()

    return cfg
