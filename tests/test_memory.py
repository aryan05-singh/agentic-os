from agentic_os.memory import Memory


def make_memory(tmp_path):
    return Memory(tmp_path / "memory.db")


def test_remember_and_recall(tmp_path):
    mem = make_memory(tmp_path)
    mem.remember("coffee", "Aryan prefers filter coffee in the morning")
    mem.remember("editor", "Prefers neovim with catppuccin theme")

    rows = mem.recall("coffee morning")
    assert len(rows) == 1
    assert rows[0]["topic"] == "coffee"


def test_recall_no_match(tmp_path):
    mem = make_memory(tmp_path)
    mem.remember("coffee", "filter coffee")
    assert mem.recall("kubernetes") == []


def test_digest_orders_oldest_first_and_handles_empty(tmp_path):
    mem = make_memory(tmp_path)
    assert "no stored memories" in mem.digest()

    mem.remember("first", "fact one")
    mem.remember("second", "fact two")
    digest = mem.digest()
    assert digest.index("first") < digest.index("second")
