from __future__ import annotations

import copy
import unittest

import pandas as pd

from _engine.tower_policy_projection import project_tower_policy_deltas
from slurry_policy_config import PLANT_CONFIG


class TowerPolicyProjectionTest(unittest.TestCase):
    def test_alternating_branch_valves_become_same_tower_equivalent_action(self):
        plant = copy.deepcopy(PLANT_CONFIG)
        raw = pd.DataFrame(
            [
                {
                    "action_family": "TOWER:xst|SUPPLY",
                    "delta_valve__xst_v1": 2.0,
                    "delta_valve__xst_v2": 0.0,
                    "delta_valve__apt_v1": 0.0,
                    "normalized_delta_valve__xst_v1": 0.02,
                    "normalized_delta_valve__xst_v2": 0.00,
                    "normalized_delta_valve__apt_v1": 0.00,
                },
                {
                    "action_family": "TOWER:xst|SUPPLY",
                    "delta_valve__xst_v1": 0.0,
                    "delta_valve__xst_v2": 2.0,
                    "delta_valve__apt_v1": 0.0,
                    "normalized_delta_valve__xst_v1": 0.00,
                    "normalized_delta_valve__xst_v2": 0.02,
                    "normalized_delta_valve__apt_v1": 0.00,
                },
            ]
        )

        projected = project_tower_policy_deltas(raw, plant)

        # 每条历史事件的塔级等效动作都是 (2% + 0%)/2 = 1%，因此用于
        # 聚合的两个一级塔阀门都投影为 +1 个百分点。原始审计数据不被修改。
        self.assertEqual(raw.loc[0, "delta_valve__xst_v1"], 2.0)
        self.assertEqual(raw.loc[0, "delta_valve__xst_v2"], 0.0)
        self.assertAlmostEqual(projected.loc[0, "delta_valve__xst_v1"], 1.0)
        self.assertAlmostEqual(projected.loc[0, "delta_valve__xst_v2"], 1.0)
        self.assertAlmostEqual(projected.loc[1, "delta_valve__xst_v1"], 1.0)
        self.assertAlmostEqual(projected.loc[1, "delta_valve__xst_v2"], 1.0)
        self.assertEqual(projected.loc[0, "delta_valve__apt_v1"], 0.0)


if __name__ == "__main__":
    unittest.main()
