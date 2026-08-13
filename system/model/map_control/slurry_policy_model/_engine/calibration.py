from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pandas as pd


def calibrate_action_magnitude_bins(
    episodes: pd.DataFrame, training: dict[str, Any]
) -> dict[str, Any]:
    cfg = copy.deepcopy(training["action_magnitude"])
    if cfg.get("mode", "auto") != "auto":
        cfg["effective_family_bins"] = copy.deepcopy(
            cfg.get("family_fixed_bins", {})
        )
        cfg["effective_default_bins"] = copy.deepcopy(
            cfg["default_fixed_bins"]
        )
        return cfg

    if episodes.empty or not {
        "episode_type",
        "valid",
        "action_magnitude_value",
    }.issubset(episodes.columns):
        action = pd.DataFrame(
            columns=["action_family", "action_magnitude_value"]
        )
    else:
        action = episodes[
            (episodes["episode_type"] == "ACTION") & episodes["valid"]
        ].copy()
    values = pd.to_numeric(
        action.get("action_magnitude_value", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    if values.empty:
        default = copy.deepcopy(cfg["default_fixed_bins"])
    else:
        default = {
            "small_max": float(
                values.quantile(float(cfg["small_quantile"]))
            ),
            "medium_max": float(
                values.quantile(float(cfg["medium_quantile"]))
            ),
        }
    default["small_max"] = max(float(cfg["micro_max"]), default["small_max"])
    default["medium_max"] = max(
        default["small_max"], default["medium_max"]
    )

    family_bins: dict[str, dict[str, float]] = {}
    min_events = int(cfg["minimum_events_per_family"])
    for family, group in action.groupby("action_family", dropna=False):
        magnitudes = pd.to_numeric(
            group["action_magnitude_value"], errors="coerce"
        ).dropna()
        if len(magnitudes) < min_events:
            continue
        small = float(
            magnitudes.quantile(float(cfg["small_quantile"]))
        )
        medium = float(
            magnitudes.quantile(float(cfg["medium_quantile"]))
        )
        family_bins[str(family)] = {
            "small_max": max(float(cfg["micro_max"]), small),
            "medium_max": max(
                max(float(cfg["micro_max"]), small), medium
            ),
        }
    cfg["effective_default_bins"] = default
    cfg["effective_family_bins"] = family_bins
    cfg["calibrated_from_data"] = True
    return cfg


def assign_action_magnitude_labels(
    episodes: pd.DataFrame, effective_action_cfg: dict[str, Any]
) -> pd.DataFrame:
    result = episodes.copy()
    labels: list[str] = []
    micro = float(effective_action_cfg["micro_max"])
    default = effective_action_cfg["effective_default_bins"]
    family_bins = effective_action_cfg.get("effective_family_bins", {})
    for row in result.itertuples(index=False):
        if row.episode_type == "HOLD":
            labels.append("HOLD")
            continue
        value = float(row.action_magnitude_value)
        bins = family_bins.get(str(row.action_family), default)
        if value <= micro:
            label = "MICRO"
        elif value <= float(bins["small_max"]):
            label = "SMALL"
        elif value <= float(bins["medium_max"]):
            label = "MEDIUM"
        else:
            label = "STRONG"
        labels.append(label)
    result["action_magnitude"] = labels
    result["action_id"] = (
        result["action_family"].astype(str)
        + "|"
        + result["action_direction"].astype(str)
        + "|"
        + result["action_magnitude"].astype(str)
    )
    return result


def calibrate_response_settings(
    episodes: pd.DataFrame, training: dict[str, Any]
) -> dict[str, Any]:
    cfg = copy.deepcopy(training["response"])
    if episodes.empty or "valid" not in episodes.columns:
        valid = pd.DataFrame(
            columns=["delta_outlet_so2", "post_outlet_so2_range"]
        )
    else:
        valid = episodes[episodes["valid"]].copy()
    abs_delta = pd.to_numeric(
        valid.get("delta_outlet_so2", pd.Series(dtype=float)),
        errors="coerce",
    ).abs().dropna()
    if cfg.get("effect_strength_mode") == "auto" and not abs_delta.empty:
        q1 = float(
            abs_delta.quantile(float(cfg["effect_strength_small_quantile"]))
        )
        q2 = float(
            abs_delta.quantile(float(cfg["effect_strength_medium_quantile"]))
        )
        dead = float(cfg["so2_direction_deadband"])
        cfg["effective_effect_strength_bins"] = {
            "weak_max": max(dead, q1 * 0.5),
            "small_max": max(dead, q1),
            "medium_max": max(q1, q2),
        }
    else:
        cfg["effective_effect_strength_bins"] = copy.deepcopy(
            cfg["effect_strength_fixed_bins"]
        )

    if cfg.get("stable_so2_range_max") is None:
        ranges = pd.to_numeric(
            valid.get(
                "post_outlet_so2_range", pd.Series(dtype=float)
            ),
            errors="coerce",
        ).dropna()
        cfg["effective_stable_so2_range_max"] = (
            float(ranges.quantile(0.75)) if not ranges.empty else 5.0
        )
    else:
        cfg["effective_stable_so2_range_max"] = float(
            cfg["stable_so2_range_max"]
        )
    cfg["calibrated_from_data"] = True
    return cfg


def assign_response_labels(
    episodes: pd.DataFrame, effective_response_cfg: dict[str, Any]
) -> pd.DataFrame:
    result = episodes.copy()
    dead = float(effective_response_cfg["so2_direction_deadband"])
    bins = effective_response_cfg["effective_effect_strength_bins"]

    directions: list[str] = []
    strengths: list[str] = []
    stable_flags: list[bool] = []
    for row in result.itertuples(index=False):
        delta = (
            float(row.delta_outlet_so2)
            if pd.notna(row.delta_outlet_so2)
            else np.nan
        )
        if np.isnan(delta):
            directions.append("UNKNOWN")
            strengths.append("UNKNOWN")
        elif delta < -dead:
            directions.append("DECREASE")
        elif delta > dead:
            directions.append("INCREASE")
        else:
            directions.append("NEUTRAL")

        if np.isnan(delta):
            pass
        elif abs(delta) <= dead:
            strengths.append("NEUTRAL")
        elif abs(delta) <= float(bins["weak_max"]):
            strengths.append("WEAK")
        elif abs(delta) <= float(bins["small_max"]):
            strengths.append("SMALL")
        elif abs(delta) <= float(bins["medium_max"]):
            strengths.append("MEDIUM")
        else:
            strengths.append("STRONG")

        stable_flags.append(
            bool(
                row.post_outlet_so2_range
                <= effective_response_cfg["effective_stable_so2_range_max"]
            )
            and int(row.outlet_so2_sign_changes)
            <= int(effective_response_cfg["max_oscillation_sign_changes"])
        )
    result["so2_effect_direction"] = directions
    result["so2_effect_strength"] = strengths
    result["stable_response"] = stable_flags
    result["oscillation_detected"] = (
        result["outlet_so2_sign_changes"]
        > int(effective_response_cfg["max_oscillation_sign_changes"])
    )
    return result
