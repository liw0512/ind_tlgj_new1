from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _engine.condition_snapshot_bridge import (
    load_condition_snapshot_index,
    remap_episode_conditions,
    validate_input_frame_alignment,
)


def _write_snapshot(path: Path, version: str, merged: bool) -> None:
    regions = {
        "R_0001": {
            "region_id": "R_0001",
            "member_grid_ids": ["P1-S1", "P1-S2"] if merged else ["P1-S1"],
            "status": "AUTO_PROVISIONAL_MERGE" if merged else "INDEPENDENT",
            "condition_label": "1",
        }
    }
    if not merged:
        regions["R_0002"] = {
            "region_id": "R_0002",
            "member_grid_ids": ["P1-S2"],
            "status": "INDEPENDENT",
            "condition_label": "2",
        }
    value = {
        "snapshot_version": version,
        "previous_snapshot_version": "v001" if version == "v002" else None,
        "build_time": "2026-01-01T00:00:00Z",
        "grid_config": {
            "grid_definition": {
                "jzfh": {"min": 100, "max": 110, "step": 10},
                "yyq_SO2": {"min": 500, "max": 900, "step": 200},
            }
        },
        "grid_catalog": {
            "P1-S1": {
                "grid_id": "P1-S1",
                "load_level": 1,
                "inlet_so2_level": 1,
                "policy_region_id": "R_0001",
            },
            "P1-S2": {
                "grid_id": "P1-S2",
                "load_level": 1,
                "inlet_so2_level": 2,
                "policy_region_id": "R_0001" if merged else "R_0002",
            },
        },
        "grid_adjacency": {"P1-S1": ["P1-S2"], "P1-S2": ["P1-S1"]},
        "policy_regions": regions,
        "metadata": {},
    }
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot_path = root / "condition_snapshot.json"
        _write_snapshot(snapshot_path, "v002", merged=True)
        index = load_condition_snapshot_index(snapshot_path)

        assert index.snapshot_version == "v002"
        assert index.condition_members == {"1": ["P1-S1", "P1-S2"]}
        assert index.grid_records["P1-S2"].condition_label == "1"

        input_frame = pd.DataFrame(
            {
                "condition_snapshot_version": ["v002"],
                "grid_id": ["P1-S2"],
                "condition_label": ["1"],
            }
        )
        validate_input_frame_alignment(input_frame, index, context="self check")

        episodes = pd.DataFrame(
            {
                "episode_id": ["E1"],
                "condition_label": ["2"],
                "anchor_condition_label": ["2"],
                "anchor_grid_id": ["P1-S2"],
                "start_grid_id": ["P1-S2"],
                "end_grid_id": ["P1-S2"],
                "grid_transition_path": ["P1-S2"],
                "condition_snapshot_version": ["v001"],
                "policy_region_id": ["R_0002"],
                "region_status": ["INDEPENDENT"],
                "region_member_count": [1],
                "base_condition_id": ["2"],
            }
        )
        remapped, report, unresolved = remap_episode_conditions(
            episodes,
            index,
            strict=True,
            dataset_name="self_check",
        )
        row = remapped.iloc[0]
        assert unresolved.empty
        assert row["original_condition_label"] == "2"
        assert row["condition_label"] == "1"
        assert row["current_condition_snapshot_version"] == "v002"
        assert bool(row["condition_remapped"])
        assert report["remapped_episode_count"] == 1

    print("V1.8B 自检通过：第一模块版本握手、grid 映射和历史 episode 重映射正常。")


if __name__ == "__main__":
    main()
