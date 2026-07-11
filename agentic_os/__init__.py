"""agentic-os — a minimal personal agentic operating system built from scratch.

Five layers:
  intelligence (llm.py)  — Claude via the official Anthropic SDK
  memory       (memory.py) — persistent SQLite memory with recall
  tools        (tools.py)  — shell / files / memory, with an approval gate
  automation   (scheduler.py) — cron-friendly scheduled tasks
  interface    (__main__.py)  — CLI chat REPL
"""

__version__ = "0.1.0"
