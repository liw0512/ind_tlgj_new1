from __future__ import annotations

from typing import Any

import pandas as pd

from .config_loader import enabled_towers


def clean_supply_flow_column(tower_id: str) -> str:
    """Canonical cleaned total slurry-flow column for one absorber tower."""
    return f"__clean_supply_flow__{tower_id}"


def add_clean_supply_flow_columns(
    df: pd.DataFrame, plant: dict[str, Any], training: dict[str, Any]
) -> pd.DataFrame:
    """Add cleaned per-meter and tower-total actual slurry-flow signals.

    A plant may configure one or multiple ``supply_flows`` for a tower. Each
    physical meter is lightly median-filtered first, then all configured meters
    are summed into one canonical tower-level flow signal. If any configured
    meter is missing at a timestamp, the tower total remains NaN rather than
    silently under-counting the delivered slurry flow.
    """
    result = df.copy()
    preprocessing = training.get("preprocessing", {})
    window = int(preprocessing["supply_flow_rolling_median_points"])
    window = max(1, window)

    for tower in enabled_towers(plant):
        tower_id = str(tower["tower_id"])
        meter_clean_columns: list[str] = []
        for index, flow in enumerate(tower.get("supply_flows", []) or [], start=1):
            source = str(flow.get("column", "")).strip()
            if not source:
                continue
            flow_id = str(flow.get("flow_id") or f"{tower_id}_flow_{index}")
            clean_meter = f"__clean_supply_flow_meter__{flow_id}"
            result[clean_meter] = (
                pd.to_numeric(result[source], errors="coerce")
                .rolling(window=window, center=True, min_periods=1)
                .median()
            )
            meter_clean_columns.append(clean_meter)

        if not meter_clean_columns:
            continue

        tower_clean = clean_supply_flow_column(tower_id)
        if len(meter_clean_columns) == 1:
            result[tower_clean] = result[meter_clean_columns[0]]
        else:
            result[tower_clean] = result[meter_clean_columns].sum(
                axis=1,
                min_count=len(meter_clean_columns),
            )

    return result
