from __future__ import annotations

"""V1.8B 临近工况严格等价优化回归测试。

验证：
1. 反向网格索引映射与 V1.8B 全量 condition 扫描一致；
2. 单次排序分位数与 V1.8B 三次排序一致；
3. NumPy 分类加权统计与 V1.8B pandas 分组一致；
4. 分批展开 + 组合 groupby 与 V1.8B 全量 mapped + 嵌套 groupby 一致；
5. 预计算安全与 pH 方向辅助列不改变 profile 内容。
"""

from pathlib import Path
import copy
import math
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from slurry_policy_config import PLANT_CONFIG, TRAINING_CONFIG
from _engine.aggregator import (
    _build_neighbor_state_profiles,
    _distribution,
    _prepare_neighbor_profile_helpers,
    _weighted_category_ratios,
    _weighted_counts,
    aggregate_action_profile,
    build_neighbor_mapping_table,
    build_nested_profiles,
)
from _engine.spatial_policy import distance_mapping_weight, minimum_offsets_to_grids
from _engine.utils import normalize_condition_label


def _assert_equivalent(left, right, path="root") -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        assert set(left) == set(right), f"{path}: key mismatch"
        for key in left:
            _assert_equivalent(left[key], right[key], f"{path}.{key}")
        return
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        assert len(left) == len(right), f"{path}: length mismatch"
        for index, (a, b) in enumerate(zip(left, right)):
            _assert_equivalent(a, b, f"{path}[{index}]")
        return
    if isinstance(left, (float, np.floating)) or isinstance(right, (float, np.floating)):
        a = float(left)
        b = float(right)
        if math.isnan(a) and math.isnan(b):
            return
        assert math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12), (
            f"{path}: {a!r} != {b!r}"
        )
        return
    assert left == right, f"{path}: {left!r} != {right!r}"


def _legacy_mapping(source_grid_ids, target_grids, training):
    cfg = training.get("neighbor_policy", {})
    max_load = int(training["condition_attribution"]["max_load_grid_offset"])
    max_inlet = int(training["condition_attribution"]["max_inlet_so2_grid_offset"])
    minimum_weight = float(cfg.get("minimum_mapping_weight", 0.10))
    mode = str(cfg.get("distance_weight_mode", "LINEAR_AXIS"))
    rows = []
    for source_grid_id in list(dict.fromkeys(str(v) for v in source_grid_ids)):
        for target_label, member_grid_ids in target_grids.items():
            offsets = minimum_offsets_to_grids(source_grid_id, member_grid_ids)
            if offsets is None or offsets[0] > max_load or offsets[1] > max_inlet:
                continue
            weight = distance_mapping_weight(
                offsets[0], offsets[1], max_load, max_inlet, mode
            )
            if weight < minimum_weight:
                continue
            rows.append(
                (
                    str(source_grid_id),
                    str(target_label),
                    int(offsets[0]),
                    int(offsets[1]),
                    float(weight),
                )
            )
    return rows


def _mapping_rows(frame: pd.DataFrame):
    return [
        (
            str(row.anchor_grid_id),
            str(row.neighbor_target_condition_label),
            int(row.neighbor_load_grid_offset),
            int(row.neighbor_inlet_so2_grid_offset),
            float(row.neighbor_mapping_weight),
        )
        for row in frame.itertuples(index=False)
    ]


def _legacy_weighted_counts(series: pd.Series, weights: pd.Series):
    output = {}
    clean = series.astype("object").fillna("UNKNOWN").astype(str)
    for value, index in clean.groupby(clean, observed=True).groups.items():
        output[str(value)] = float(weights.loc[index].sum())
    return output


def _legacy_weighted_ratios(series: pd.Series, weights: pd.Series):
    total = float(weights.sum())
    if total <= 0:
        return {}
    return {
        key: float(value / total)
        for key, value in _legacy_weighted_counts(series, weights).items()
    }


def _legacy_distribution(series: pd.Series, weights: pd.Series):
    clean = pd.to_numeric(series, errors="coerce")
    mask = clean.notna()
    clean = clean[mask]
    if clean.empty:
        return {
            "median": None,
            "p25": None,
            "p75": None,
            "iqr": None,
            "minimum": None,
            "maximum": None,
        }
    w = pd.to_numeric(weights.loc[clean.index], errors="coerce").fillna(0.0)
    if float(w.sum()) <= 0:
        w = pd.Series(1.0, index=clean.index)
    values = clean.to_numpy(dtype=float)
    weight_values = w.to_numpy(dtype=float)

    def q(value):
        order = np.argsort(values)
        ordered_values = values[order]
        ordered_weights = weight_values[order]
        cumulative = np.cumsum(ordered_weights)
        index = int(np.searchsorted(cumulative, value * cumulative[-1], side="left"))
        return float(ordered_values[min(index, len(ordered_values) - 1)])

    p25, median, p75 = q(0.25), q(0.50), q(0.75)
    return {
        "median": median,
        "p25": p25,
        "p75": p75,
        "iqr": p75 - p25,
        "minimum": float(np.min(values)),
        "maximum": float(np.max(values)),
    }


def _legacy_neighbor_profiles(episodes, target_grids, plant, training):
    cfg = training.get("neighbor_policy", {})
    include_same = bool(cfg.get("include_same_condition", True))
    include_global_only = bool(cfg.get("include_global_only", False))
    routes = {"LOCAL_REGULAR"}
    if include_global_only:
        routes.add("GLOBAL_ONLY")
    source = episodes[episodes["training_route"].isin(routes)].copy()
    labels = [str(label) for label in target_grids]
    output = {label: {} for label in labels}
    if source.empty:
        return output
    source["anchor_grid_id"] = source["anchor_grid_id"].astype(str)
    mapping = pd.DataFrame(
        _legacy_mapping(source["anchor_grid_id"].dropna().unique(), target_grids, training),
        columns=[
            "anchor_grid_id",
            "neighbor_target_condition_label",
            "neighbor_load_grid_offset",
            "neighbor_inlet_so2_grid_offset",
            "neighbor_mapping_weight",
        ],
    )
    mapped = source.merge(mapping, on="anchor_grid_id", how="inner", sort=False)
    if not include_same:
        mapped = mapped[
            mapped["condition_label"].map(normalize_condition_label)
            != mapped["neighbor_target_condition_label"]
        ].copy()
    if mapped.empty:
        return output
    mapped["aggregation_weight"] = (
        pd.to_numeric(mapped.get("evidence_weight", 1.0), errors="coerce")
        .fillna(1.0)
        .astype(float)
        * pd.to_numeric(mapped["neighbor_mapping_weight"], errors="coerce")
        .fillna(0.0)
        .astype(float)
    )
    grouped = {
        str(label): group
        for label, group in mapped.groupby(
            "neighbor_target_condition_label", sort=False, observed=True
        )
    }
    for label in labels:
        group = grouped.get(label)
        if group is None or group.empty:
            continue
        output[label] = build_nested_profiles(
            group, None, "policy_state_key_no_grid", plant, training
        ).get("PLANT", {})
    return output


def _fixture(seed=20260731, rows=900):
    rng = np.random.default_rng(seed)
    grids = [f"P{p}-S{s}" for p in range(1, 9) for s in range(1, 7)]
    members = {
        str(300 + i // 2): grids[i : i + 2]
        for i in range(0, len(grids), 2)
    }
    grid_to_condition = {grid: label for label, values in members.items() for grid in values}
    anchors = rng.choice(grids, size=rows)
    labels = [grid_to_condition[grid] for grid in anchors]
    states = rng.choice([f"STATE_{i}" for i in range(4)], size=rows)
    actions = rng.choice([f"ACTION_{i}" for i in range(4)], size=rows)
    times = pd.Timestamp("2026-01-01") + pd.to_timedelta(np.arange(rows) * 30, unit="s")
    frame = pd.DataFrame(
        {
            "condition_label": labels,
            "anchor_grid_id": anchors,
            "start_grid_id": anchors,
            "policy_state_key_no_grid": states,
            "action_id": actions,
            "action_family": rng.choice(["HOLD", "XST", "APT"], size=rows),
            "action_direction": rng.choice(["HOLD", "INCREASE", "DECREASE"], size=rows),
            "action_magnitude": rng.choice(["HOLD", "SMALL", "MEDIUM"], size=rows),
            "evidence_weight": rng.uniform(0.2, 1.0, size=rows),
            "so2_effect_direction": rng.choice(
                ["DECREASE", "NEUTRAL", "INCREASE", "UNKNOWN"], size=rows
            ),
            "so2_effect_strength": rng.choice(["WEAK", "SMALL", "MEDIUM"], size=rows),
            "delta_outlet_so2": rng.normal(-0.5, 1.8, size=rows),
            "stable_response": rng.random(rows) > 0.2,
            "oscillation_detected": rng.random(rows) < 0.1,
            "short_reverse_action": rng.random(rows) < 0.08,
            "post_outlet_so2_range": rng.uniform(0, 7, size=rows),
            "post_outlet_so2_std": rng.uniform(0, 3, size=rows),
            "outlet_so2_out_of_range": rng.random(rows) < 0.04,
            "outlet_so2_over_hard_max": rng.random(rows) < 0.01,
            "continuous_segment_id": rng.integers(0, 70, size=rows).astype(str),
            "event_date": times.date.astype(str),
            "action_start_time": times,
            "episode_type": rng.choice(["ACTION", "HOLD"], size=rows),
            "disturbance_mode": "STEADY",
            "attribution_source": rng.choice(["EXACT_LOCAL", "NEARBY_ACCEPTED"], size=rows),
            "training_route": rng.choice(
                ["LOCAL_REGULAR", "GLOBAL_ONLY"], size=rows, p=[0.86, 0.14]
            ),
            "neighborhood_coverage_ratio": rng.uniform(0.6, 1.0, size=rows),
            "delta_valve__xst_v1": rng.normal(0, 2, size=rows),
            "delta_valve__xst_v2": rng.normal(0, 2, size=rows),
            "delta_valve__apt_v1": rng.normal(0, 2, size=rows),
            "delta_ph__xst": rng.normal(0, 0.15, size=rows),
            "post_ph_range__xst": rng.uniform(0, 0.5, size=rows),
            "ph_below_limit__xst": rng.random(rows) < 0.03,
            "ph_above_limit__xst": rng.random(rows) < 0.03,
            "ph_out_of_range__xst": rng.random(rows) < 0.05,
            "delta_ph__apt": rng.normal(0, 0.15, size=rows),
            "post_ph_range__apt": rng.uniform(0, 0.5, size=rows),
            "ph_below_limit__apt": rng.random(rows) < 0.03,
            "ph_above_limit__apt": rng.random(rows) < 0.03,
            "ph_out_of_range__apt": rng.random(rows) < 0.05,
        }
    )
    return frame, members


def main() -> None:
    rng = np.random.default_rng(51)
    # Randomized mapping checks, including an axis-asymmetric rejection case.
    for _ in range(80):
        source = [f"P{int(p)}-S{int(s)}" for p, s in rng.integers(1, 25, size=(20, 2))]
        targets = {}
        for label in range(25):
            count = int(rng.integers(1, 7))
            cells = rng.integers(1, 25, size=(count, 2))
            targets[str(label)] = [f"P{int(p)}-S{int(s)}" for p, s in cells]
        expected = _legacy_mapping(source, targets, TRAINING_CONFIG)
        actual = _mapping_rows(build_neighbor_mapping_table(source, targets, TRAINING_CONFIG))
        _assert_equivalent(expected, actual, "mapping")

    asymmetric = {"X": ["P4-S1", "P3-S4"]}
    expected = _legacy_mapping(["P1-S1"], asymmetric, TRAINING_CONFIG)
    actual = _mapping_rows(
        build_neighbor_mapping_table(["P1-S1"], asymmetric, TRAINING_CONFIG)
    )
    _assert_equivalent(expected, actual, "asymmetric_mapping")

    values = pd.Series(rng.normal(size=400))
    values.iloc[::37] = np.nan
    weights = pd.Series(rng.uniform(0.0, 1.0, size=400))
    _assert_equivalent(
        _legacy_distribution(values, weights),
        _distribution(values, weights),
        "distribution",
    )

    categories = pd.Series(rng.choice(["A", "B", "C", None], size=400))
    _assert_equivalent(
        _legacy_weighted_counts(categories, weights),
        _weighted_counts(categories, weights),
        "weighted_counts",
    )
    _assert_equivalent(
        _legacy_weighted_ratios(categories, weights),
        _weighted_category_ratios(categories, weights),
        "weighted_ratios",
    )

    episodes, members = _fixture()
    for include_same, include_global in ((True, False), (False, False), (True, True)):
        training = copy.deepcopy(TRAINING_CONFIG)
        training["neighbor_policy"]["include_same_condition"] = include_same
        training["neighbor_policy"]["include_global_only"] = include_global
        training["performance"]["neighbor_target_condition_batch_size"] = 5
        training["performance"]["neighbor_max_expanded_rows_per_batch"] = 600
        expected = _legacy_neighbor_profiles(
            episodes, members, PLANT_CONFIG, training
        )
        actual = _build_neighbor_state_profiles(
            episodes, members, PLANT_CONFIG, training
        )
        _assert_equivalent(expected, actual, "neighbor_profiles")

    sample = episodes.iloc[:80].copy()
    sample["aggregation_weight"] = sample["evidence_weight"]
    raw_profile = aggregate_action_profile(sample, PLANT_CONFIG, TRAINING_CONFIG)
    prepared = _prepare_neighbor_profile_helpers(
        sample.copy(), PLANT_CONFIG, TRAINING_CONFIG
    )
    prepared_profile = aggregate_action_profile(
        prepared, PLANT_CONFIG, TRAINING_CONFIG
    )
    _assert_equivalent(raw_profile, prepared_profile, "prepared_profile")

    print("V1.8B 临近工况优化等价测试通过。")


if __name__ == "__main__":
    main()
