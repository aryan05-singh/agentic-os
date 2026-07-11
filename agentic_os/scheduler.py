"""Automation layer — scheduled tasks, cron-friendly.

Tasks live in config.yaml:

    tasks:
      - name: morning-brief
        every: daily          # daily | hourly
        prompt: "Summarize yesterday's notes in the workspace and ..."

Run one pass (from cron, a systemd timer, or by hand):

    python -m agentic_os.scheduler --config config.yaml

Each pass runs every task that is due, records the run time in
<workspace>/.scheduler_state.json, and appends output to <workspace>/logs/.
Unattended runs get shell access only if config.autonomous_shell is true.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from .config import load_config
from .kernel import Kernel
from .llm import make_client
from .memory import Memory

INTERVALS = {"hourly": 3600, "daily": 86400}


def due_tasks(tasks: list[dict], state: dict, now: float) -> list[dict]:
    """Pure function so it's trivially testable."""
    out = []
    for task in tasks:
        interval = INTERVALS.get(task.get("every", "daily"), 86400)
        last = state.get(task["name"], 0)
        if now - last >= interval:
            out.append(task)
    return out


def run_pass(config_path: str) -> int:
    config = load_config(config_path)
    state_path: Path = config["workspace"] / ".scheduler_state.json"
    log_dir: Path = config["workspace"] / "logs"
    log_dir.mkdir(exist_ok=True)

    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    due = due_tasks(config["tasks"], state, time.time())
    if not due:
        return 0

    client = make_client()
    memory = Memory(config["memory_db"])
    # unattended: no human to ask — policy decides shell access
    approve = lambda _cmd: bool(config["autonomous_shell"])  # noqa: E731

    for task in due:
        kernel = Kernel(client, config, memory, approve)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            result = kernel.run_turn(task["prompt"])
        except Exception as e:  # noqa: BLE001 — one bad task must not kill the pass
            result = f"TASK FAILED: {e}"
        log_file = log_dir / f"{task['name']}.log"
        with log_file.open("a") as f:
            f.write(f"\n===== {stamp} =====\n{result}\n")
        state[task["name"]] = time.time()
        state_path.write_text(json.dumps(state, indent=2))

    memory.close()
    return len(due)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run due scheduled tasks once.")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    ran = run_pass(args.config)
    print(f"ran {ran} task(s)")


if __name__ == "__main__":
    main()
