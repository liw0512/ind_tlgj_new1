import json
from pathlib import Path
import tempfile
import threading
import unittest

from system.model.Process4MapControl import ProcessForMapConsole as LegacyProcess4
from system.model.map_control.mfac_model.activate_mfac_version import (
    validate_offline_lifecycle_artifacts,
)
from system.model.map_control.mfac_model.version_artifacts import sha256_file


class Scheme2Process4OfflineLifecycleTest(unittest.TestCase):
    @staticmethod
    def _write_json(path: Path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return path

    @classmethod
    def _valid_artifact_tree(cls, root: Path, *, days=7):
        version = "v001"
        snapshot_dir = root / "mfac" / "snapshots" / version
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        condition_path = cls._write_json(
            root / "condition" / version / "condition_snapshot.json",
            {"snapshot_version": version},
        )
        offline_path = cls._write_json(
            snapshot_dir / "offline_training_report.json",
            {
                "version": version,
                "mode": "INITIAL",
                "lifecycle_contract": {
                    "periodic_offline_retrain_days": days,
                    "offline_order": ["CONDITION", "MFAC"],
                    "online_update_trigger": "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
                    "online_update_is_periodic": False,
                    "historical_prior_may_overwrite_online_evidence": False,
                },
            },
        )
        episodes_path = snapshot_dir / "historical_valid_episodes.csv"
        episodes_path.write_text("episode_id\n", encoding="utf-8")
        effective_path = cls._write_json(
            snapshot_dir / "offline_effective_config.json",
            {"config": "test"},
        )
        summary_path = cls._write_json(
            snapshot_dir / "training_summary.json",
            {
                "version": version,
                "mode": "INITIAL",
                "condition_snapshot_version": version,
                "periodic_offline_retrain_days": days,
                "online_update_trigger": "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
                "online_runtime_state_overwrite": False,
            },
        )
        manifest = {
            "version": version,
            "condition_snapshot_version": version,
            "condition_snapshot_path": str(condition_path.resolve()),
            "condition_snapshot_sha256": sha256_file(condition_path),
            "training_summary_path": str(summary_path.resolve()),
            "training_summary_sha256": sha256_file(summary_path),
            "offline_training_report_path": str(offline_path.resolve()),
            "offline_training_report_sha256": sha256_file(offline_path),
            "historical_valid_episodes_path": str(episodes_path.resolve()),
            "historical_valid_episodes_sha256": sha256_file(episodes_path),
            "offline_effective_config_path": str(effective_path.resolve()),
            "offline_effective_config_sha256": sha256_file(effective_path),
            "persisted_online_state_precedence": True,
            "cross_snapshot_online_state_reuse": True,
            "cross_snapshot_online_state_reuse_policy": (
                "SAME_MFAC_CONTEXT_AND_GRID_ONLY"
            ),
            "cross_snapshot_online_state_requires_runtime_grid_id": True,
            "cross_snapshot_residual_reuse": False,
            "cross_snapshot_pending_or_hold_reuse": False,
            "historical_prior_may_overwrite_online_evidence": False,
        }
        cls._write_json(snapshot_dir / "manifest.json", manifest)
        return version, snapshot_dir, condition_path

    def test_activation_accepts_seven_day_offline_plus_event_driven_online_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            version, snapshot_dir, condition_path = self._valid_artifact_tree(
                Path(temp)
            )
            result = validate_offline_lifecycle_artifacts(
                version=version,
                snapshot_dir=snapshot_dir,
                condition_path=condition_path,
            )
            self.assertEqual(result["offline_order"], ["CONDITION", "MFAC"])
            self.assertEqual(result["periodic_offline_retrain_days"], 7)
            self.assertFalse(result["online_update_is_periodic"])
            self.assertEqual(
                result["online_update_trigger"],
                "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
            )
            self.assertTrue(result["persisted_online_state_precedence"])
            self.assertEqual(
                result["cross_snapshot_online_state_reuse_policy"],
                "SAME_MFAC_CONTEXT_AND_GRID_ONLY",
            )
            self.assertFalse(result["cross_snapshot_residual_reuse"])

    def test_activation_rejects_old_three_day_offline_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            version, snapshot_dir, condition_path = self._valid_artifact_tree(
                Path(temp),
                days=3,
            )
            with self.assertRaisesRegex(ValueError, "7 days"):
                validate_offline_lifecycle_artifacts(
                    version=version,
                    snapshot_dir=snapshot_dir,
                    condition_path=condition_path,
                )

    @staticmethod
    def _bare_process4(*, initial: bool):
        process = LegacyProcess4.__new__(LegacyProcess4)
        process.is_initial_training = bool(initial)
        process.is_training = True
        process.model_training_completed = False
        process.last_training_time = None
        process.training_lock = threading.Lock()
        process.slurry_core_config = {"initial_version": "v001"}
        process.system_state = (
            process.SystemState.MODEL_TRAINING
            if initial
            else process.SystemState.NORMAL_OPERATION
        )
        process._load_training_data = lambda mode: (
            object(),
            {"mode": mode},
        )
        process._save_training_work_csv = lambda frame, settings: "training.csv"
        process._training_env = lambda: (".", {})
        return process

    def test_process4_initial_training_order_is_condition_then_mfac_then_activation(self):
        process = self._bare_process4(initial=True)
        order = []
        process._run_condition_initial = lambda *args: (
            order.append("CONDITION") or ("labeled.csv", "condition.json")
        )
        process._run_policy_initial = lambda *args: order.append("MFAC")
        process.hot_update_models = lambda version: (
            order.append("ACTIVATE") or True
        )

        LegacyProcess4._do_training(process)

        self.assertEqual(order, ["CONDITION", "MFAC", "ACTIVATE"])
        self.assertTrue(process.model_training_completed)
        self.assertEqual(process.system_state, process.SystemState.NORMAL_OPERATION)
        self.assertFalse(process.is_initial_training)
        self.assertFalse(process.is_training)

    def test_process4_incremental_training_order_is_condition_then_mfac_then_activation(self):
        process = self._bare_process4(initial=False)
        order = []
        process._read_active_version = lambda: "v001"
        process._next_version = lambda version: "v002"
        process._run_condition_incremental = lambda *args: (
            order.append("CONDITION") or ("labeled.csv", "condition-v002.json")
        )
        process._run_policy_incremental = lambda *args: order.append("MFAC")
        process.hot_update_models = lambda version: (
            order.append("ACTIVATE") or True
        )

        LegacyProcess4._do_training(process)

        self.assertEqual(order, ["CONDITION", "MFAC", "ACTIVATE"])
        self.assertEqual(process.system_state, process.SystemState.NORMAL_OPERATION)
        self.assertFalse(process.is_training)


if __name__ == "__main__":
    unittest.main()
