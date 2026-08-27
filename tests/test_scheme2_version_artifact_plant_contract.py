import copy
import json
import tempfile
import unittest
from pathlib import Path

from system.model.config.mfac_plant_contract import (
    plant_contract_snapshot,
    target_supply_flow_contract,
    validate_plant_contract_snapshot,
)
from system.model.map_control.condition_model.integrated_version_manager import (
    IntegratedVersionError,
    IntegratedVersionManager,
    normalize_pointer,
)
from system.model.map_control.mfac_model.mfac_primary_config import MFAC_PRIMARY_MODE
from system.model.map_control.mfac_model.version_artifacts import (
    build_mfac_version_artifact,
)


class Scheme2VersionArtifactPlantContractTest(unittest.TestCase):
    @staticmethod
    def _build(root: str):
        root_path = Path(root)
        input_csv = root_path / "input.csv"
        input_csv.write_text(
            "date,yyq_SO2\n2026-08-27 10:00:00,2000\n",
            encoding="utf-8",
        )
        condition = root_path / "condition_snapshot.json"
        condition.write_text(
            json.dumps({"snapshot_version": "v001"}),
            encoding="utf-8",
        )
        return build_mfac_version_artifact(
            input_csv=str(input_csv),
            output_root=str(root_path / "mfac"),
            condition_snapshot=str(condition),
            mode="INITIAL",
        )

    def test_manifest_captures_current_contract_as_audit_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = self._build(root)
            current = plant_contract_snapshot()
            target = target_supply_flow_contract()
            self.assertEqual(manifest["primary_mode"], MFAC_PRIMARY_MODE)
            self.assertEqual(manifest["plant_contract_snapshot"], current)
            self.assertIn(str(float(target["minimum"])), manifest["runtime_semantics"])
            self.assertIn(str(float(target["maximum"])), manifest["runtime_semantics"])
            self.assertEqual(
                manifest["plant_contract_snapshot"]["authority"],
                "PLANT_CONFIG_SNAPSHOT",
            )

    def test_contract_snapshot_drift_is_rejected(self):
        snapshot = plant_contract_snapshot()
        validate_plant_contract_snapshot(snapshot)
        drifted = copy.deepcopy(snapshot)
        drifted["target_supply_flow"]["maximum"] = (
            float(drifted["target_supply_flow"]["maximum"]) - 1.0
        )
        with self.assertRaises(ValueError):
            validate_plant_contract_snapshot(drifted)

    def test_version_manager_rejects_manifest_mode_drift(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = self._build(root)
            manifest_path = Path(manifest["manifest_path"])
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["primary_mode"] = "WRONG_MODE"
            manifest_path.write_text(json.dumps(value), encoding="utf-8")

            pointer = normalize_pointer(
                {
                    "integrated_version": "v001",
                    "condition": {
                        "version": "v001",
                        "snapshot_path": value["condition_snapshot_path"],
                    },
                    "mfac": {
                        "version": "v001",
                        "source_condition_version": "v001",
                        "snapshot_path": str(manifest_path),
                    },
                }
            )
            manager = IntegratedVersionManager(
                {"enabled": False, "active_version_file": ""}
            )
            with self.assertRaises(IntegratedVersionError):
                manager.prepare_mfac(pointer)

    def test_version_manager_rejects_stale_plant_contract_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = self._build(root)
            manifest_path = Path(manifest["manifest_path"])
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["plant_contract_snapshot"]["primary_tower"]["guard_band"] = (
                float(
                    value["plant_contract_snapshot"]["primary_tower"]["guard_band"]
                )
                + 0.01
            )
            manifest_path.write_text(json.dumps(value), encoding="utf-8")

            pointer = normalize_pointer(
                {
                    "integrated_version": "v001",
                    "condition": {
                        "version": "v001",
                        "snapshot_path": value["condition_snapshot_path"],
                    },
                    "mfac": {
                        "version": "v001",
                        "source_condition_version": "v001",
                        "snapshot_path": str(manifest_path),
                        "mode": MFAC_PRIMARY_MODE,
                    },
                }
            )
            manager = IntegratedVersionManager(
                {"enabled": False, "active_version_file": ""}
            )
            with self.assertRaises(IntegratedVersionError):
                manager.prepare_mfac(pointer)


if __name__ == "__main__":
    unittest.main()
