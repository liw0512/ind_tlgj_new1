import unittest

import numpy as np
import pandas as pd

from system.model.map_control.slurry_policy_model.adaptive_predictive.arx_identifier import (
    ArxIdentifierConfig,
    fit_arx_ridge_channel,
)
from system.model.map_control.slurry_policy_model.adaptive_predictive.feature_builder import (
    CausalFeatureConfig,
)


TOWER = {
    "tower_id": "xst",
    "ph_column": "xstjy_PH",
    "supply_flows": [{"flow_id": "main", "column": "xstshsjy_LL"}],
}


class ArxIdentifierTest(unittest.TestCase):
    def test_recovers_simple_causal_closed_loop_dynamics(self):
        rng = np.random.default_rng(7)
        n = 1600
        dq = rng.normal(0.0, 0.8, size=n)
        dd = rng.normal(0.0, 15.0, size=n)
        q = 50.0 + np.cumsum(dq)
        disturbance = 1800.0 + np.cumsum(dd)

        y = np.zeros(n, dtype=float)
        dy = np.zeros(n, dtype=float)
        y[0] = 15.0
        for t in range(n - 1):
            # Increasing slurry suppresses outlet SO2; increasing inlet SO2
            # pushes it upward. The output also has its own dynamic memory.
            dy[t + 1] = (
                0.55 * dy[t]
                - 0.08 * dq[t]
                + 0.003 * dd[t]
                + rng.normal(0.0, 0.01)
            )
            y[t + 1] = y[t] + dy[t + 1]

        frame = pd.DataFrame(
            {
                "continuous_segment_id": 1,
                "jyq_SO2": y,
                "xstshsjy_LL": q,
                "yyq_SO2": disturbance,
            }
        )
        result = fit_arx_ridge_channel(
            frame,
            output_column="jyq_SO2",
            tower=TOWER,
            disturbance_columns=("yyq_SO2",),
            feature_config=CausalFeatureConfig(
                output_delta_lags=2,
                flow_delta_lags=2,
                disturbance_delta_lags=2,
                context_delta_lags=2,
                causal_flow_filter_points=1,
            ),
            identifier_config=ArxIdentifierConfig(
                ridge_alpha=0.1,
                train_ratio=0.70,
                minimum_train_rows=500,
                minimum_validation_rows=200,
            ),
        )

        metrics = result.validation["validation"]
        self.assertGreater(metrics["r2"], 0.90)
        self.assertGreater(metrics["direction_accuracy"], 0.85)
        self.assertGreater(metrics["rmse_improvement_ratio"], 0.50)
        self.assertEqual(result.model_payload["target_semantics"], "DELTA_OUTPUT_T_PLUS_1")
        self.assertEqual(result.model_payload["disturbance_columns"], ["yyq_SO2"])


if __name__ == "__main__":
    unittest.main()
