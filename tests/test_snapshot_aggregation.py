from types import SimpleNamespace
import unittest

from system.model.map_control.snapshot_aggregation import average_snapshot_window


CONFIG = SimpleNamespace(
    latest_value_fields=(
        "date",
        "id",
        "jym",
        "connection_status",
        "outlet_so2_target",
    ),
    latest_value_prefixes=("_",),
    latest_value_suffixes=("_status", "_id", "_code", "_seq"),
)


class SnapshotAggregationTest(unittest.TestCase):
    def test_continuous_values_are_averaged(self):
        result = average_snapshot_window(
            [
                {"jyq_SO2": 10.0, "xstshsjy_LL": 30.0},
                {"jyq_SO2": 12.0, "xstshsjy_LL": 33.0},
                {"jyq_SO2": 14.0, "xstshsjy_LL": 36.0},
            ],
            CONFIG,
        )
        self.assertEqual(result["jyq_SO2"], 12.0)
        self.assertEqual(result["xstshsjy_LL"], 33.0)

    def test_discrete_and_target_fields_keep_latest_value(self):
        result = average_snapshot_window(
            [
                {
                    "date": "2026-08-21 10:00:08",
                    "id": 101,
                    "jym": 50,
                    "connection_status": True,
                    "pump_status": 0,
                    "device_id": 7,
                    "alarm_code": 0,
                    "frame_seq": 1001,
                    "outlet_so2_target": 20.0,
                },
                {
                    "date": "2026-08-21 10:00:09",
                    "id": 102,
                    "jym": 50,
                    "connection_status": True,
                    "pump_status": 0,
                    "device_id": 8,
                    "alarm_code": 0,
                    "frame_seq": 1002,
                    "outlet_so2_target": 20.0,
                },
                {
                    "date": "2026-08-21 10:00:10",
                    "id": 103,
                    "jym": 100,
                    "connection_status": False,
                    "pump_status": 1,
                    "device_id": 9,
                    "alarm_code": 2,
                    "frame_seq": 1003,
                    "outlet_so2_target": 25.0,
                },
            ],
            CONFIG,
        )
        self.assertEqual(result["date"], "2026-08-21 10:00:10")
        self.assertEqual(result["id"], 103)
        self.assertEqual(result["jym"], 100)
        self.assertIs(result["connection_status"], False)
        self.assertEqual(result["pump_status"], 1)
        self.assertEqual(result["device_id"], 9)
        self.assertEqual(result["alarm_code"], 2)
        self.assertEqual(result["frame_seq"], 1003)
        self.assertEqual(result["outlet_so2_target"], 25.0)

    def test_strings_and_internal_fields_keep_latest_value(self):
        result = average_snapshot_window(
            [
                {"mode": "A", "_snapshot_seq": 1},
                {"mode": "A", "_snapshot_seq": 2},
                {"mode": "B", "_snapshot_seq": 3},
            ],
            CONFIG,
        )
        self.assertEqual(result["mode"], "B")
        self.assertEqual(result["_snapshot_seq"], 3)


if __name__ == "__main__":
    unittest.main()
