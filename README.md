# agentic-os

A minimal personal **agentic operating system** built from scratch in Python —
no agent frameworks, no orchestration libraries. Just the five layers every
agentic OS is made of, each in one readable file:

| Layer | File | What it does |
|---|---|---|
| **Intelligence** | `agentic_os/llm.py` | Claude via the official Anthropic SDK (adaptive thinking, prompt caching, streaming) |
| **Memory** | `agentic_os/memory.py` | Persistent SQLite memory — the agent `remember`s facts and wakes up with a digest of them in every session |
| **Tools** | `agentic_os/tools.py` | Shell, workspace files, memory — with a human approval gate and path confinement |
| **Automation** | `agentic_os/scheduler.py` | Cron-friendly scheduled tasks the agent runs unattended |
| **Interface** | `agentic_os/__main__.py` | Streaming CLI chat REPL |

The agent loop itself (`agentic_os/kernel.py`) is a deliberate manual
implementation of the request → tool_use → tool_result cycle, so the entire
control flow of "an agent" fits on one screen.

## Quick start

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit name/personality/tasks

export ANTHROPIC_API_KEY=sk-ant-...  # or `ant auth login`
python -m agentic_os --config config.yaml
```

```
jarvis ready. Ctrl-D to exit.

you> remember that I deploy on Fridays only
jarvis> Stored. I'll keep that in mind.

you> what do you know about my deploys?
jarvis> You deploy on Fridays only.
```

Memory persists across sessions — quit, reopen, and the agent still knows.

## Model configuration

Any Claude model works — set `model` and the matching `thinking` mode in
`config.yaml`:

```yaml
model: claude-opus-4-8      # or claude-sonnet-5, claude-haiku-4-5, ...
thinking: adaptive          # adaptive (Opus 4.6+ / Sonnet 5) | extended (older models) | off
effort: high                # only applies with adaptive thinking
# thinking_budget: 4096     # only with thinking: extended
```

`adaptive` lets the model decide when and how deeply to think (recommended on
current models). `extended` is the fixed-budget form older models like Haiku
4.5 require. `off` disables thinking entirely for latency-sensitive setups.

## Scheduled automation

Define tasks in `config.yaml`, then let cron fire a pass:

```cron
*/30 * * * * cd /path/to/agentic-os && python3 -m agentic_os.scheduler --config config.yaml
```

Each pass runs only the tasks that are due (`daily` / `hourly`), logs output to
`<workspace>/logs/<task>.log`, and tracks state in `.scheduler_state.json`.
Missed windows (laptop asleep) simply run on the next pass — no double-runs.

## Safety model

- **Shell approval gate** — interactive chat asks `y/N` before every command;
  unattended scheduled runs get shell access only if `autonomous_shell: true`.
- **Workspace confinement** — file tools resolve paths and reject anything
  that escapes the workspace directory.
- **Failure honesty** — tool errors go back to the model as `is_error` results
  instead of crashing the loop; denied commands are reported as denied.

## Tests

```bash
pytest
```

Fake-client dependency injection — the full tool loop, approval gate, and
path-confinement behavior are tested without a single real API call.

## Design notes

- One YAML config drives everything; code stays generic (same philosophy as
  [linux-guardian](https://github.com/aryan05-singh/linux-guardian)).
- The system prompt is the stable cache prefix: identity + memory digest are
  assembled once per turn and marked with `cache_control` so multi-turn chat
  reuses the cache.
- Parallel tool calls are supported: all results return in a single user
  message, as the API expects.
- `stop_reason` is handled exhaustively: `end_turn`, `tool_use`, `pause_turn`
  (server-side resume), `max_tokens`, and `refusal`.
