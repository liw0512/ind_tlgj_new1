from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

import numpy as np
import pandas as pd

from .config import InitialTrainingConfig


@dataclass(frozen=True)
class KBaseCalibrationResult:
    kbase: float
    confidence: float
    calibration_window_hours: int
    accepted_window_count: int
    ratio_median: float
    ratio_p25: float
    ratio_p75: float
    independent_days: int

    def to_dict(self) -> dict:
        return {
            "kbase": self.kbase,
            "confidence": self.confidence,
            "calibration_window_hours": self.calibration_window_hours,
            "accepted_window_count": self.accepted_window_count,
            "ratio_median": self.ratio_median,
            "ratio_p25": self.ratio_p25,
            "ratio_p75": self.ratio_p75,
            "independent_days": self.independent_days,
        }


def _required_columns(config: InitialTrainingConfig) -> Tuple[str, ...]:
    return (
        config.timestamp_column,
        config.inlet_so2_column,
        config.outlet_so2_column,
        config.gas_flow_column,
        config.density_column,
        config.actual_flow_column,
    )


def prepare_qbase_history(
    frame: pd.DataFrame,
    config: InitialTrainingConfig,
) -> pd.DataFrame:
    """Build historical raw-Qbase used only for Kbase calibration.

    Historical calibration intentionally uses measured outlet SO2 because the
    historical operator setpoint is unknown. Online control must instead use the
    runtime outlet target supplied by the future online module.
    """

    config.validate()
    missing = [column for column in _required_columns(config) if column not in frame.columns]
    if missing:
        raise KeyError("missing Qbase calibration columns: %s" % ", ".join(missing))

    work = frame.loc[:, list(_required_columns(config))].copy()
    work["timestamp"] = pd.to_datetime(work[config.timestamp_column], errors="coerce")
    for column in _required_columns(config)[1:]:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["timestamp"]).sort_values("timestamp", kind="stable")
    work = work.reset_index(drop=True)

    inlet = work[config.inlet_so2_column].to_numpy(dtype=float)
    outlet = work[config.outlet_so2_column].to_numpy(dtype=float)
    gas = work[config.gas_flow_column].to_numpy(dtype=float)
    rho = work[config.density_column].to_numpy(dtype=float)
    actual_q = work[config.actual_flow_column].to_numpy(dtype=float)

    omega_percent = config.omega_k * rho + config.omega_c
    omega_fraction = omega_percent / 100.0

    valid = (
        np.isfinite(inlet)
        & np.isfinite(outlet)
        & np.isfinite(gas)
        & np.isfinite(rho)
        & np.isfinite(actual_q)
        & (inlet >= 0.0)
        & (outlet >= 0.0)
        & (gas > 0.0)
        & (rho > 0.0)
        & (actual_q >= 0.0)
        & (omega_fraction > 0.0)
        & (omega_fraction < 1.0)
    )

    delta_c = np.maximum(inlet - np.minimum(outlet, inlet), 0.0)
    removed_so2_kg_h = delta_c * gas / 1_000_000.0
    stoich_caco3_kg_h = removed_so2_kg_h * 100.0 / 64.0
    denominator = config.limestone_purity * omega_fraction * rho
    qraw = np.full(len(work), np.nan, dtype=float)
    qraw[valid] = stoich_caco3_kg_h[valid] / denominator[valid] * config.ca_s_reference

    return pd.DataFrame(
        {
            "timestamp": work["timestamp"],
            "qbase_raw_hist_m3h": qraw,
            "qactual_m3h": actual_q,
            "omega_percent": omega_percent,
            "density_kg_m3": rho,
            "inlet_so2_mg_nm3": inlet,
            "outlet_so2_actual_mg_nm3": outlet,
            "gas_flow_nm3_h": gas,
            "valid_qbase_row": valid,
        }
    )


def _aggregate_window(
    stream: pd.DataFrame,
    *,
    hours: int,
    sample_seconds: int,
    minimum_coverage: float,
) -> pd.DataFrame:
    indexed = stream.set_index("timestamp")
    rule = "%dh" % int(hours)
    expected_rows = max(int(round(hours * 3600.0 / sample_seconds)), 1)

    valid = indexed["valid_qbase_row"].astype(bool)
    qraw = indexed["qbase_raw_hist_m3h"].where(valid)
    qactual = indexed["qactual_m3h"].where(valid)

    grouped = pd.DataFrame(
        {
            "valid_rows": valid.resample(rule).sum().astype(int),
            "qraw_mean_m3h": qraw.resample(rule).mean(),
            "qactual_mean_m3h": qactual.resample(rule).mean(),
        }
    )
    grouped["coverage"] = grouped["valid_rows"] / float(expected_rows)
    grouped["qraw_volume_m3"] = grouped["qraw_mean_m3h"] * float(hours)
    grouped["qactual_volume_m3"] = grouped["qactual_mean_m3h"] * float(hours)
    grouped["kbase_observed"] = grouped["qactual_volume_m3"] / grouped["qraw_volume_m3"]
    grouped["window_hours"] = int(hours)
    grouped["accepted"] = (
        (grouped["coverage"] >= float(minimum_coverage))
        & np.isfinite(grouped["kbase_observed"])
        & (grouped["qraw_volume_m3"] > 0.0)
        & (grouped["kbase_observed"] > 0.0)
    )
    return grouped.reset_index()


def calibrate_kbase(
    frame: pd.DataFrame,
    config: InitialTrainingConfig,
    *,
    backtest_hours: Iterable[int] = (1, 6, 24),
) -> Tuple[KBaseCalibrationResult, pd.DataFrame, pd.DataFrame]:
    """Calibrate Kbase from long-horizon actual/raw slurry-volume ratios."""

    stream = prepare_qbase_history(frame, config)
    hours_list = sorted(set(int(value) for value in backtest_hours) | {config.kbase_window_hours})
    window_frames: List[pd.DataFrame] = []
    for hours in hours_list:
        if hours <= 0:
            continue
        windows = _aggregate_window(
            stream,
            hours=hours,
            sample_seconds=config.sample_seconds,
            minimum_coverage=config.kbase_min_window_coverage,
        )
        windows["used_for_kbase"] = (
            windows["window_hours"].eq(config.kbase_window_hours) & windows["accepted"]
        )
        window_frames.append(windows)

    backtest = pd.concat(window_frames, ignore_index=True) if window_frames else pd.DataFrame()
    calibration_rows = backtest.loc[backtest["used_for_kbase"]].copy()
    if len(calibration_rows) < config.kbase_min_windows:
        raise ValueError(
            "insufficient long-horizon windows for Kbase calibration: %d < %d"
            % (len(calibration_rows), config.kbase_min_windows)
        )

    ratios = calibration_rows["kbase_observed"].to_numpy(dtype=float)
    median = float(np.median(ratios))
    p25, p75 = (float(value) for value in np.quantile(ratios, [0.25, 0.75]))
    iqr = p75 - p25
    independent_days = int(calibration_rows["timestamp"].dt.normalize().nunique())
    count_score = min(1.0, len(calibration_rows) / max(float(config.kbase_min_windows * 2), 1.0))
    day_score = min(1.0, independent_days / 7.0)
    dispersion_score = max(0.0, 1.0 - iqr / max(abs(median), 1e-9))
    confidence = float(
        np.clip(0.40 * count_score + 0.25 * day_score + 0.35 * dispersion_score, 0.0, 1.0)
    )

    backtest["qbase_effective_mean_m3h"] = backtest["qraw_mean_m3h"] * median
    backtest["effective_bias_m3h"] = backtest["qbase_effective_mean_m3h"] - backtest["qactual_mean_m3h"]
    backtest["effective_abs_error_m3h"] = backtest["effective_bias_m3h"].abs()
    backtest["effective_volume_error_m3"] = backtest["effective_bias_m3h"] * backtest["window_hours"]

    result = KBaseCalibrationResult(
        kbase=median,
        confidence=confidence,
        calibration_window_hours=config.kbase_window_hours,
        accepted_window_count=int(len(calibration_rows)),
        ratio_median=median,
        ratio_p25=p25,
        ratio_p75=p75,
        independent_days=independent_days,
    )
    return result, backtest, stream


def evaluate_kbase(
    frame: pd.DataFrame,
    config: InitialTrainingConfig,
    *,
    kbase: float,
    hours_list: Iterable[int] = (1, 6, 24),
) -> pd.DataFrame:
    """Evaluate a frozen Kbase on chronologically separate data."""

    stream = prepare_qbase_history(frame, config)
    outputs: List[pd.DataFrame] = []
    for hours in sorted(set(int(value) for value in hours_list if int(value) > 0)):
        windows = _aggregate_window(
            stream,
            hours=hours,
            sample_seconds=config.sample_seconds,
            minimum_coverage=config.kbase_min_window_coverage,
        )
        windows["qbase_effective_mean_m3h"] = windows["qraw_mean_m3h"] * float(kbase)
        windows["effective_bias_m3h"] = windows["qbase_effective_mean_m3h"] - windows["qactual_mean_m3h"]
        windows["effective_abs_error_m3h"] = windows["effective_bias_m3h"].abs()
        windows["effective_volume_error_m3"] = windows["effective_bias_m3h"] * windows["window_hours"]
        outputs.append(windows)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
