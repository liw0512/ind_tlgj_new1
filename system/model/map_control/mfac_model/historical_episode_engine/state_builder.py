from __future__ import annotations

from typing import Any


def build_policy_state(
    row: dict[str, Any], plant: dict[str, Any], training: dict[str, Any]
) -> tuple[str, str]:
    """Build the flow-policy state without valve-position dependencies."""
    del plant, training
    grid = str(row.get("anchor_grid_id", row.get("start_grid_id", "UNKNOWN")))
    disturbance = str(row.get("disturbance_mode", "UNKNOWN"))
    state = "TRANSIENT" if "FAST" in disturbance else "REGULAR"
    return f"GRID={grid}|{state}", state
