from __future__ import annotations

import json
import pickle
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from _engine.state_builder import build_policy_state
from _engine.utils import sha256_file, write_json
from slurry_policy_config import ONLINE_POLICY_CONFIG, PLANT_CONFIG, TRAINING_CONFIG
from slurry_policy_online.online_slurry_policy import OnlineSlurryPolicy


def _profile():
    return {
        "action_profile": {
            "action_family": "TOWER:xst|BALANCED",
            "direction": "INCREASE",
            "magnitude": "SMALL",
            "representative_delta": {"xst_v1": 1.0, "xst_v2": 1.0, "apt_v1": 0.0},
            "delta_distribution": {},
        },
        "so2_effect": {
            "dominant_direction": "DECREASE",
            "direction_consistency": 0.90,
            "effect_strength_mode": "SMALL",
        },
        "ph_effect": {},
        "stability": {"stable_response_ratio": 0.90, "oscillation_ratio": 0.05},
        "safety": {"any_safety_violation_ratio": 0.02},
        "support": {"effective_weighted_event_count": 10.0},
        "spatial_support": {"direction_generalizable": True},
        "reliability": {
            "total_score": 88.0,
            "safety_history_score": 98.0,
            "stability_score": 90.0,
        },
        "profile_status": "SUPPORTED",
    }


class OnlinePolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.output_root = root / "output"
        self.condition_root = root / "condition"
        self.runtime_root = root / "runtime"
        self.snapshot = self.output_root / "snapshots" / "v001"
        self.snapshot.mkdir(parents=True)
        condition_path = self.condition_root / "v001" / "condition_snapshot.json"
        condition_path.parent.mkdir(parents=True)
        write_json(condition_path, {"condition_snapshot_version": "v001"})

        plant = json.loads(json.dumps(PLANT_CONFIG))
        training = json.loads(json.dumps(TRAINING_CONFIG))
        online = json.loads(json.dumps(ONLINE_POLICY_CONFIG))
        plant["paths"].update(
            {
                "output_root": str(self.output_root),
                "condition_snapshots_dir": str(self.condition_root),
                "active_policy_version_file": str(self.output_root / "active_version.json"),
                "online_runtime_dir": str(self.runtime_root),
            }
        )
        online["model_loading"]["reload_check_interval_seconds"] = 0.0
        online["logging"]["enabled"] = True
        online["so2_control"]["target_transition_enabled"] = False

        process = {
            "jzfh": 350.0,
            "yyq_SO2": 3000.0,
            "jyq_SO2": 24.0,
            "xstjy_PH": 5.0,
            "aptjy_PH": 6.0,
            "xst_FMKD1": 30.0,
            "xst_FMKD2": 31.0,
            "apt_FMKD": 25.0,
        }
        row = {
            "anchor_grid_id": "P1-S1",
            "condition_state_key": "MODE=A",
            "before_outlet_so2": process["jyq_SO2"],
            "before_outlet_so2_rate": 0.0,
            "disturbance_mode": "STEADY",
            "before_ph__xst": process["xstjy_PH"],
            "before_ph__apt": process["aptjy_PH"],
            "before_valve__xst_v1": process["xst_FMKD1"],
            "before_valve__xst_v2": process["xst_FMKD2"],
            "before_valve__apt_v1": process["apt_FMKD"],
        }
        _full, no_grid = build_policy_state(row, plant, training)
        action_id = "TOWER:xst|BALANCED|INCREASE|SMALL"
        local = {
            "schema_version": "1.5",
            "policy_snapshot_version": "v001",
            "condition_snapshot_version": "v001",
            "state_action_profiles": {no_grid: {action_id: _profile()}},
        }
        condition_dir = self.snapshot / "conditions" / "condition_label_365"
        condition_dir.mkdir(parents=True)
        with (condition_dir / "condition_policy.pkl").open("wb") as handle:
            pickle.dump(local, handle)
        global_dir = self.snapshot / "global"
        global_dir.mkdir()
        with (global_dir / "plant_action_prior.pkl").open("wb") as handle:
            pickle.dump(
                {
                    "schema_version": "1.5",
                    "policy_snapshot_version": "v001",
                    "condition_snapshot_version": "v001",
                    "state_action_profiles": {},
                },
                handle,
            )
        write_json(
            self.snapshot / "effective_config.json",
            {
                "plant": plant,
                "training": training,
                "disturbance": {
                    "trend_window_minutes": 5.0,
                    "load_slow_rate": 1.0,
                    "load_fast_rate": 3.0,
                    "inlet_so2_slow_rate": 20.0,
                    "inlet_so2_fast_rate": 60.0,
                },
                "condition_alignment": {
                    "condition_snapshot_version": "v001",
                    "grid_condition_mapping_sha256": "mapping-hash",
                },
            },
        )
        write_json(self.snapshot / "condition_alignment.json", {"condition_snapshot_version": "v001"})
        (self.snapshot / "grid_condition_mapping.csv").write_text(
            "condition_snapshot_version,grid_id,condition_label\nv001,P1-S1,365\n",
            encoding="utf-8",
        )
        write_json(
            self.snapshot / "training_summary.json",
            {
                "policy_snapshot_version": "v001",
                "condition_snapshot_version": "v001",
                "condition_snapshot_sha256": sha256_file(condition_path),
                "grid_condition_mapping_sha256": "mapping-hash",
            },
        )

        files = []
        for path in self.snapshot.rglob("*"):
            if path.is_file() and path.name != "manifest.json":
                files.append(
                    {
                        "path": str(path.relative_to(self.snapshot)).replace("\\", "/"),
                        "size": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
        write_json(
            self.snapshot / "manifest.json",
            {
                "policy_snapshot_version": "v001",
                "condition_snapshot_version": "v001",
                "grid_condition_mapping_sha256": "mapping-hash",
                "files": files,
            },
        )
        write_json(
            self.output_root / "active_version.json",
            {
                "policy_version": "v001",
                "condition_snapshot_version": "v001",
                "policy_snapshot_path": str(self.snapshot),
                "condition_snapshot_path": str(condition_path),
            },
        )
        self.config_path = root / "test_config.py"
        self.config_path.write_text(
            "PLANT_CONFIG = %r\nTRAINING_CONFIG = %r\nONLINE_POLICY_CONFIG = %r\n"
            % (plant, training, online),
            encoding="utf-8",
        )
        self.process = process
        self.condition = {
            "condition_snapshot_version": "v001",
            "condition_label": "365",
            "stable_condition_label": "365",
            "raw_grid_id": "P1-S1",
            "raw_condition_label": "365",
            "condition_stable": True,
            "condition_switch_state": "STABLE",
            "condition_valid": True,
            "state_key": "MODE=A",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_recommendation_and_waiting_effect(self):
        policy = OnlineSlurryPolicy(str(self.config_path))
        decision = policy.evaluate(
            {"timestamp": "2026-01-01 00:00:00", "process": self.process, "condition": self.condition},
            target=20.0,
        )
        self.assertEqual(decision["decision_status"], "RECOMMENDED")
        self.assertEqual(decision["experience_source"], "LOCAL_CONDITION")
        self.assertEqual(decision["action_direction"], "INCREASE")
        self.assertAlmostEqual(decision["recommended_valve_deltas"]["xst_v1"], 1.0)

        feedback = policy.record_execution(
            {
                "decision_id": decision["decision_id"],
                "recommendation_accepted": True,
                "actual_action_executed": True,
                "actual_execution_time": "2026-01-01 00:00:10",
            }
        )
        self.assertEqual(feedback["state_after_feedback"], "WAITING_EFFECT")

        later = policy.evaluate(
            {"timestamp": "2026-01-01 00:01:00", "process": self.process, "condition": self.condition},
            target=20.0,
        )
        self.assertEqual(later["action_family"], "HOLD")
        self.assertIn("WAITING_PREVIOUS_ACTION_EFFECT", later["reason_codes"])

    def test_unstable_condition_blocks(self):
        policy = OnlineSlurryPolicy(str(self.config_path))
        condition = dict(self.condition)
        condition["condition_stable"] = False
        decision = policy.evaluate(
            {"timestamp": "2026-01-01 00:00:00", "process": self.process, "condition": condition},
            target=20.0,
        )
        self.assertEqual(decision["decision_status"], "INITIALIZING")
        self.assertEqual(decision["action_family"], "HOLD")


if __name__ == "__main__":
    unittest.main()
