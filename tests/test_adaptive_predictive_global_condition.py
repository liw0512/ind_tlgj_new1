import numpy as np
import pandas as pd

from system.model.map_control.slurry_policy_model.adaptive_predictive import (
    GLOBAL_CONDITION_MODEL_TYPE,
    GlobalConditionResponseConfig,
    build_foundation_spec,
    fit_tower_dual_response,
)
from system.model.map_control.slurry_policy_model.adaptive_predictive.feature_builder import (
    CausalFeatureConfig,
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


def _synthetic_closed_loop_frame(rows=2200):
    index = np.arange(rows, dtype=float)
    yyq_so2 = 1800.0 + 120.0 * np.sin(index / 80.0) + 35.0 * np.sin(index / 17.0)
    yyq_ll = 820000.0 + 45000.0 * np.sin(index / 130.0)

    flow = 22.0 + 1.5 * np.sin(index / 55.0)
    for start, delta in ((250, 4.0), (520, -3.0), (850, 5.0), (1180, -4.0), (1510, 3.5), (1840, -2.5)):
        flow[start:] += delta

    block = (np.arange(rows) // 300) % 2
    condition = np.where(block == 0, "10002", "10004")
    so2 = np.zeros(rows, dtype=float)
    ph = np.zeros(rows, dtype=float)
    so2[0] = 10.0
    ph[0] = 6.2

    flow_delta = np.diff(flow, prepend=flow[0])
    for t in range(1, rows):
        lag = max(0, t - 3)
        q_gain = 0.10 if condition[t] == "10002" else 0.18
        ph_gain = 0.010 if condition[t] == "10002" else 0.016
        so2[t] = (
            0.92 * so2[t - 1]
            + 0.80
            + 0.0025 * (yyq_so2[t - 1] - 1800.0)
            - q_gain * flow_delta[lag]
        )
        ph[t] = (
            0.96 * ph[t - 1]
            + 0.248
            - 0.0000015 * (yyq_ll[t - 1] - 820000.0)
            + ph_gain * flow_delta[lag]
        )

    return pd.DataFrame(
        {
            "yyq_SO2": yyq_so2,
            "yyq_LL": yyq_ll,
            "xstshsjy_LL": flow,
            "jyq_SO2": so2,
            "xstjy_PH": ph,
            "condition_label": condition,
            "continuous_segment_id": 1,
        }
    )


def test_operating_context_is_separate_from_hard_condition_axis():
    plant = {
        "condition_axes": [{"column": "yyq_SO2"}],
        "predictive_context_columns": ["yyq_LL"],
        "towers": [TOWER],
    }
    spec = build_foundation_spec(plant)
    assert spec.disturbance_columns == ("yyq_SO2",)
    assert spec.context_columns == ("yyq_LL",)
    assert spec.condition_label_column == "condition_label"


def test_dual_response_uses_global_backbone_plus_shrunk_q_path_corrections():
    frame = _synthetic_closed_loop_frame()
    feature_config = CausalFeatureConfig(
        output_delta_lags=4,
        flow_delta_lags=18,
        disturbance_delta_lags=18,
        context_delta_lags=6,
        causal_flow_filter_points=1,
    )
    response_config = GlobalConditionResponseConfig(
        global_ridge_alpha=5.0,
        condition_ridge_alpha=100.0,
        train_ratio=0.70,
        minimum_train_rows=700,
        minimum_validation_rows=250,
        minimum_condition_train_rows=250,
        shrinkage_reference_rows=1000.0,
        condition_column="condition_label",
    )
    result = fit_tower_dual_response(
        frame,
        tower=TOWER,
        outlet_so2_column="jyq_SO2",
        disturbance_columns=("yyq_SO2",),
        context_columns=("yyq_LL",),
        feature_config=feature_config,
        response_config=response_config,
    )

    for channel, expected_effect in (
        (result.so2, "NEGATIVE_Q_TO_SO2"),
        (result.ph, "POSITIVE_Q_TO_PH"),
    ):
        payload = channel.model_payload
        assert payload["model_type"] == GLOBAL_CONDITION_MODEL_TYPE
        assert payload["decomposition"] == "G_CURRENT = G_GLOBAL + DELTA_G_CONDITION"
        assert payload["condition_correction_scope"] == "Q_PATH_ONLY"
        assert payload["expected_q_effect"] == expected_effect
        assert payload["context_columns"] == ["yyq_LL"]
        assert channel.condition_model_count >= 2
        for correction in payload["condition_flow_corrections"].values():
            assert correction["semantics"] == "DELTA_G_CONDITION_ON_Q_PATH_ONLY"
            assert 0.0 < correction["shrinkage_factor"] < 1.0
            assert all(
                name == "flow_level_t" or name.startswith("flow_delta_lag_")
                for name in correction["feature_names"]
            )
        assert "global" in channel.validation["validation"]
        assert "global_plus_condition" in channel.validation["validation"]
