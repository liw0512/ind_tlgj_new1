import unittest

import pandas as pd

from system.model.map_control.slurry_policy_model.adaptive_predictive.feature_builder import (
    CausalFeatureConfig,
    build_causal_one_step_frame,
    causal_tower_total_flow,
)


TOWER = {
    "tower_id": "xst",
    "ph_column": "xstjy_PH",
    "supply_flows": [{"flow_id": "main", "column": "xstshsjy_LL"}],
}


class CausalPredictiveFeatureTest(unittest.TestCase):
    def test_future_flow_changes_do_not_change_past_filtered_flow(self):
        base = pd.DataFrame({"xstshsjy_LL": [10, 10, 10, 10, 10, 10]})
        changed_future = base.copy()
        changed_future.loc[4:, "xstshsjy_LL"] = [100, 100]

        a = causal_tower_total_flow(base, TOWER, filter_points=3)
        b = causal_tower_total_flow(changed_future, TOWER, filter_points=3)

        self.assertEqual(a.iloc[:4].tolist(), b.iloc[:4].tolist())

    def test_lag_builder_does_not_cross_continuous_segment_boundary(self):
        frame = pd.DataFrame(
            {
                "continuous_segment_id": [1] * 6 + [2] * 6,
                "jyq_SO2": [10, 11, 12, 13, 14, 15, 30, 31, 32, 33, 34, 35],
                "xstjy_PH": [6.2] * 12,
                "xstshsjy_LL": [20, 21, 22, 23, 24, 25, 40, 41, 42, 43, 44, 45],
                "yyq_SO2": [1000, 1010, 1020, 1030, 1040, 1050, 2000, 2010, 2020, 2030, 2040, 2050],
            }
        )
        cfg = CausalFeatureConfig(
            output_delta_lags=2,
            flow_delta_lags=2,
            disturbance_delta_lags=2,
            context_delta_lags=2,
            causal_flow_filter_points=1,
        )
        result, features, target = build_causal_one_step_frame(
            frame,
            output_column="jyq_SO2",
            tower=TOWER,
            disturbance_columns=("yyq_SO2",),
            context_columns=("xstjy_PH",),
            config=cfg,
        )

        # Each six-row segment loses its first two rows to causal deltas/lags and
        # its final row to the t+1 target. No sample is allowed to bridge 15->30.
        self.assertEqual(len(result), 6)
        self.assertIn("flow_delta_lag_1", features)
        self.assertIn("disturbance__yyq_SO2__delta_lag_1", features)
        self.assertEqual(target, "target_delta_output_next")
        self.assertNotIn(5, set(result["source_index"].tolist()))
        self.assertNotIn(6, set(result["source_index"].tolist()))

    def test_missing_physical_flow_meter_is_rejected(self):
        frame = pd.DataFrame({"other": [1, 2, 3]})
        with self.assertRaises(KeyError):
            causal_tower_total_flow(frame, TOWER)


if __name__ == "__main__":
    unittest.main()
