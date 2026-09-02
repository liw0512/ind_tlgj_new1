from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from system.model.map_control.mfac_model.historical_disturbance_slurry_coupling_diagnostic import (
    build_coupling_summary,
    diagnose_disturbance_slurry_coupling,
)


class Scheme2DisturbanceSlurryCouplingDiagnosticTests(unittest.TestCase):
    def _timing_detail(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "episode_id": "E1",
                    "flow_shape": "STEP",
                    "start_local_median_shift": 120.0,
                    "action_axis_delta": 200.0,
                    "action_axis_range": 300.0,
                    "last_pre_switch_offset_min_from_action_start": -0.5,
                    "first_action_switch_offset_min": 1.0,
                    "switch_pattern": "PRE+ACTION+POST",
                },
                {
                    "episode_id": "E2",
                    "flow_shape": "PULSE",
                    "start_local_median_shift": -80.0,
                    "action_axis_delta": -150.0,
                    "action_axis_range": 280.0,
                    "last_pre_switch_offset_min_from_action_start": -2.5,
                    "first_action_switch_offset_min": 2.0,
                    "switch_pattern": "PRE+ACTION+POST",
                },
                {
                    "episode_id": "E3",
                    "flow_shape": "BOOST_STEP",
                    "start_local_median_shift": 20.0,
                    "action_axis_delta": 90.0,
                    "action_axis_range": 180.0,
                    "last_pre_switch_offset_min_from_action_start": -7.0,
                    "first_action_switch_offset_min": 4.0,
                    "switch_pattern": "PRE+ACTION",
                },
                {
                    "episode_id": "E4",
                    "flow_shape": "STEP",
                    "start_local_median_shift": -120.0,
                    "action_axis_delta": -210.0,
                    "action_axis_range": 320.0,
                    "last_pre_switch_offset_min_from_action_start": np.nan,
                    "first_action_switch_offset_min": 0.5,
                    "switch_pattern": "ACTION+POST",
                },
            ]
        )

    def _episodes(self) -> pd.DataFrame:
        reason = "FLOW_CONTEXT_NOT_CLEAN:PROCESS_STATE_CHANGED_DURING_EVENT"
        context = "PROCESS_STATE_CHANGED_DURING_EVENT"
        return pd.DataFrame(
            [
                {
                    "episode_id": "E1",
                    "action_semantics": "ACTUAL_SUPPLY_FLOW_V1",
                    "flow_direction": "INCREASE",
                    "flow_event_baseline_flow": 40.0,
                    "flow_event_peak_delta_flow": 20.0,
                    "flow_event_max_abs_delta_flow": 20.0,
                    "flow_event_final_delta_flow": 18.0,
                    "flow_effect_complete": True,
                    "flow_context_reason": context,
                    "invalid_reason": reason,
                },
                {
                    "episode_id": "E2",
                    "action_semantics": "ACTUAL_SUPPLY_FLOW_V1",
                    "flow_direction": "DECREASE",
                    "flow_event_baseline_flow": 50.0,
                    "flow_event_peak_delta_flow": -15.0,
                    "flow_event_max_abs_delta_flow": 15.0,
                    # A PULSE can return to baseline; endpoint delta must not be
                    # used as the action-direction definition.
                    "flow_event_final_delta_flow": 0.0,
                    "flow_effect_complete": True,
                    "flow_context_reason": context,
                    "invalid_reason": reason,
                },
                {
                    "episode_id": "E3",
                    "action_semantics": "ACTUAL_SUPPLY_FLOW_V1",
                    "flow_direction": "INCREASE",
                    "flow_event_baseline_flow": 30.0,
                    "flow_event_peak_delta_flow": 10.0,
                    "flow_event_max_abs_delta_flow": 10.0,
                    "flow_event_final_delta_flow": 7.0,
                    "flow_effect_complete": True,
                    "flow_context_reason": context,
                    "invalid_reason": reason,
                },
                {
                    "episode_id": "E4",
                    "action_semantics": "ACTUAL_SUPPLY_FLOW_V1",
                    "flow_direction": "INCREASE",
                    "flow_event_baseline_flow": 45.0,
                    "flow_event_peak_delta_flow": 12.0,
                    "flow_event_max_abs_delta_flow": 12.0,
                    "flow_event_final_delta_flow": 11.0,
                    "flow_effect_complete": False,
                    "flow_context_reason": context,
                    "invalid_reason": reason,
                },
            ]
        )

    def test_uses_actual_flow_event_direction_and_deadbanded_so2_coupling(self):
        detail = diagnose_disturbance_slurry_coupling(
            self._episodes(),
            self._timing_detail(),
            so2_deadbands=(0.0, 50.0, 100.0),
        )
        self.assertEqual(detail["episode_id"].tolist(), ["E1", "E2", "E3", "E4"])

        pulse = detail.loc[detail["episode_id"] == "E2"].iloc[0]
        self.assertAlmostEqual(float(pulse["flow_event_final_delta_flow"]), 0.0)
        self.assertAlmostEqual(float(pulse["flow_signed_amplitude_actual"]), -15.0)
        self.assertEqual(pulse["coupling_db_0"], "SAME_DIRECTION")
        self.assertEqual(pulse["coupling_db_50"], "SAME_DIRECTION")
        self.assertEqual(pulse["coupling_db_100"], "SO2_NEUTRAL")

        weak_so2 = detail.loc[detail["episode_id"] == "E3"].iloc[0]
        self.assertEqual(weak_so2["coupling_db_0"], "SAME_DIRECTION")
        self.assertEqual(weak_so2["coupling_db_50"], "SO2_NEUTRAL")

        opposite = detail.loc[detail["episode_id"] == "E4"].iloc[0]
        self.assertEqual(opposite["coupling_db_100"], "OPPOSITE_DIRECTION")

    def test_summary_reports_direction_ratios_correlation_timing_and_amplitude(self):
        detail = diagnose_disturbance_slurry_coupling(
            self._episodes(),
            self._timing_detail(),
        )
        summary = build_coupling_summary(detail)

        self.assertEqual(summary["target_event_count"], 4)
        self.assertEqual(summary["flow_shape_counts"]["STEP"], 2)
        self.assertEqual(summary["flow_direction_counts"]["INCREASE"], 3)
        self.assertEqual(summary["flow_direction_counts"]["DECREASE"], 1)
        self.assertEqual(summary["actual_flow_semantics_ok_count"], 4)
        self.assertEqual(summary["only_condition_context_invalid_count"], 4)
        self.assertEqual(
            summary["only_condition_context_invalid_and_effect_complete_count"], 3
        )

        db50 = summary["direction_coupling"]["50"]
        self.assertEqual(db50["same_direction_count"], 2)
        self.assertEqual(db50["opposite_direction_count"], 1)
        self.assertEqual(db50["so2_neutral_count"], 1)
        self.assertEqual(db50["directional_pair_count"], 3)
        self.assertAlmostEqual(db50["same_direction_ratio_directional"], 2.0 / 3.0)
        self.assertAlmostEqual(db50["opposite_direction_ratio_directional"], 1.0 / 3.0)

        db100 = summary["direction_coupling"]["100"]
        self.assertEqual(db100["same_direction_count"], 1)
        self.assertEqual(db100["opposite_direction_count"], 1)
        self.assertEqual(db100["so2_neutral_count"], 2)

        self.assertEqual(summary["signed_correlation"]["pair_count"], 4)
        self.assertIsNotNone(summary["signed_correlation"]["pearson"])
        self.assertIsNotNone(summary["signed_correlation"]["spearman"])

        timing = summary["pre_switch_timing"]
        self.assertEqual(timing["available_count"], 3)
        self.assertEqual(timing["within_1_min_count"], 1)
        self.assertEqual(timing["within_3_min_count"], 2)
        self.assertEqual(timing["within_5_min_count"], 2)
        self.assertEqual(timing["within_10_min_count"], 3)
        self.assertAlmostEqual(summary["flow_abs_peak_delta_quantiles"]["max"], 20.0)

    def test_rejects_timing_episode_missing_from_original_episode_table(self):
        episodes = self._episodes().iloc[:-1].copy()
        with self.assertRaises(KeyError):
            diagnose_disturbance_slurry_coupling(episodes, self._timing_detail())


if __name__ == "__main__":
    unittest.main()
