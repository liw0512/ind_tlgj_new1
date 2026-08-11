from __future__ import annotations

"""验证关闭完整 CSV 后，增量训练仍能只依赖 Pickle 正常继承。"""

import copy
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from slurry_policy_config import PLANT_CONFIG, TRAINING_CONFIG
from slurry_policy_core import run_initial_training, run_incremental_training
from export_episode_csv import export_episode_csv
from test_version_alignment_pipeline import _rows, _snapshot


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshots = root / "condition_snapshots"
        _snapshot(snapshots / "v001" / "condition_snapshot.json", "v001", False)
        _snapshot(snapshots / "v002" / "condition_snapshot.json", "v002", True)
        initial_csv = root / "initial.csv"
        incremental_csv = root / "incremental.csv"
        _rows(
            "v001",
            [("P1-S1", "1", 600.0, 60), ("P1-S2", "2", 800.0, 60)],
            1,
        ).to_csv(initial_csv, index=False, encoding="utf-8-sig")
        _rows("v002", [("P1-S2", "1", 800.0, 60)], 2).to_csv(
            incremental_csv, index=False, encoding="utf-8-sig"
        )

        plant = copy.deepcopy(PLANT_CONFIG)
        training = copy.deepcopy(TRAINING_CONFIG)
        plant["paths"]["default_initial_input"] = str(initial_csv)
        plant["paths"]["default_incremental_input"] = str(incremental_csv)
        plant["paths"]["output_root"] = str(root / "output")
        plant["paths"]["condition_snapshots_dir"] = str(snapshots)
        training["progress"]["enabled"] = False
        training["output"]["write_full_episode_csv"] = False
        training["output"]["write_context_tail_csv"] = False

        config_path = root / "config.py"
        config_path.write_text(
            "PLANT_CONFIG = "
            + repr(plant)
            + "\nTRAINING_CONFIG = "
            + repr(training)
            + "\n",
            encoding="utf-8",
        )

        first = run_initial_training(
            config_spec=str(config_path),
            condition_snapshot=str(
                snapshots / "v001" / "condition_snapshot.json"
            ),
            progress_enabled=False,
        )
        second = run_incremental_training(
            config_spec=str(config_path),
            condition_snapshot=str(
                snapshots / "v002" / "condition_snapshot.json"
            ),
            progress_enabled=False,
        )

        for snapshot in (first, second):
            datasets = snapshot / "datasets"
            assert (datasets / "valid_decision_episodes.pkl").exists()
            assert (datasets / "invalid_decision_episodes.pkl").exists()
            assert (datasets / "context_tail.pkl").exists()
            assert not (datasets / "valid_decision_episodes.csv").exists()
            assert not (datasets / "context_tail.csv").exists()

        exported = export_episode_csv(second)
        assert len(exported) == 3
        assert (second / "datasets" / "valid_decision_episodes.csv").exists()
        assert (second / "datasets" / "context_tail.csv").exists()

    print("V1.8B Pickle-only 测试通过：初次、增量继承和按需 CSV 导出正常。")


if __name__ == "__main__":
    main()
