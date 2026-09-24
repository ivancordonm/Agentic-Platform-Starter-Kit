"""Bounded store isolation and retention behavior."""

from app.engine.run_store import InMemoryRunStore


def test_store_bounds_and_returns_copies() -> None:
    store = InMemoryRunStore(max_runs=2, max_events=1)
    store.create("one", "agent", 1, "a")
    store.event("one", {"type": "started"})
    store.event("one", {"type": "ignored"})
    assert len(store.get("one")["events"]) == 1  # type: ignore[index]
    copy = store.get("one")
    assert copy is not None
    copy["events"].append({"type": "mutated"})
    assert len(store.get("one")["events"]) == 1  # type: ignore[index]
    store.create("two", "agent", 1, "b")
    store.create("three", "workflow", 2, "flow")
    assert store.get("one") is None
    assert [run["run_id"] for run in store.list()] == ["three", "two"]


def test_store_rejects_invalid_bounds() -> None:
    import pytest

    with pytest.raises(ValueError, match="positive"):
        InMemoryRunStore(max_runs=0)
