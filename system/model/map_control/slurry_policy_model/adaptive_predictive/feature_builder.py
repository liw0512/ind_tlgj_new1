from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd


@dataclass(frozen=True)
class CausalFeatureConfig:
    output_delta_lags: int = 6
    flow_delta_lags: int = 60
    disturbance_delta_lags: int = 60
    context_delta_lags: int = 6
    causal_flow_filter_points: int = 3
    segment_column: str = "continuous_segment_id"

    def validate(self) -> None:
        for name in (
            "output_delta_lags",
            "flow_delta_lags",
            "disturbance_delta_lags",
            "context_delta_lags",
            "causal_flow_filter_points",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def causal_tower_total_flow(
    frame: pd.DataFrame,
    tower: dict[str, Any],
    *,
    filter_points: int = 3,
) -> pd.Series:
    """Build a causal tower-total actual-flow signal.

    The legacy event pipeline uses a centered median for offline event cleanup.
    Predictive identification must not use that acausal signal.  This function
    only uses each meter's current and past samples (trailing median), and it
    refuses to silently sum a partial meter set.
    """

    window = max(1, int(filter_points))
    meter_series: list[pd.Series] = []
    for flow in tower.get("supply_flows", []) or []:
        column = str(flow.get("column", "")).strip()
        if not column:
            continue
        if column not in frame.columns:
            raise KeyError(f"missing actual supply-flow column: {column}")
        values = pd.to_numeric(frame[column], errors="coerce")
        filtered = values.rolling(window=window, min_periods=1).median()
        meter_series.append(filtered.rename(column))

    if not meter_series:
        raise ValueError(
            "predictive identification requires at least one configured actual supply-flow meter"
        )
    if len(meter_series) == 1:
        return meter_series[0].rename("tower_total_flow")
    meters = pd.concat(meter_series, axis=1)
    return meters.sum(axis=1, min_count=len(meter_series)).rename("tower_total_flow")


def _segment_iterator(
    frame: pd.DataFrame,
    segment_column: str,
):
    if segment_column in frame.columns:
        yield from frame.groupby(segment_column, sort=False, dropna=False)
    else:
        yield "__ALL__", frame


def build_causal_one_step_frame(
    frame: pd.DataFrame,
    *,
    output_column: str,
    tower: dict[str, Any],
    disturbance_columns: Iterable[str],
    context_columns: Iterable[str] = (),
    config: CausalFeatureConfig | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...], str]:
    """Create a strictly causal ARX/FIR-style one-step training frame.

    Row ``t`` predicts ``output(t+1)-output(t)``. Every feature is available at
    or before ``t``. Lag construction is restarted at each continuous segment,
    so no feature can cross a long data gap.
    """

    cfg = config or CausalFeatureConfig()
    cfg.validate()
    disturbances = _unique(disturbance_columns)
    contexts = tuple(
        value
        for value in _unique(context_columns)
        if value != output_column and value not in disturbances
    )

    required = _unique((output_column,) + disturbances + contexts)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise KeyError("missing predictive columns: " + ", ".join(missing))

    target_column = "target_delta_output_next"
    pieces: list[pd.DataFrame] = []
    feature_names: list[str] | None = None

    for _, segment in _segment_iterator(frame, cfg.segment_column):
        if segment.empty:
            continue
        work = pd.DataFrame(index=segment.index)
        output = pd.to_numeric(segment[output_column], errors="coerce")
        flow = causal_tower_total_flow(
            segment,
            tower,
            filter_points=cfg.causal_flow_filter_points,
        )
        output_delta = output.diff()
        flow_delta = flow.diff()

        work["output_level_t"] = output
        for lag in range(cfg.output_delta_lags):
            work[f"output_delta_lag_{lag}"] = output_delta.shift(lag)

        work["flow_level_t"] = flow
        for lag in range(cfg.flow_delta_lags):
            work[f"flow_delta_lag_{lag}"] = flow_delta.shift(lag)

        for column in disturbances:
            values = pd.to_numeric(segment[column], errors="coerce")
            delta = values.diff()
            work[f"disturbance__{column}__level_t"] = values
            for lag in range(cfg.disturbance_delta_lags):
                work[f"disturbance__{column}__delta_lag_{lag}"] = delta.shift(lag)

        for column in contexts:
            values = pd.to_numeric(segment[column], errors="coerce")
            delta = values.diff()
            work[f"context__{column}__level_t"] = values
            for lag in range(cfg.context_delta_lags):
                work[f"context__{column}__delta_lag_{lag}"] = delta.shift(lag)

        work[target_column] = output.shift(-1) - output
        work["source_index"] = segment.index
        current_features = [
            column
            for column in work.columns
            if column not in {target_column, "source_index"}
        ]
        if feature_names is None:
            feature_names = current_features
        elif current_features != feature_names:
            raise RuntimeError("causal feature schema changed between segments")
        work = work.dropna(subset=current_features + [target_column])
        if not work.empty:
            pieces.append(work)

    features = tuple(feature_names or ())
    if not pieces:
        return pd.DataFrame(columns=list(features) + [target_column, "source_index"]), features, target_column
    result = pd.concat(pieces, axis=0, ignore_index=True)
    return result, features, target_column
