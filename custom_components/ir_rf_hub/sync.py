"""Pure add/remove diffing for coordinator.py's full-resync-on-any-event
strategy, split into its own module (zero Home Assistant imports) so it's
unit-testable without installing HA core.
"""

from __future__ import annotations


def diff_ids(old_ids: set[str], new_ids: set[str]) -> tuple[set[str], set[str]]:
    """Returns (added, removed)."""
    return new_ids - old_ids, old_ids - new_ids
