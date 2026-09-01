import unittest

from system.model.config.standard_fields import LIQUID_GAS_RATIO_COLUMN
from system.model.map_control.condition_model.auto_merge_manager import AutoMergeManager
from system.model.map_control.condition_model.condition_config import default_config
from system.model.map_control.condition_model.condition_schema import GridCell


class Scheme2ConditionAutoMergeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.manager = AutoMergeManager(default_config())

    def test_effective_evidence_count_uses_standard_liquid_gas_field(self):
        cell = GridCell(
            grid_id="P1-S1",
            axis_1_level=1,
            axis_2_level=1,
            axis_1_range=(0.0, 1.0),
            axis_2_range=(0.0, 1.0),
            sample_count=500,
        )
        cell.accumulators = {
            "numeric": {
                LIQUID_GAS_RATIO_COLUMN: {
                    "count": 450,
                    "sum": 450.0,
                    "sum_square": 450.0,
                    "minimum": 1.0,
                    "maximum": 1.0,
                }
            },
            "risk": {"valid_count": 430, "risk_count": 0},
        }

        self.assertEqual(self.manager._effective_evidence_count(cell), 430)

    def test_verification_can_advance_to_required_third_pass(self):
        grid_ids = ["P1-S1", "P2-S1"]

        passes, progress, _, counted = self.manager._advance_verification(
            grid_ids,
            {"P1-S1": 300, "P2-S1": 300},
            {},
        )
        self.assertEqual(passes, 1)
        self.assertEqual(progress, "INITIAL_PASS")

        previous = {
            "verification_passes": passes,
            "counted_member_evidence_counts": counted,
        }
        passes, progress, _, counted = self.manager._advance_verification(
            grid_ids,
            {"P1-S1": 310, "P2-S1": 310},
            previous,
        )
        self.assertEqual(passes, 2)
        self.assertEqual(progress, "COUNTED_NEW_EVIDENCE")

        previous = {
            "verification_passes": passes,
            "counted_member_evidence_counts": counted,
        }
        passes, progress, _, _ = self.manager._advance_verification(
            grid_ids,
            {"P1-S1": 320, "P2-S1": 320},
            previous,
        )
        self.assertEqual(passes, 3)
        self.assertEqual(progress, "COUNTED_NEW_EVIDENCE")
        self.assertGreaterEqual(
            passes,
            self.manager.config.merge.min_consecutive_pass_snapshots,
        )


if __name__ == "__main__":
    unittest.main()
