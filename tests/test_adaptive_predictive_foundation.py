import unittest

from system.model.map_control.slurry_policy_model.adaptive_predictive import (
    IdentifiabilityLevel,
    ResponseChannelSpec,
    ResponseModelArtifact,
    assess_episode_identifiability,
    build_foundation_spec,
)


TOWER = {
    "tower_id": "xst",
    "enabled": True,
    "ph_column": "xstjy_PH",
    "ph_safe_range": [5.6, 6.8],
    "supply_flows": [
        {"flow_id": "main", "column": "xstshsjy_LL"},
    ],
}


class PredictiveFoundationTest(unittest.TestCase):
    def test_steel_and_power_plants_share_code_but_not_disturbance_parameters(self):
        steel = {
            "condition_axes": [{"column": "yyq_SO2"}],
            "towers": [TOWER],
        }
        power = {
            "condition_axes": [
                {"column": "jzfh"},
                {"column": "yyq_SO2"},
            ],
            "towers": [TOWER],
            "realtime_monitor": {
                "inlet_signals": [
                    {"column": "yyq_SO2"},
                    {"column": "yyq_LL"},
                ]
            },
        }

        steel_spec = build_foundation_spec(steel)
        power_spec = build_foundation_spec(power)

        self.assertEqual(steel_spec.disturbance_columns, ("yyq_SO2",))
        self.assertEqual(power_spec.disturbance_columns, ("jzfh", "yyq_SO2"))
        # yyq_LL is not auto-added merely because it exists as a realtime signal.
        self.assertNotIn("yyq_LL", power_spec.disturbance_columns)
        self.assertTrue(steel_spec.shadow_only)
        self.assertTrue(power_spec.shadow_only)
        self.assertEqual(steel_spec.prediction_steps, 60)

    def test_historical_bad_outcome_is_not_identifiability_failure(self):
        row = {
            "flow_event_complete": True,
            "flow_learning_eligible": True,
            "flow_effect_complete": True,
            "flow_event_max_abs_delta_flow": 10.0,
            "condition_valid": True,
            # Bad/unsafe outcome is intentionally not part of the physical
            # identifiability gate.
            "ph_out_of_range__xst": True,
            "outlet_so2_over_hard_max": True,
        }
        result = assess_episode_identifiability(row)
        self.assertEqual(result.level, IdentifiabilityLevel.IDENTIFIABLE)
        self.assertEqual(result.weight, 1.0)

    def test_measured_disturbance_is_weak_not_discarded(self):
        row = {
            "flow_event_complete": True,
            "flow_learning_eligible": True,
            "flow_effect_complete": True,
            "flow_event_max_abs_delta_flow": 8.0,
            "condition_valid": True,
            "flow_major_process_transition": True,
            "is_transient": True,
        }
        result = assess_episode_identifiability(row)
        self.assertEqual(result.level, IdentifiabilityLevel.WEAKLY_IDENTIFIABLE)
        self.assertGreater(result.weight, 0.0)
        self.assertIn("MEASURED_PROCESS_DISTURBANCE_PRESENT", result.reason_codes)

    def test_manipulated_path_topology_change_is_hard_blocker(self):
        row = {
            "flow_event_complete": True,
            "flow_learning_eligible": True,
            "flow_effect_complete": True,
            "flow_event_max_abs_delta_flow": 8.0,
            "condition_valid": True,
            "flow_circulation_change": True,
        }
        result = assess_episode_identifiability(row)
        self.assertEqual(result.level, IdentifiabilityLevel.UNIDENTIFIABLE)
        self.assertEqual(result.weight, 0.0)

    def test_response_artifact_round_trip(self):
        channel = ResponseChannelSpec(
            channel_id="xst:ph",
            output_column="xstjy_PH",
            tower_id="xst",
            manipulated_flow_columns=("xstshsjy_LL",),
            disturbance_columns=("yyq_SO2",),
            sample_seconds=10,
            prediction_steps=60,
        )
        artifact = ResponseModelArtifact(
            model_type="ARX_RIDGE_V1",
            source_policy_version="v001",
            source_condition_version="v001",
            channels=[channel],
            model_payloads={"xst:ph": {"intercept": 0.0}},
        )
        restored = ResponseModelArtifact.from_dict(artifact.to_dict())
        self.assertEqual(restored.model_type, "ARX_RIDGE_V1")
        self.assertEqual(restored.channels[0].output_column, "xstjy_PH")
        self.assertEqual(restored.channels[0].disturbance_columns, ("yyq_SO2",))


if __name__ == "__main__":
    unittest.main()
