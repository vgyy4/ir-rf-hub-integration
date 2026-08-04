"""Tests custom_components/ir_rf_hub/sync.py -- pure set-diff logic, zero
HA imports."""

from __future__ import annotations

from sync import diff_ids


def test_no_change():
    added, removed = diff_ids({"a", "b"}, {"a", "b"})
    assert added == set()
    assert removed == set()


def test_additions_only():
    added, removed = diff_ids({"a"}, {"a", "b", "c"})
    assert added == {"b", "c"}
    assert removed == set()


def test_removals_only():
    added, removed = diff_ids({"a", "b", "c"}, {"a"})
    assert added == set()
    assert removed == {"b", "c"}


def test_simultaneous_add_and_remove():
    added, removed = diff_ids({"a", "b"}, {"b", "c"})
    assert added == {"c"}
    assert removed == {"a"}


def test_empty_to_populated():
    added, removed = diff_ids(set(), {"a", "b"})
    assert added == {"a", "b"}
    assert removed == set()


def test_populated_to_empty():
    added, removed = diff_ids({"a", "b"}, set())
    assert added == set()
    assert removed == {"a", "b"}
