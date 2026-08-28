import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.config.standard_fields import LIQUID_GAS_RATIO_COLUMN
from system.model.map_control.condition_model.initial_condition_builder import (
    build_initial_condition_csv,
)
from system.model.map_control.mfac_model.version_artifacts import (
    build_mfac_version_artifact,
)


class Scheme2OfflineEndToEndContractTest(unittest.TestCase):
    @staticmethod
    def _raw_frame(periods=240):
        times = pd.date_range("2026-07-01", periods=periods, freq="10s")
        rows = []
        for timestamp in times:
            row = {
                "date": timestamp,
                "yyq_SO2": 1500.0,
                "yyq_LL": 1_700_000.0,
                "jyq_SO2": 8.0,
                LIQUID_GAS_RATIO_COLUMN: 10.0,
                "outlet_so2_target": 8.0,
                "fast_change_mode": "STABLE",
            }
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
        return pd.DataFrame(rows)

    def test_raw_data_flows_condition_then_mfac_with_same_version(self):
        """Lock the real module-1 -> module-2 artifact interface.

        The synthetic frame is deliberately small because the 7-day admission
        gate belongs to Process4 and is covered separately. This test verifies
        that once data has been admitted, the actual condition builder produces
        the labelled CSV and immutable snapshot consumed by the actual MFAC
        offline version builder without a second mapping/config source.
        """
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw_csv = root / "raw.csv"
            labelled_csv = root / "after_condition.csv"
            condition_snapshot = root / "condition" / "v001" / "condition_snapshot.json"
            self._raw_frame().to_csv(raw_csv, index=False, encoding="utf-8-sig")

            output = build_initial_condition_csv(
                input_csv_path=str(raw_csv),
                output_csv_path=str(labelled_csv),
                snapshot_output_path=str(condition_snapshot),
                snapshot_version="v001",
            )
            self.assertEqual(Path(output).resolve(), labelled_csv.resolve())
            self.assertTrue(condition_snapshot.is_file())

            labelled = pd.read_csv(labelled_csv, encoding="utf-8-sig")
            for column in (
                "condition_snapshot_version",
                "grid_id",
                "condition_label",
                "policy_region_id",
                "state_key",
            ):
                self.assertIn(column, labelled.columns)
            self.assertEqual(set(labelled["condition_snapshot_version"]), {"v001"})

            snapshot_value = json.loads(condition_snapshot.read_text(encoding="utf-8"))
            self.assertEqual(snapshot_value["snapshot_version"], "v001")

            manifest = build_mfac_version_artifact(
                input_csv=str(labelled_csv),
                output_root=str(root / "mfac"),
                condition_snapshot=str(condition_snapshot),
                mode="INITIAL",
            )
            self.assertEqual(manifest["version"], "v001")
            self.assertEqual(manifest["condition_snapshot_version"], "v001")
            self.assertEqual(
                Path(manifest["condition_snapshot_path"]).resolve(),
                condition_snapshot.resolve(),
            )
            self.assertEqual(manifest["training_summary"]["required_training_days"], 7)
            self.assertEqual(manifest["training_summary"]["initial_training_days"], 7)
            self.assertEqual(manifest["training_summary"]["incremental_training_days"], 3)
            self.assertEqual(
                manifest["training_summary"]["online_update_trigger"],
                "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
            )


if __name__ == "__main__":
    unittest.main()
