import unittest

import pandas as pd

from system.model.config.standard_fields import TIME_COLUMN
from system.model.map_control.condition_model.condition_config import default_config
from system.model.map_control.condition_model.initial_condition_builder import (
    InitialConditionBuilder,
)
from system.model.map_control.mfac_model.historical_condition_stability_replay_diagnostic import (
    diagnose_episode_condition_transitions,
    replay_canonical_condition_history,
)


class Scheme2HistoricalConditionStabilityReplayTest(unittest.TestCase):
    @staticmethod
    def _snapshot_and_levels():
        config = default_config()
        snapshot = InitialConditionBuilder(config).build([], "v001")
        axis = config.axis_1
        first = axis.minimum + 0.5 * axis.step
        second = axis.minimum + 1.5 * axis.step
        return snapshot, first, second

    @staticmethod
    def _history(values):
        return pd.DataFrame(
            {
                TIME_COLUMN: pd.date_range(
                    "2026-07-01 00:00:00",
                    periods=len(values),
                    freq="10s",
                ),
                "yyq_SO2": values,
            }
        )

    def test_majority_replay_filters_boundary_chatter(self):
        snapshot, first, second = self._snapshot_and_levels()
        values = [first] * 6 + [second, first, second, first, second, first]
        history = self._history(values)

        replay = replay_canonical_condition_history(history, snapshot)
        event = pd.DataFrame(
            [
                {
                    "episode_id": "E_CHATTER",
                    "action_start_time": history[TIME_COLUMN].iloc[6],
                    "action_end_time": history[TIME_COLUMN].iloc[-1],
                    "flow_shape": "STEP",
                    "valid": False,
                    "flow_context_reason": (
                        "PROCESS_STATE_CHANGED_DURING_EVENT"
                    ),
                }
            ]
        )
        detail = diagnose_episode_condition_transitions(replay, event).iloc[0]

        self.assertTrue(bool(detail["raw_condition_changed"]))
        self.assertFalse(bool(detail["majority_condition_changed"]))
        self.assertTrue(bool(detail["majority_filters_raw_transition"]))
        self.assertTrue(
            bool(
                detail[
                    "learnable_shape_process_state_rejection_but_majority_stable"
                ]
            )
        )
        self.assertEqual(int(detail["formal_online_switched_count"]), 0)

    def test_majority_replay_keeps_sustained_condition_transition(self):
        snapshot, first, second = self._snapshot_and_levels()
        values = [first] * 6 + [second] * 6
        history = self._history(values)

        replay = replay_canonical_condition_history(history, snapshot)
        event = pd.DataFrame(
            [
                {
                    "episode_id": "E_SUSTAINED",
                    "action_start_time": history[TIME_COLUMN].iloc[5],
                    "action_end_time": history[TIME_COLUMN].iloc[-1],
                    "flow_shape": "STEP",
                    "valid": False,
                    "flow_context_reason": (
                        "PROCESS_STATE_CHANGED_DURING_EVENT"
                    ),
                }
            ]
        )
        detail = diagnose_episode_condition_transitions(replay, event).iloc[0]

        self.assertTrue(bool(detail["raw_condition_changed"]))
        self.assertTrue(bool(detail["majority_condition_changed"]))
        self.assertFalse(bool(detail["majority_filters_raw_transition"]))
        self.assertGreaterEqual(int(detail["formal_online_switched_count"]), 1)


if __name__ == "__main__":
    unittest.main()
