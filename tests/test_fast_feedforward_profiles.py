import unittest

import pandas as pd

from system.model.map_control.slurry_policy_model._engine.supply_flow_prototype import (
    build_supply_flow_prototypes,
)
from system.model.map_control.slurry_policy_model.slurry_policy_online.supply_flow_advisor import (
    SupplyFlowAdvisor,
)


TRAINING = {
    "validity": {
        "require_condition_valid": True,
        "allow_out_of_range_clipped": True,
    },
    "reliability": {
        "reference_event_count": 5,
        "reference_segment_count": 3,
        "reference_day_count": 3,
        "minimum_supported_events": 2,
        "minimum_supported_segments": 1,
        "minimum_supported_days": 1,
        "weights": {
            "support": 0.20,
            "direction_consistency": 0.20,
            "response_stability": 0.20,
            "safety_history": 0.30,
            "time_coverage": 0.10,
        },
    },
}

PLANT = {
    "towers": [
        {
            "tower_id": "xst",
            "enabled": True,
            "ph_column": "xstjy_PH",
            "ph_safe_range": [5.6, 6.8],
            "supply_flows": [
                {"flow_id": "main", "column": "xstshsjy_LL"},
            ],
        }
    ]
}

ONLINE = {
    "profile_acceptance": {
        "local_allowed_status": ["SUPPORTED", "LOW_SUPPORT"],
        "minimum_direction_consistency": 0.0,
        "minimum_safety_history_score": 0.0,
        "minimum_reliability_total_score": 0.0,
        "minimum_stable_response_ratio": 0.0,
    }
}


def episode(
    index,
    *,
    fast=False,
    rate=0.0,
    condition="C1",
    final_delta=10.0,
    safe_ratio=1.0,
    ph_out=False,
    hard_max=False,
):
    return {
        "episode_id": f"E{index}",
        "episode_type": "FLOW_ACTION",
        "flow_context": "CLEAN",
        "flow_learning_eligible": True,
        "flow_effect_complete": True,
        "flow_shape": "STEP",
        "condition_label": condition,
        "flow_event_tower_id": "xst",
        "condition_valid": True,
        "out_of_range_clipped": False,
        "action_direction": "INCREASE",
        "flow_execution_profile": "STEP",
        "source_files": "history.csv",
        "continuous_segment_id": index,
        "event_date": f"2026-07-{(index % 20) + 1:02d}",
        "flow_effect_outlet_so2_direction": "DECREASE",
        "flow_effect_tower_ph_direction": "INCREASE",
        "flow_timing_settled": True,
        "ph_out_of_range__xst": ph_out,
        "outlet_so2_over_hard_max": hard_max,
        "flow_event_final_delta_flow": final_delta,
        "flow_event_peak_delta_flow": final_delta + 2.0,
        "flow_event_max_abs_delta_flow": final_delta + 2.0,
        "flow_event_active_duration_minutes": 2.0,
        "flow_event_signed_slurry_volume": 20.0,
        "flow_temporary_plateau": False,
        "delta_outlet_so2": -2.0,
        "delta_ph__xst": 0.05,
        "flow_timing_observed_response_delay_minutes": 1.0,
        "flow_timing_time_to_extreme_minutes": 3.0,
        "flow_timing_time_to_stable_minutes": 5.0,
        "post_outlet_so2_safe_ratio": safe_ratio,
        "fast_change_mode": "FAST_CHANGE" if fast else "REGULAR",
        "fast_change_direction": "RISE" if fast else "NONE",
        "fast_change_primary_axis_rate": rate if fast else 0.0,
        "fast_change_exact_trend_mode": "AXIS1_RISE_FAST" if fast else "STEADY",
    }


class Loader:
    def __init__(self, profiles):
        self.profiles = profiles

    def load_supply_flow_prototypes(self):
        return self.profiles


class FastFeedforwardProfileTest(unittest.TestCase):
    def test_safe_regular_actions_create_plant_baseline_without_fast_history(self):
        frame = pd.DataFrame(
            [episode(i, final_delta=value) for i, value in enumerate([6, 8, 10, 12, 14], 1)]
        )
        profiles = build_supply_flow_prototypes(frame, TRAINING)
        baselines = [
            value
            for value in profiles.values()
            if value.get("profile_kind") == "FAST_PLANT_SAFE_BASELINE"
        ]
        self.assertEqual(len(baselines), 1)
        self.assertEqual(baselines[0]["event_count"], 5)
        # P25 of [6,8,10,12,14] = 8, deliberately below the median 10.
        self.assertAlmostEqual(baselines[0]["recommended_delta_flow"], 8.0)

    def test_fast_history_creates_exact_and_severity_pool(self):
        frame = pd.DataFrame(
            [
                episode(1, fast=True, rate=170, final_delta=8),
                episode(2, fast=True, rate=175, final_delta=10),
                episode(3, fast=True, rate=180, final_delta=12),
            ]
        )
        profiles = build_supply_flow_prototypes(frame, TRAINING)
        kinds = {value.get("profile_kind") for value in profiles.values()}
        self.assertIn("FAST_EXACT", kinds)
        self.assertIn("FAST_DIRECTION_SEVERITY_POOL", kinds)
        exact = next(
            value for value in profiles.values()
            if value.get("profile_kind") == "FAST_EXACT"
        )
        self.assertEqual(exact["fast_level"], "L2")
        self.assertAlmostEqual(exact["recommended_delta_flow"], 10.0)

    def test_unsafe_actions_do_not_teach_fast_baseline(self):
        rows = [
            episode(i, final_delta=10, safe_ratio=0.5)
            for i in range(1, 6)
        ]
        rows.append(episode(6, final_delta=10, ph_out=True))
        rows.append(episode(7, final_delta=10, hard_max=True))
        profiles = build_supply_flow_prototypes(pd.DataFrame(rows), TRAINING)
        self.assertFalse(
            any(
                value.get("profile_kind") == "FAST_PLANT_SAFE_BASELINE"
                for value in profiles.values()
            )
        )

    def test_advisor_falls_back_from_missing_exact_to_pool_then_baseline(self):
        pool = {
            "prototype_id": "POOL",
            "profile_kind": "FAST_DIRECTION_SEVERITY_POOL",
            "tower_id": "xst",
            "action_direction": "INCREASE",
            "fast_level": "L2",
            "fast_rate_thresholds": {"L1": 120.0, "L2": 160.0, "L3": 220.0},
            "recommended_delta_flow": 9.0,
            "event_count": 4,
            "target_flow": {"final_delta_flow": {"p25": 9.0, "median": 11.0, "p75": 13.0}},
            "safety": {"observed_safe_ratio_median": 1.0},
            "evidence": {"status": "FAST_SUPPORTED"},
            "effect": {},
            "timing": {},
        }
        baseline = {
            **pool,
            "prototype_id": "BASE",
            "profile_kind": "FAST_PLANT_SAFE_BASELINE",
            "fast_level": "*",
            "recommended_delta_flow": 6.0,
            "event_count": 10,
        }
        advisor = SupplyFlowAdvisor(Loader({"p": pool, "b": baseline}), PLANT, ONLINE)
        process = {
            "fast_change_mode": "FAST_CHANGE",
            "fast_change_direction": "RISE",
            "fast_change_exact_trend_mode": "AXIS1_RISE_FAST",
            "fast_change_axis_columns": ["yyq_SO2"],
            "fast_change_axis_rates": {"yyq_SO2": 175.0},
            "xstjy_PH": 6.1,
            "xstshsjy_LL": 50.0,
        }
        result = advisor.recommend("C1", ["DECREASE"], process)
        self.assertTrue(result["available"])
        self.assertEqual(result["profile_kind"], "FAST_DIRECTION_SEVERITY_POOL")
        self.assertEqual(result["target_final_flow"], 59.0)

        advisor = SupplyFlowAdvisor(Loader({"b": baseline}), PLANT, ONLINE)
        result = advisor.recommend("C1", ["DECREASE"], process)
        self.assertTrue(result["available"])
        self.assertEqual(result["profile_kind"], "FAST_PLANT_SAFE_BASELINE")
        self.assertEqual(result["target_final_flow"], 56.0)

    def test_fast_drop_does_not_fall_back_to_regular_economic_policy(self):
        regular = {
            "prototype_id": "REG",
            "profile_kind": "REGULAR_SUPPLY_FLOW",
            "condition_label": "C1",
            "tower_id": "xst",
            "action_direction": "DECREASE",
            "flow_shape": "STEP",
            "flow_execution_profile": "STEP",
            "event_count": 10,
            "target_flow": {
                "final_delta_flow": {"p25": -8.0, "median": -10.0, "p75": -12.0},
                "peak_delta_flow": {"p25": -8.0, "median": -10.0, "p75": -12.0},
            },
            "effect": {"outlet_so2_direction": "INCREASE", "outlet_so2_direction_consistency": 1.0},
            "timing": {"settled_ratio": 1.0},
            "evidence": {
                "status": "SUPPORTED",
                "reliability": {"safety_history_score": 100.0, "total_score": 100.0},
            },
        }
        advisor = SupplyFlowAdvisor(Loader({"r": regular}), PLANT, ONLINE)
        result = advisor.recommend(
            "C1",
            ["INCREASE"],
            {
                "fast_change_mode": "FAST_CHANGE",
                "fast_change_direction": "DROP",
                "xstjy_PH": 6.1,
                "xstshsjy_LL": 50.0,
            },
        )
        self.assertFalse(result["available"])
        self.assertIn("FAST_DROP_CONSERVATIVE_HOLD", result["reason_codes"])

    def test_regular_mode_ignores_fast_special_profiles(self):
        special = {
            "prototype_id": "FAST",
            "profile_kind": "FAST_PLANT_SAFE_BASELINE",
            "condition_label": "C1",
            "tower_id": "xst",
            "action_direction": "INCREASE",
            "recommended_delta_flow": 8.0,
            "event_count": 20,
        }
        advisor = SupplyFlowAdvisor(Loader({"f": special}), PLANT, ONLINE)
        result = advisor.recommend(
            "C1",
            ["DECREASE"],
            {
                "fast_change_mode": "REGULAR",
                "xstjy_PH": 6.1,
                "xstshsjy_LL": 50.0,
            },
        )
        self.assertFalse(result["available"])
        self.assertIn("NO_ACCEPTED_FLOW_PROTOTYPE", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
