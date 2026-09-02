from __future__ import annotations

import unittest

import pandas as pd

from system.model.map_control.mfac_model.historical_condition_action_timing_diagnostic import (
    build_timing_summary,
    diagnose_condition_action_timing,
    select_target_events,
)


class Scheme2ConditionActionTimingDiagnosticTests(unittest.TestCase):
    def _history(self) -> pd.DataFrame:
        times = pd.date_range("2026-06-01 09:45:00", periods=151, freq="1min")
        values = [1000.0 + float(index) for index in range(len(times))]
        return pd.DataFrame({"date": times, "yyq_SO2": values})

    def _replay(self) -> pd.DataFrame:
        times = pd.date_range("2026-06-01 09:45:00", periods=151, freq="1min")
        labels = []
        switches = []
        current = "1"
        switch_times = {
            pd.Timestamp("2026-06-01 09:55:00"): "2",
            pd.Timestamp("2026-06-01 10:01:00"): "3",
            pd.Timestamp("2026-06-01 10:07:00"): "4",
            pd.Timestamp("2026-06-01 11:04:00"): "5",
        }
        for timestamp in times:
            if timestamp in switch_times:
                current = switch_times[timestamp]
                state = "SWITCHED"
            else:
                state = "STABLE"
            labels.append(current)
            switches.append(state)
        return pd.DataFrame(
            {
                "date": times,
                "replay_stable_condition_label": labels,
                "replay_condition_switch_state": switches,
            }
        )

    def _events(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "episode_id": "E1",
                    "flow_shape": "STEP",
                    "action_start_time": "2026-06-01 10:00:00",
                    "action_end_time": "2026-06-01 10:05:00",
                    "learnable_flow_shape": True,
                    "original_process_state_changed": True,
                    "majority_condition_changed": True,
                },
                {
                    "episode_id": "E2",
                    "flow_shape": "PULSE",
                    "action_start_time": "2026-06-01 11:00:00",
                    "action_end_time": "2026-06-01 11:05:00",
                    "learnable_flow_shape": True,
                    "original_process_state_changed": True,
                    "majority_condition_changed": True,
                },
                {
                    "episode_id": "FILTERED",
                    "flow_shape": "STEP",
                    "action_start_time": "2026-06-01 11:20:00",
                    "action_end_time": "2026-06-01 11:25:00",
                    "learnable_flow_shape": True,
                    "original_process_state_changed": True,
                    "majority_condition_changed": False,
                },
            ]
        )

    def test_selects_only_learnable_formal_transition_cohort(self):
        target = select_target_events(self._events())
        self.assertEqual(target["episode_id"].tolist(), ["E1", "E2"])

    def test_profiles_pre_action_post_switch_timing_without_changing_eligibility(self):
        detail = diagnose_condition_action_timing(
            self._history(),
            self._replay(),
            self._events(),
            pre_minutes=10,
            post_minutes=10,
        )
        self.assertEqual(detail["episode_id"].tolist(), ["E1", "E2"])

        first = detail.loc[detail["episode_id"] == "E1"].iloc[0]
        self.assertEqual(first["switch_pattern"], "PRE+ACTION+POST")
        self.assertEqual(int(first["pre_formal_switch_count"]), 1)
        self.assertEqual(int(first["action_formal_switch_count"]), 1)
        self.assertEqual(int(first["post_formal_switch_count"]), 1)
        self.assertAlmostEqual(float(first["last_pre_switch_offset_min_from_action_start"]), -5.0)
        self.assertAlmostEqual(float(first["first_action_switch_offset_min"]), 1.0)
        self.assertEqual(first["first_action_switch_bucket"], "0_1_MIN")

        second = detail.loc[detail["episode_id"] == "E2"].iloc[0]
        self.assertEqual(second["switch_pattern"], "ACTION")
        self.assertAlmostEqual(float(second["first_action_switch_offset_min"]), 4.0)
        self.assertEqual(second["first_action_switch_bucket"], "3_5_MIN")

        summary = build_timing_summary(detail)
        self.assertEqual(summary["target_event_count"], 2)
        self.assertEqual(summary["switch_pattern_counts"]["PRE+ACTION+POST"], 1)
        self.assertEqual(summary["switch_pattern_counts"]["ACTION"], 1)
        self.assertEqual(summary["first_action_switch_bucket_counts"]["0_1_MIN"], 1)
        self.assertEqual(summary["first_action_switch_bucket_counts"]["3_5_MIN"], 1)


if __name__ == "__main__":
    unittest.main()
