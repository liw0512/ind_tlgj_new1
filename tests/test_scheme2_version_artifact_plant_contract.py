import copy
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from system.model.config.mfac_plant_contract import (
    plant_contract_snapshot,
    target_supply_flow_contract,
    validate_plant_contract_snapshot,
)
from system.model.config.plant_config import PLANT_CONFIG
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
        """Build through the real first-module -> MFAC artifact contract.

        ``build_mfac_version_artifact`` now intentionally executes the real
        historical MFAC offline pipeline.  These plant-contract tests therefore
        must use the same minimum valid ConditionSnapshot/labeled-CSV contract as
        Process4MapControl instead of the old ``{"snapshot_version": ...}``
        stub.  The data can remain dynamically uninformative; the tests here are
        about artifact/plant-contract validation, not gain quality.
        """
        root_path = Path(root)
        condition = root_path / "condition_snapshot.json"
        condition.write_text(
            json.dumps(
                {
                    "snapshot_version": "v001",
                    "previous_snapshot_version": None,
                    "grid_catalog": {
                        "P1-S1": {
                            "policy_region_id": "R1",
                            "load_level": 1,
                            "inlet_so2_level": 1,
                        }
                    },
                    "policy_regions": {
                        "R1": {
                            "condition_label": "C1",
                            "status": "INDEPENDENT",
                            "member_grid_ids": ["P1-S1"],
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        times = pd.date_range("2026-08-27 10:00:00", periods=240, freq="10s")
        condition_axes = [
            str(item["column"])
            for item in PLANT_CONFIG.get("condition_axes", [])
        ]
        rows = []
        for timestamp in times:
            row = {
                "date": timestamp,
                "jyq_SO2": 8.0,
                "condition_snapshot_version": "v001",
                "grid_id": "P1-S1",
                "condition_label": "C1",
                "policy_region_id": "R1",
                "state_key": "P1-S1",
                "condition_valid": True,
                "out_of_range_clipped": False,
                "fast_change_mode": "STABLE",
            }
            for axis_index, column in enumerate(condition_axes):
                row[column] = 1500.0 + 10.0 * axis_index
            for tower in PLANT_CONFIG.get("towers", []) or []:
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
        input_csv = root_path / "input.csv"
        pd.DataFrame(rows).to_csv(input_csv, index=False)

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