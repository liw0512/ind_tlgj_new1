from __future__ import annotations

from typing import Any

import pandas as pd

from .config_loader import all_valves


def add_clean_valve_columns(
    df: pd.DataFrame, plant: dict[str, Any], training: dict[str, Any]
) -> pd.DataFrame:
    result = df.copy()
    window = int(training["preprocessing"].get("valve_rolling_median_points", 3))
    window = max(1, window)
    for valve in all_valves(plant):
        source = valve["column"]
        clean = f"__clean_valve__{valve['valve_id']}"
        result[clean] = (
            pd.to_numeric(result[source], errors="coerce")
            .rolling(window=window, center=True, min_periods=1)
            .median()
        )
    return result
