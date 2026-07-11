"""Interface layer — CLI chat REPL.

    python -m agentic_os --config config.yaml

Streams responses token-by-token, keeps conversation history for the session,
and gates every shell command behind a y/N prompt.
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .kernel import Kernel
from .llm import make_client
from .memory import Memory


def tty_approve(command: str) -> bool:
    print(f"\n\033[33m[shell approval] {command}\033[0m")
    answer = input("run this? [y/N] ").strip().lower()
    return answer == "y"


def main() -> None:
    parser = argparse.ArgumentParser(description="agentic-os chat")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    memory = Memory(config["memory_db"])
    kernel = Kernel(
        make_client(),
        config,
        memory,
        approve=tty_approve if config["require_approval"] else (lambda _: True),
        on_text=lambda t: (sys.stdout.write(t), sys.stdout.flush()),
    )

    print(f"{config['name']} ready. Ctrl-D to exit.\n")
    while True:
        try:
            user_input = input("\n\033[36myou>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not user_input:
            continue
        print(f"\033[35m{config['name']}>\033[0m ", end="", flush=True)
        kernel.run_turn(user_input)
        print()

    memory.close()


if __name__ == "__main__":
    main()
