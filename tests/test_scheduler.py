from agentic_os.scheduler import due_tasks


TASKS = [
    {"name": "brief", "every": "daily", "prompt": "..."},
    {"name": "check", "every": "hourly", "prompt": "..."},
]


def test_all_due_when_no_state():
    assert len(due_tasks(TASKS, {}, now=1_000_000)) == 2


def test_respects_intervals():
    now = 1_000_000
    state = {"brief": now - 3600, "check": now - 3700}  # brief ran 1h ago
    due = due_tasks(TASKS, state, now)
    assert [t["name"] for t in due] == ["check"]


def test_unknown_interval_defaults_to_daily():
    tasks = [{"name": "odd", "every": "fortnightly", "prompt": "..."}]
    now = 1_000_000
    assert due_tasks(tasks, {"odd": now - 90_000}, now)  # >1 day ago -> due
    assert not due_tasks(tasks, {"odd": now - 3600}, now)
