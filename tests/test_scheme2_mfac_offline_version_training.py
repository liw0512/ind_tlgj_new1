import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.config.process4map_config import PROCESS4MAP_CONFIG
from system.model.map_control.mfac_model.historical_episode_engine.exceptions import (
    ConfigurationError,
)
from system.model.map_control.mfac_model.offline_training_config import (
    OFFLINE_ONLINE_LIFECYCLE_CONTRACT,
)
from system.model.map_control.mfac_model.offline_version_training import (
    train_mfac_offline_version,
)
from system.model.map_control.mfac_model.version_artifacts import (
    build_mfac_version_artifact,
)


class Scheme2MFACOfflineVersionTrainingTest(unittest.TestCase):
    @staticmethod
    def _snapshot(path: Path, version: str, label: str, previous=None):
        value = {
            "snapshot_version": version,
            "previous_snapshot_version": previous,
            "grid_catalog": {
                "P1-S1": {
                    "policy_region_id": "R1",
                    "load_level": 1,
                    "inlet_so2_level": 1,
                }
            },
            "policy_regions": {
                "R1": {
                    "condition_label": label,
                    "status": "INDEPENDENT",
                    "member_grid_ids": ["P1-S1"],
                }
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _frame(version: str, label: str, periods=240):
        times = pd.date_range("2026-07-01", periods=periods, freq="10s")
        rows = []
        condition_axes = [
            str(item["column"])
            for item in PLANT_CONFIG.get("condition_axes", [])
        ]
        for index, timestamp in enumerate(times):
            row = {
                "date": timestamp,
                "jyq_SO2": 8.0,
                "condition_snapshot_version": version,
                "grid_id": "P1-S1",
                "condition_label": label,
                "policy_region_id": "R1",
                "state_key": "P1-S1",
                "condition_valid": True,
                "out_of_range_clipped": False,
                "fast_change_mode": "STABLE",
            }
            for axis_index, column in enumerate(condition_axes):
                row[column] = 1500.0 + 10.0 * axis_index
            for tower in PLANT_CONFIG.get("towers", []):
                if not tower.get("enabled", True):
                    continue
                row[str(tower["ph_column"])] = 6.2
                for flow in tower.get("supply_flows", []) or []:
                    row[str(flow["column"])] = 30.0
                for pump in tower.get("circulation_pumps", []) or []:
                    column = str(pump.get("value_column") or "").strip()
                    if column:
                        row[column] = 100.0
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def _previous_episode(version="v001", label="C1"):
        return pd.DataFrame(
            [
                {
                    "episode_id": "legacy-episode-1",
                    "condition_snapshot_version": version,
                    "condition_label": label,
                    "anchor_condition_label": label,
                    "anchor_grid_id": "P1-S1",
                    "start_grid_id": "P1-S1",
                    "end_grid_id": "P1-S1",
                    "grid_transition_path": "P1-S1",
                    "grid_change_count": 0,
                    "condition_label_change_count": 0,
                    "policy_region_id": "R1",
                    "base_condition_id": "1",
                    "action_start_time": "2026-06-25T01:00:00",
                    "valid": True,
                    "flow_effect_complete": True,
                    "mfac_dynamic_evidence_eligible": True,
                    "mfac_safety_evidence": False,
                    "condition_valid": True,
                    "followup_action_in_response": False,
                    "condition_remapped": False,
                    "flow_shape": "PULSE",
                    "flow_event_final_delta_flow": 50.0,
                    "delta_outlet_so2": -2.0,
                    "before_condition_axis_1": 1500.0,
                    "before_outlet_so2": 9.0,
                    "flow_event_active_duration_minutes": 8.0,
                    "before_condition_axis_1_rate": 0.0,
                    "before_outlet_so2_rate": 0.0,
                    "flow_event_extra_slurry_volume": 6.5,
                    "before_ph__xst": 6.2,
                    "delta_ph__xst": 0.2,
                    "evidence_weight": 1.0,
                }
            ]
        )

    def test_process4_periodic_offline_refresh_is_seven_days(self):
        training = PROCESS4MAP_CONFIG.training
        self.assertEqual(training.initial_training_days, 7)
        self.assertEqual(training.incremental_trigger_interval_days, 7)
        self.assertEqual(training.incremental_training_days, 7)
        self.assertEqual(training.incremental_minimum_records, 54_432)
        self.assertEqual(
            OFFLINE_ONLINE_LIFECYCLE_CONTRACT["offline_order"],
            ["CONDITION", "MFAC"],
        )
        self.assertFalse(
            OFFLINE_ONLINE_LIFECYCLE_CONTRACT["online_update_is_periodic"]
        )
        self.assertEqual(
            OFFLINE_ONLINE_LIFECYCLE_CONTRACT["online_update_trigger"],
            "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
        )

    def test_snapshot_label_mismatch_fails_before_mfac_training(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = self._snapshot(root / "condition.json", "v001", "C1")
            csv_path = root / "train.csv"
            self._frame("v001", "C2").to_csv(csv_path, index=False)
            with self.assertRaises(ConfigurationError):
                train_mfac_offline_version(
                    input_csv=str(csv_path),
                    output_root=str(root / "mfac"),
                    condition_snapshot=str(snapshot),
                    mode="INITIAL",
                )

    def test_version_builder_executes_real_offline_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = self._snapshot(root / "condition.json", "v001", "C1")
            csv_path = root / "train.csv"
            self._frame("v001", "C1").to_csv(csv_path, index=False)
            manifest = build_mfac_version_artifact(
                input_csv=str(csv_path),
                output_root=str(root / "mfac"),
                condition_snapshot=str(snapshot),
                mode="INITIAL",
            )
            summary = manifest["training_summary"]
            self.assertNotEqual(
                summary["bootstrap_status"],
                "NOT_ACTIVATED_IN_PRIMARY_REPLACEMENT",
            )
            self.assertEqual(
                summary["bootstrap_status"],
                "HISTORICAL_PRIOR_REVIEW_REQUIRED",
            )
            self.assertTrue(Path(manifest["offline_training_report_path"]).is_file())
            self.assertTrue(Path(manifest["historical_valid_episodes_path"]).is_file())
            self.assertTrue(Path(manifest["offline_effective_config_path"]).is_file())
            self.assertFalse(manifest["runtime_prior_reviewed"])
            self.assertFalse(manifest["runtime_prior_allowed"])
            self.assertTrue(manifest["persisted_online_state_precedence"])
            self.assertFalse(manifest["cross_snapshot_online_state_reuse"])

    def test_incremental_training_remaps_and_keeps_previous_episode_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_root = root / "mfac"
            previous_snapshot = self._snapshot(
                root / "v001_condition.json", "v001", "C1"
            )
            current_snapshot = self._snapshot(
                root / "v002_condition.json", "v002", "C2", previous="v001"
            )
            previous_dir = output_root / "snapshots" / "v001"
            previous_dir.mkdir(parents=True, exist_ok=True)
            self._previous_episode().to_csv(
                previous_dir / "historical_valid_episodes.csv", index=False
            )

            csv_path = root / "incremental.csv"
            self._frame("v002", "C2").to_csv(csv_path, index=False)
            report = train_mfac_offline_version(
                input_csv=str(csv_path),
                output_root=str(output_root),
                condition_snapshot=str(current_snapshot),
                mode="INCREMENTAL",
                previous_snapshot=str(previous_snapshot),
            )
            self.assertEqual(report["cumulative_valid_episode_count"], 1)
            self.assertEqual(
                report["carry_forward"]["status"],
                "PREVIOUS_EPISODES_REMAPPED",
            )
            stored = pd.read_csv(report["historical_valid_episodes_path"])
            self.assertEqual(stored.loc[0, "condition_snapshot_version"], "v002")
            self.assertEqual(stored.loc[0, "condition_label"], "C2")
            self.assertTrue(
                str(stored.loc[0, "condition_remapped"]).lower() in {"true", "1"}
            )


if __name__ == "__main__":
    unittest.main()
