from __future__ import annotations

import math
import re
from collections import Counter, deque
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .schema import CONDITION_LABEL_COLUMN, GRID_ID_COLUMN
from .utils import normalize_condition_label

_GRID_PATTERN = re.compile(r"^P(?P<p>\d+)-S(?P<s>\d+)$", re.IGNORECASE)


def parse_grid_id(value: Any) -> tuple[int, int] | None:
    """把第一模块固定网格 ID（例如 P12-S13）解析为二维格索引。"""
    text = str(value).strip()
    match = _GRID_PATTERN.match(text)
    if not match:
        return None
    return int(match.group("p")), int(match.group("s"))


def grid_axis_offsets(anchor_grid_id: Any, other_grid_id: Any) -> tuple[int, int] | None:
    anchor = parse_grid_id(anchor_grid_id)
    other = parse_grid_id(other_grid_id)
    if anchor is None or other is None:
        return None
    return abs(other[0] - anchor[0]), abs(other[1] - anchor[1])


def _consecutive_unique(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none"}:
            continue
        if not output or output[-1] != text:
            output.append(text)
    return output


def _mode_text(values: Iterable[Any], default: str = "UNKNOWN") -> str:
    clean = [str(v).strip() for v in values]
    clean = [v for v in clean if v and v.lower() not in {"nan", "none"}]
    if not clean:
        return default
    return Counter(clean).most_common(1)[0][0]


def select_anchor_row(identity_window: pd.DataFrame, episode_type: str) -> pd.Series:
    """选择事件归属锚点。

    ACTION：动作开始时第一行。
    HOLD：窗口内占比最高的 condition_label；再在该标签内选择占比最高的 grid_id，
    并返回最早匹配行。
    """
    if identity_window.empty:
        return pd.Series(dtype="object")
    if str(episode_type).upper() != "HOLD":
        return identity_window.iloc[0]

    labels = identity_window.get(
        CONDITION_LABEL_COLUMN, pd.Series(index=identity_window.index, dtype="object")
    ).map(normalize_condition_label)
    anchor_label = _mode_text(labels, "UNKNOWN")
    label_mask = labels == anchor_label
    label_rows = identity_window[label_mask] if label_mask.any() else identity_window
    anchor_grid = _mode_text(label_rows.get(GRID_ID_COLUMN, []), "UNKNOWN")
    if GRID_ID_COLUMN in label_rows.columns:
        grid_rows = label_rows[label_rows[GRID_ID_COLUMN].astype(str) == anchor_grid]
        if not grid_rows.empty:
            return grid_rows.iloc[0]
    return label_rows.iloc[0]


def analyze_condition_attribution(
    identity_window: pd.DataFrame,
    episode_type: str,
    disturbance_mode: str,
    training: dict[str, Any],
) -> dict[str, Any]:
    """计算细工况路径、锚点邻域覆盖率和训练路由。"""
    cfg = training["condition_attribution"]
    max_load = int(cfg["max_load_grid_offset"])
    max_inlet = int(cfg["max_inlet_so2_grid_offset"])
    min_coverage = float(cfg["minimum_neighborhood_coverage_ratio"])

    start_row = identity_window.iloc[0] if not identity_window.empty else pd.Series(dtype="object")
    anchor_row = select_anchor_row(identity_window, episode_type)
    end_row = identity_window.iloc[-1] if not identity_window.empty else pd.Series(dtype="object")

    raw_grids = identity_window.get(GRID_ID_COLUMN, pd.Series(dtype="object"))
    grid_path = _consecutive_unique(raw_grids)
    raw_labels = identity_window.get(CONDITION_LABEL_COLUMN, pd.Series(dtype="object"))
    labels = [normalize_condition_label(v) for v in raw_labels]
    label_path = _consecutive_unique(labels)

    start_grid = str(start_row.get(GRID_ID_COLUMN, "UNKNOWN"))
    end_grid = str(end_row.get(GRID_ID_COLUMN, "UNKNOWN"))
    anchor_grid = str(anchor_row.get(GRID_ID_COLUMN, start_grid or "UNKNOWN"))
    start_label = normalize_condition_label(start_row.get(CONDITION_LABEL_COLUMN, "UNKNOWN"))
    end_label = normalize_condition_label(end_row.get(CONDITION_LABEL_COLUMN, "UNKNOWN"))
    anchor_label = normalize_condition_label(
        anchor_row.get(CONDITION_LABEL_COLUMN, start_label or "UNKNOWN")
    )

    valid_offsets: list[tuple[int, int]] = []
    inside_count = 0
    for value in raw_grids:
        offsets = grid_axis_offsets(anchor_grid, value)
        if offsets is None:
            continue
        valid_offsets.append(offsets)
        if offsets[0] <= max_load and offsets[1] <= max_inlet:
            inside_count += 1

    valid_grid_point_count = len(valid_offsets)
    neighborhood_coverage = (
        float(inside_count / valid_grid_point_count) if valid_grid_point_count else 0.0
    )
    max_load_offset = max((item[0] for item in valid_offsets), default=None)
    max_inlet_offset = max((item[1] for item in valid_offsets), default=None)

    exact = bool(
        grid_path
        and len(grid_path) == 1
        and len(label_path) <= 1
        and grid_path[0] == anchor_grid
        and (not label_path or label_path[0] == anchor_label)
    )
    fast = "FAST" in str(disturbance_mode)

    if fast:
        training_route = "TRANSIENT"
    elif neighborhood_coverage >= min_coverage:
        training_route = "LOCAL_REGULAR"
    else:
        training_route = "GLOBAL_ONLY"

    if exact and training_route == "LOCAL_REGULAR":
        attribution_source = "EXACT_LOCAL"
    elif training_route == "LOCAL_REGULAR":
        attribution_source = "NEARBY_ACCEPTED"
    elif training_route == "GLOBAL_ONLY":
        attribution_source = "OUTSIDE_NEIGHBORHOOD"
    else:
        attribution_source = "FAST_DISTURBANCE"

    if training_route == "LOCAL_REGULAR":
        evidence_weight = 1.0 if exact else neighborhood_coverage
    elif training_route == "GLOBAL_ONLY":
        evidence_weight = float(
            training.get("plant_action_prior", {}).get(
                "global_only_evidence_weight", 0.50
            )
        )
    else:
        evidence_weight = 1.0

    return {
        "start_grid_id": start_grid,
        "end_grid_id": end_grid,
        "anchor_grid_id": anchor_grid,
        "grid_transition_path": ">".join(grid_path),
        "grid_change_count": max(0, len(grid_path) - 1),
        "start_condition_label": start_label,
        "end_condition_label": end_label,
        "anchor_condition_label": anchor_label,
        "condition_label": anchor_label,
        "condition_label_path": ">".join(label_path),
        "condition_label_change_count": max(0, len(label_path) - 1),
        "max_load_grid_offset": max_load_offset,
        "max_inlet_so2_grid_offset": max_inlet_offset,
        "valid_grid_point_count": valid_grid_point_count,
        "neighborhood_inside_point_count": inside_count,
        "neighborhood_coverage_ratio": neighborhood_coverage,
        "attribution_source": attribution_source,
        "training_route": training_route,
        "evidence_weight": float(max(0.0, min(1.0, evidence_weight))),
        "is_transient": training_route == "TRANSIENT",
        "anchor_row": anchor_row,
    }


def detect_supply_pump_state_change(
    identity_window: pd.DataFrame, plant: dict[str, Any]
) -> tuple[bool, list[str]]:
    """检测供浆泵启停或运行组合变化。

    仅检查 PLANT_CONFIG.supply_pump_state_columns 中实际配置的字段；空列表表示
    当前厂没有接入供浆泵启停测点，此规则不生效。
    """
    changed: list[str] = []
    for column in plant.get("supply_pump_state_columns", []) or []:
        if column not in identity_window.columns:
            continue
        values = identity_window[column].dropna().map(lambda v: str(v).strip()).tolist()
        values = [v for v in values if v and v.lower() not in {"nan", "none"}]
        if len(set(values)) > 1:
            changed.append(str(column))
    return bool(changed), changed


def distance_mapping_weight(
    load_offset: int,
    inlet_offset: int,
    max_load_offset: int,
    max_inlet_offset: int,
    mode: str = "LINEAR_AXIS",
) -> float:
    """临近工况映射权重；不会突破空间硬半径。"""
    if load_offset > max_load_offset or inlet_offset > max_inlet_offset:
        return 0.0
    mode = str(mode).upper()
    if mode == "UNIFORM":
        return 1.0
    if mode == "INVERSE_DISTANCE":
        return 1.0 / (1.0 + load_offset + inlet_offset)
    # LINEAR_AXIS：两个轴分别线性衰减后相乘。
    load_term = 1.0 - load_offset / max(max_load_offset + 1.0, 1.0)
    inlet_term = 1.0 - inlet_offset / max(max_inlet_offset + 1.0, 1.0)
    return float(max(0.0, min(1.0, load_term * inlet_term)))


def minimum_offsets_to_grids(
    source_grid_id: Any, target_grid_ids: Iterable[Any]
) -> tuple[int, int] | None:
    source = parse_grid_id(source_grid_id)
    if source is None:
        return None
    candidates: list[tuple[int, int]] = []
    for target in target_grid_ids:
        parsed = parse_grid_id(target)
        if parsed is None:
            continue
        candidates.append((abs(source[0] - parsed[0]), abs(source[1] - parsed[1])))
    if not candidates:
        return None
    # 先最小化归一化后的“矩形距离”，再返回两个轴偏移。
    return min(candidates, key=lambda item: (max(item), sum(item), item[0], item[1]))


def connected_grid_region_count(grid_ids: Iterable[Any]) -> int:
    """按上下左右相邻统计基础格集合包含多少个不连通区域。"""
    points = {parsed for parsed in (parse_grid_id(v) for v in grid_ids) if parsed}
    if not points:
        return 0
    unseen = set(points)
    regions = 0
    while unseen:
        regions += 1
        start = unseen.pop()
        queue: deque[tuple[int, int]] = deque([start])
        while queue:
            p, s = queue.popleft()
            for neighbor in ((p - 1, s), (p + 1, s), (p, s - 1), (p, s + 1)):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
    return regions


def finite_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
