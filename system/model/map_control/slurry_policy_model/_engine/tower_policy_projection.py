from __future__ import annotations

from typing import Any

import pandas as pd

from .config_loader import enabled_towers


def project_tower_policy_deltas(
    episodes: pd.DataFrame,
    plant: dict[str, Any],
) -> pd.DataFrame:
    """Project single-tower episode deltas to a tower-demand representation.

    The returned frame keeps the original branch-valve facts in
    ``raw_delta_valve__*`` / ``raw_normalized_delta_valve__*`` columns.  The normal
    ``delta_valve__*`` columns are the policy-facing representation used by existing
    aggregation code.

    For ``TOWER:<tower>|SUPPLY`` we calculate, per episode, the mean normalized
    movement across all configured valves of the tower, then write the same normalized
    tower-equivalent movement back to every valve in that tower.  Therefore operators
    alternating between branch valves no longer causes the learned representative
    action to collapse toward zero, while the exact historical allocation remains
    auditable through the raw columns.

    Multi-tower combined actions and legacy action families are left unchanged.
    """
    if episodes.empty or "action_family" not in episodes.columns:
        return episodes.copy()

    result = episodes.copy()

    # Preserve physical historical facts before changing the policy-facing columns.
    for tower in enabled_towers(plant):
        for valve in tower.get("valves", []):
            valve_id = str(valve["valve_id"])
            delta_column = f"delta_valve__{valve_id}"
            normalized_column = f"normalized_delta_valve__{valve_id}"
            raw_delta_column = f"raw_delta_valve__{valve_id}"
            raw_normalized_column = f"raw_normalized_delta_valve__{valve_id}"
            if delta_column in result.columns and raw_delta_column not in result.columns:
                result[raw_delta_column] = result[delta_column]
            if (
                normalized_column in result.columns
                and raw_normalized_column not in result.columns
            ):
                result[raw_normalized_column] = result[normalized_column]

    families = result["action_family"].astype(str)
    for tower in enabled_towers(plant):
        tower_id = str(tower["tower_id"])
        family = f"TOWER:{tower_id}|SUPPLY"
        mask = families == family
        if not bool(mask.any()):
            continue

        valves = list(tower.get("valves", []))
        normalized_columns = [
            f"raw_normalized_delta_valve__{valve['valve_id']}"
            for valve in valves
            if f"raw_normalized_delta_valve__{valve['valve_id']}" in result.columns
        ]
        if not normalized_columns:
            continue

        normalized = result.loc[mask, normalized_columns].apply(
            pd.to_numeric, errors="coerce"
        ).fillna(0.0)
        equivalent = normalized.mean(axis=1)

        for valve in valves:
            valve_id = str(valve["valve_id"])
            span = float(valve["max_opening"]) - float(valve["min_opening"])
            delta_column = f"delta_valve__{valve_id}"
            normalized_column = f"normalized_delta_valve__{valve_id}"
            if delta_column in result.columns:
                result.loc[mask, delta_column] = equivalent * span
            if normalized_column in result.columns:
                result.loc[mask, normalized_column] = equivalent

    return result
