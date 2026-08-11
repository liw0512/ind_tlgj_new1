from __future__ import annotations

"""V1.8B 严格等价性能优化的基础回归测试。

检查内容：
1. searchsorted 时间窗口与原来的闭区间布尔筛选完全一致；
2. 有序排除区间索引与逐区间 any(...) 判断一致；
3. 预计算的 grid→目标工况邻域映射与 V1.7 逐条距离计算一致。
"""

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from slurry_policy_config import TRAINING_CONFIG
from _engine.aggregator import build_neighbor_mapping_table
from _engine.spatial_policy import distance_mapping_weight, minimum_offsets_to_grids
from _engine.time_index import IntervalOverlapIndex, TimeWindowIndexer


def _test_time_window_indexer() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=31, freq="30s"),
            "value": range(31),
        }
    )
    indexer = TimeWindowIndexer(frame, "date")
    windows = [
        (frame["date"].iloc[0], frame["date"].iloc[0]),
        (frame["date"].iloc[2], frame["date"].iloc[9]),
        (frame["date"].iloc[0] - pd.Timedelta(minutes=2), frame["date"].iloc[4]),
        (frame["date"].iloc[20], frame["date"].iloc[-1] + pd.Timedelta(minutes=2)),
    ]
    for start, end in windows:
        expected = frame[(frame["date"] >= start) & (frame["date"] <= end)]
        actual = indexer.slice(start, end)
        assert actual.index.tolist() == expected.index.tolist()
        assert actual["value"].tolist() == expected["value"].tolist()


def _test_interval_overlap_index() -> None:
    intervals = [
        (pd.Timestamp("2026-01-01 00:05"), pd.Timestamp("2026-01-01 00:10")),
        (pd.Timestamp("2026-01-01 00:09"), pd.Timestamp("2026-01-01 00:12")),
        (pd.Timestamp("2026-01-01 00:20"), pd.Timestamp("2026-01-01 00:25")),
    ]
    index = IntervalOverlapIndex(intervals)
    queries = [
        ("00:00", "00:04"),
        ("00:04", "00:05"),
        ("00:11", "00:13"),
        ("00:13", "00:19"),
        ("00:25", "00:30"),
    ]
    for start_text, end_text in queries:
        start = pd.Timestamp(f"2026-01-01 {start_text}")
        end = pd.Timestamp(f"2026-01-01 {end_text}")
        expected = any(not (end < left or start > right) for left, right in intervals)
        assert index.overlaps(start, end) == expected


def _legacy_neighbor_rows(source_grid_ids, target_grids, training):
    cfg = training["neighbor_policy"]
    max_load = int(training["condition_attribution"]["max_load_grid_offset"])
    max_inlet = int(training["condition_attribution"]["max_inlet_so2_grid_offset"])
    minimum_weight = float(cfg["minimum_mapping_weight"])
    mode = str(cfg["distance_weight_mode"])
    rows = []
    for source_grid_id in source_grid_ids:
        for label, member_grids in target_grids.items():
            offsets = minimum_offsets_to_grids(source_grid_id, member_grids)
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
                    str(label),
                    int(offsets[0]),
                    int(offsets[1]),
                    round(float(weight), 12),
                )
            )
    return sorted(rows)


def _test_neighbor_mapping() -> None:
    source_grids = ["P10-S10", "P11-S11", "P13-S14", "P20-S20"]
    target_grids = {
        "365": ["P10-S10", "P10-S11"],
        "366": ["P12-S13"],
        "500": ["P19-S20", "P20-S20"],
    }
    expected = _legacy_neighbor_rows(source_grids, target_grids, TRAINING_CONFIG)
    actual_frame = build_neighbor_mapping_table(
        source_grids, target_grids, TRAINING_CONFIG
    )
    actual = sorted(
        (
            str(row.anchor_grid_id),
            str(row.neighbor_target_condition_label),
            int(row.neighbor_load_grid_offset),
            int(row.neighbor_inlet_so2_grid_offset),
            round(float(row.neighbor_mapping_weight), 12),
        )
        for row in actual_frame.itertuples(index=False)
    )
    assert actual == expected


def main() -> None:
    _test_time_window_indexer()
    _test_interval_overlap_index()
    _test_neighbor_mapping()
    print("V1.8B 等价优化测试通过：时间窗口、排除区间和邻域映射结果一致。")


if __name__ == "__main__":
    main()
