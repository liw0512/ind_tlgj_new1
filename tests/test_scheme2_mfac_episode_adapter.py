import unittest

import pandas as pd

from system.model.map_control.mfac_model.context_resolver import (
    MFACContextResolver,
)
from system.model.map_control.mfac_model.episode_adapter import (
    Scheme1EpisodeToMFACAdapter,
)
from system.model.map_control.mfac_model.mfac_eligibility import (
    MFACEligibilityConfig,
    StrictMFACEligibilityGate,
)


class Scheme1EpisodeToMFACAdapterTest(unittest.TestCase):
    def setUp(self):
        self.resolver = MFACContextResolver("v001")
        self.gate = StrictMFACEligibilityGate(
            MFACEligibilityConfig(max_abs_qbase_drift=0.5)
        )
        self.adapter = Scheme1EpisodeToMFACAdapter(
            self.resolver,
            self.gate,
            inlet_so2_column="yyq_SO2",
            qbase_column="scheme2_qbase",
        )

    @staticmethod
    def episode():
        return {
            "episode_id": "FLOW_EP_1",
            "condition_snapshot_version": "v001",
            "condition_label": "17",
            "base_condition_id": "17",
            "grid_id": "P1-S17",
            "policy_region_id": "R_0017",
            "action_start_time": pd.Timestamp("2026-08-01 10:00:00"),
            "action_end_time": pd.Timestamp("2026-08-01 10:01:00"),
            "flow_effect_baseline_start_time": pd.Timestamp("2026-08-01 09:55:00"),
            "flow_effect_response_start_time": pd.Timestamp("2026-08-01 10:04:00"),
            "response_end_time": pd.Timestamp("2026-08-01 10:08:00"),
            "active_tower_ids": "xst",
            "flow_shape": "STEP",
            "flow_direction": "INCREASE",
            "flow_disturbance_class": "STEADY",
            "flow_disturbance_state": "STEADY",
            "valid": True,
            "flow_effect_complete": True,
            "flow_learning_eligible": True,
            "followup_action_in_response": False,
            "flow_circulation_change": False,
            "flow_major_process_transition": False,
            "condition_valid": True,
            "flow_event_baseline_flow": 30.0,
            "flow_event_final_flow": 32.0,
            "flow_event_final_delta_flow": 2.0,
            "before_outlet_so2": 48.0,
            "after_outlet_so2": 38.0,
            "delta_outlet_so2": -10.0,
            "before_ph__xst": 5.4,
            "after_ph__xst": 5.5,
            "delta_ph__xst": 0.1,
            "flow_timing_observed_response_delay_minutes": 2.0,
            "flow_timing_time_to_stable_minutes": 6.0,
        }

    @staticmethod
    def history(base_condition_ids=None, targets=None, qbase=None):
        times = pd.date_range(
            "2026-08-01 09:55:00",
            "2026-08-01 10:08:00",
            freq="10s",
        )
        count = len(times)
        return pd.DataFrame(
            {
                "date": times,
                "condition_snapshot_version": ["v001"] * count,
                "condition_label": ["17"] * count,
                "base_condition_id": base_condition_ids or ["17"] * count,
                "grid_id": ["P1-S17"] * count,
                "policy_region_id": ["R_0017"] * count,
                "outlet_so2_target": targets or [35.0] * count,
                "yyq_SO2": [1500.0] * count,
                "scheme2_qbase": qbase or [31.0] * count,
                "fast_change_mode": ["REGULAR"] * count,
            }
        )

    def test_clean_episode_becomes_eligible_mfac_event(self):
        result = self.adapter.adapt(self.episode(), history=self.history())

        self.assertTrue(result.learning_eligible)
        self.assertEqual(result.mfac_context_id, "MFAC-COND-17")
        self.assertEqual(result.phi_event, -5.0)
        self.assertEqual(result.delta_q_actual, 2.0)
        self.assertEqual(result.qbase_drift, 0.0)
        self.assertFalse(result.target_changed)
        self.assertFalse(result.condition_changed)

    def test_target_change_rejects_learning(self):
        history = self.history()
        history.loc[history["date"] >= pd.Timestamp("2026-08-01 10:03:00"), "outlet_so2_target"] = 30.0
        result = self.adapter.adapt(self.episode(), history=history)

        self.assertFalse(result.learning_eligible)
        self.assertTrue(result.target_changed)
        self.assertIn("SO2_TARGET_CHANGED", result.reject_reason)

    def test_base_override_detects_context_change_inside_merged_condition(self):
        resolver = MFACContextResolver(
            "v001",
            base_condition_overrides={
                "17": "MFAC-BASE-17",
                "18": "MFAC-BASE-18",
            },
        )
        adapter = Scheme1EpisodeToMFACAdapter(
            resolver,
            self.gate,
            inlet_so2_column="yyq_SO2",
            qbase_column="scheme2_qbase",
        )
        history = self.history()
        history.loc[history["date"] >= pd.Timestamp("2026-08-01 10:05:00"), "base_condition_id"] = "18"
        result = adapter.adapt(self.episode(), history=history)

        self.assertFalse(result.learning_eligible)
        self.assertTrue(result.condition_changed)
        self.assertIn("MFAC_CONTEXT_CHANGED", result.reject_reason)

    def test_missing_qbase_does_not_silently_approve(self):
        adapter = Scheme1EpisodeToMFACAdapter(
            self.resolver,
            self.gate,
            inlet_so2_column="yyq_SO2",
            qbase_column="missing_qbase",
        )
        result = adapter.adapt(self.episode(), history=self.history())

        self.assertFalse(result.learning_eligible)
        self.assertIn("MISSING_QBASE_STABILITY_EVIDENCE", result.reject_reason)


if __name__ == "__main__":
    unittest.main()
