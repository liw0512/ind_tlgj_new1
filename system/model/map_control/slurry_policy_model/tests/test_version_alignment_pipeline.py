from __future__ import annotations

"""完整初次 v001 + 增量合并 v002 回归测试。

该测试构造两个基础格：v001 分属工况 1、2；v002 合并为工况 1。
检查第二模块 v002 只生成 condition_label_1，且旧工况 2 的 episode 已继承。
"""

import copy
import json
import tempfile
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from slurry_policy_config import PLANT_CONFIG, TRAINING_CONFIG
from slurry_policy_core import run_initial_training, run_incremental_training


def _snapshot(path: Path, version: str, merged: bool) -> None:
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
            "P1-S1": {"grid_id": "P1-S1", "load_level": 1, "inlet_so2_level": 1, "policy_region_id": "R_0001"},
            "P1-S2": {"grid_id": "P1-S2", "load_level": 1, "inlet_so2_level": 2, "policy_region_id": "R_0001" if merged else "R_0002"},
        },
        "grid_adjacency": {"P1-S1": ["P1-S2"], "P1-S2": ["P1-S1"]},
        "policy_regions": regions,
        "metadata": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _rows(version: str, blocks: list[tuple[str, str, float, int]], day: int) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp(f"2026-01-{day:02d} 00:00:00")
    for block_index, (grid, label, inlet, minutes) in enumerate(blocks):
        start = base + pd.Timedelta(minutes=block_index * 90)
        for i in range(minutes * 2 + 1):
            t = start + pd.Timedelta(seconds=30 * i)
            merged = version == "v002"
            region = "R_0001" if merged or label == "1" else "R_0002"
            rows.append(
                {
                    "date": t,
                    "jzfh": 105.0,
                    "yyq_SO2": inlet,
                    "jyq_SO2": 20.0,
                    "condition_snapshot_version": version,
                    "grid_id": grid,
                    "base_condition_id": "1" if grid == "P1-S1" else "2",
                    "condition_label": label,
                    "policy_region_id": region,
                    "region_status": "AUTO_PROVISIONAL_MERGE" if merged else "INDEPENDENT",
                    "region_member_count": 2 if merged else 1,
                    "coverage_status": "MATURE",
                    "state_key": "XP2-AP1-NORMAL-SUPPLY_NORMAL",
                    "condition_experience_source": "LOCAL_GRID",
                    "condition_valid": True,
                    "out_of_range_clipped": False,
                    "clip_axis": "none",
                    "condition_reason": "OK",
                    "xstjy_PH": 5.2,
                    "aptjy_PH": 6.0,
                    "xst_FMKD1": 30.0,
                    "xst_FMKD2": 30.0,
                    "apt_FMKD": 25.0,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshots = root / "condition_snapshots"
        _snapshot(snapshots / "v001" / "condition_snapshot.json", "v001", False)
        _snapshot(snapshots / "v002" / "condition_snapshot.json", "v002", True)
        initial_csv = root / "initial.csv"
        incremental_csv = root / "incremental.csv"
        _rows("v001", [("P1-S1", "1", 600.0, 60), ("P1-S2", "2", 800.0, 60)], 1).to_csv(initial_csv, index=False, encoding="utf-8-sig")
        _rows("v002", [("P1-S2", "1", 800.0, 60)], 2).to_csv(incremental_csv, index=False, encoding="utf-8-sig")

        config_path = root / "config.py"
        plant = copy.deepcopy(PLANT_CONFIG)
        training = copy.deepcopy(TRAINING_CONFIG)
        plant["paths"]["default_initial_input"] = str(initial_csv)
        plant["paths"]["default_incremental_input"] = str(incremental_csv)
        plant["paths"]["output_root"] = str(root / "output")
        plant["paths"]["condition_snapshots_dir"] = str(snapshots)
        training["progress"]["enabled"] = False
        config_path.write_text(
            "PLANT_CONFIG = " + repr(plant) + "\nTRAINING_CONFIG = " + repr(training) + "\n",
            encoding="utf-8",
        )

        first = run_initial_training(
            config_spec=str(config_path),
            condition_snapshot=str(snapshots / "v001" / "condition_snapshot.json"),
            progress_enabled=False,
        )
        assert first.name == "v001"

        second = run_incremental_training(
            config_spec=str(config_path),
            condition_snapshot=str(snapshots / "v002" / "condition_snapshot.json"),
            progress_enabled=False,
        )
        assert second.name == "v002"
        assert (second / "datasets" / "valid_decision_episodes.pkl").exists()
        assert (second / "datasets" / "invalid_decision_episodes.pkl").exists()
        assert (second / "datasets" / "context_tail.pkl").exists()
        assert (second / "performance_report.json").exists()
        condition_dirs = sorted(path.name for path in (second / "conditions").iterdir() if path.is_dir())
        assert condition_dirs == ["condition_label_1"], condition_dirs
        info = json.loads((second / "conditions" / "condition_label_1" / "condition_policy_info.json").read_text(encoding="utf-8"))
        assert info["condition_identity"]["member_grid_ids"] == ["P1-S1", "P1-S2"]
        assert info["condition_identity"]["historical_source_condition_labels"] == ["1", "2"]
        assert info["condition_identity"]["remapped_episode_count"] > 0

    print("V1.8B 端到端测试通过：v001→v002 合并后 PKL 和历史 episode 已正确对齐。")


if __name__ == "__main__":
    main()
