from __future__ import annotations

import copy
import unittest

from _engine.state_builder import build_policy_state
from slurry_policy_config import ONLINE_POLICY_CONFIG, PLANT_CONFIG, TRAINING_CONFIG
from slurry_policy_online.candidate_filter import CandidateFilter
from slurry_policy_online.candidate_ranker import CandidateRanker
from slurry_policy_online.types import Candidate, ConditionContext, ControlDemand, RealtimeState
from slurry_policy_online.valve_action_resolver import ValveActionResolver


def _profile(
    family: str,
    direction: str,
    magnitude: str,
    so2_delta: float,
    tower_id: str,
    ph_delta_p25: float,
    ph_delta_median: float,
    ph_delta_p75: float,
    representative: dict[str, float],
) -> dict:
    return {
        "action_profile": {
            "action_family": family,
            "direction": direction,
            "magnitude": magnitude,
            "representative_delta": representative,
        },
        "so2_effect": {
            "dominant_direction": "DECREASE" if so2_delta < 0 else "INCREASE",
            "direction_consistency": 0.90,
            "delta_distribution": {
                "median": so2_delta,
                "p25": so2_delta - 0.5,
                "p75": so2_delta + 0.5,
            },
        },
        "ph_effect": {
            tower_id: {
                "delta_distribution": {
                    "p25": ph_delta_p25,
                    "median": ph_delta_median,
                    "p75": ph_delta_p75,
                }
            }
        },
        "stability": {"stable_response_ratio": 0.90},
        "safety": {
            "tower_ph": {tower_id: {"out_of_range_ratio": 0.02}},
            "any_safety_violation_ratio": 0.02,
        },
        "support": {"effective_weighted_event_count": 20.0},
        "spatial_support": {"direction_generalizable": True},
        "reliability": {"total_score": 85.0, "safety_history_score": 95.0},
        "profile_status": "SUPPORTED",
    }


class TowerLevelPolicyTest(unittest.TestCase):
    def setUp(self):
        self.plant = copy.deepcopy(PLANT_CONFIG)
        self.training = copy.deepcopy(TRAINING_CONFIG)
        self.online = copy.deepcopy(ONLINE_POLICY_CONFIG)
        self.condition = ConditionContext(
            condition_snapshot_version="v001",
            condition_label="365",
            raw_grid_id="P1-S1",
            condition_stable=True,
            condition_valid=True,
        )

    def _state(self, xst_ph: float = 5.0, apt_ph: float = 6.0) -> RealtimeState:
        return RealtimeState(
            timestamp="2026-01-01T00:00:00",
            condition=self.condition,
            process={
                "jzfh": 350.0,
                "yyq_SO2": 3000.0,
                "jyq_SO2": 25.0,
                "xstjy_PH": xst_ph,
                "aptjy_PH": apt_ph,
                "xst_FMKD1": 30.0,
                "xst_FMKD2": 30.0,
                "apt_FMKD": 30.0,
            },
            load_rate=0.0,
            inlet_so2_rate=0.0,
            outlet_so2_rate=0.0,
            disturbance_mode="STEADY",
            control_mode="NORMAL",
            policy_state_key="GRID=P1-S1|REGULAR",
            policy_state_key_no_grid="REGULAR",
        )

    def _demand(self) -> ControlDemand:
        return ControlDemand(
            commanded_target=20.0,
            effective_target=20.0,
            current_so2=25.0,
            error=5.0,
            demand_level="TARGET_MEDIUM",
            desired_so2_response="SO2_DOWN",
            acceptable_effect_directions=["DECREASE"],
            maximum_action_magnitude="MEDIUM",
            safety_level="NORMAL",
        )

    def test_coarse_state_does_not_split_by_ph_or_valve_opening(self):
        row_a = {
            "anchor_grid_id": "P1-S1",
            "disturbance_mode": "STEADY",
            "before_outlet_so2": 25.0,
            "before_ph__xst": 4.8,
            "before_ph__apt": 5.8,
            "before_valve__xst_v1": 20.0,
            "before_valve__xst_v2": 20.0,
            "before_valve__apt_v1": 20.0,
        }
        row_b = dict(row_a)
        row_b.update(
            {
                "before_outlet_so2": 30.0,
                "before_ph__xst": 5.4,
                "before_ph__apt": 6.3,
                "before_valve__xst_v1": 80.0,
                "before_valve__xst_v2": 60.0,
                "before_valve__apt_v1": 70.0,
            }
        )
        self.assertEqual(build_policy_state(row_a, self.plant, self.training)[1], "REGULAR")
        self.assertEqual(build_policy_state(row_b, self.plant, self.training)[1], "REGULAR")

    def test_target_matching_prefers_action_closest_to_current_gap(self):
        small = Candidate(
            source="LOCAL_CONDITION",
            owner_id="365",
            state_key="REGULAR",
            action_id="TOWER:xst|SUPPLY|INCREASE|SMALL",
            profile=_profile(
                "TOWER:xst|SUPPLY", "INCREASE", "SMALL", -2.5, "xst",
                0.05, 0.10, 0.15,
                {"xst_v1": 2.0, "xst_v2": 2.0, "apt_v1": 0.0},
            ),
            source_priority=4,
        )
        medium = Candidate(
            source="LOCAL_CONDITION",
            owner_id="365",
            state_key="REGULAR",
            action_id="TOWER:apt|SUPPLY|INCREASE|MEDIUM",
            profile=_profile(
                "TOWER:apt|SUPPLY", "INCREASE", "MEDIUM", -4.7, "apt",
                0.05, 0.10, 0.15,
                {"xst_v1": 0.0, "xst_v2": 0.0, "apt_v1": 3.0},
            ),
            source_priority=4,
        )
        selected = CandidateRanker().rank([small, medium], self._demand())
        self.assertIs(selected, medium)
        self.assertAlmostEqual(selected.evaluation["predicted_remaining_error"], 0.3)

    def test_action_tower_ph_prediction_filters_risky_tower(self):
        state = self._state(xst_ph=5.40, apt_ph=6.00)
        demand = self._demand()
        xst = Candidate(
            source="LOCAL_CONDITION",
            owner_id="365",
            state_key="REGULAR",
            action_id="TOWER:xst|SUPPLY|INCREASE|MEDIUM",
            profile=_profile(
                "TOWER:xst|SUPPLY", "INCREASE", "MEDIUM", -4.8, "xst",
                0.10, 0.18, 0.25,
                {"xst_v1": 3.0, "xst_v2": 3.0, "apt_v1": 0.0},
            ),
            source_priority=4,
        )
        apt = Candidate(
            source="LOCAL_CONDITION",
            owner_id="365",
            state_key="REGULAR",
            action_id="TOWER:apt|SUPPLY|INCREASE|MEDIUM",
            profile=_profile(
                "TOWER:apt|SUPPLY", "INCREASE", "MEDIUM", -4.2, "apt",
                0.04, 0.08, 0.12,
                {"xst_v1": 0.0, "xst_v2": 0.0, "apt_v1": 3.0},
            ),
            source_priority=4,
        )
        accepted, rejected = CandidateFilter(self.plant, self.online).filter(
            [xst, apt], state, demand, {}, {}
        )
        self.assertNotIn(xst, accepted)
        self.assertIn(apt, accepted)
        self.assertIn(
            "PREDICTED_PH_ABOVE_SAFE_RANGE:xst",
            rejected[xst.action_id],
        )

    def test_tower_equivalent_action_is_reallocated_to_all_tower_valves(self):
        state = self._state()
        candidate = Candidate(
            source="LOCAL_CONDITION",
            owner_id="365",
            state_key="REGULAR",
            action_id="TOWER:xst|SUPPLY|INCREASE|SMALL",
            profile=_profile(
                "TOWER:xst|SUPPLY", "INCREASE", "SMALL", -2.5, "xst",
                0.05, 0.10, 0.15,
                {"xst_v1": 2.0, "xst_v2": 0.0, "apt_v1": 0.0},
            ),
            source_priority=4,
        )
        resolved = ValveActionResolver(self.plant, self.online).resolve(candidate, state)
        # 两个一级塔阀门量程均为100。历史代表分阀增量 2 和 0 对应塔级等效
        # 归一化动作 1%，因此在线重新分配为两个阀各 +1 个百分点。
        self.assertAlmostEqual(resolved.recommended_valve_deltas["xst_v1"], 1.0)
        self.assertAlmostEqual(resolved.recommended_valve_deltas["xst_v2"], 1.0)
        self.assertEqual(resolved.recommended_valve_deltas["apt_v1"], 0.0)


if __name__ == "__main__":
    unittest.main()
