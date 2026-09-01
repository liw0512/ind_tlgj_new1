import numpy as np
import pandas as pd

from system.model.map_control.slurry_policy_model.adaptive_feedback.config import InitialTrainingConfig
from system.model.map_control.slurry_policy_model.adaptive_feedback.event_bootstrap import extract_action_events
from system.model.map_control.slurry_policy_model.adaptive_feedback.qbase_calibration import calibrate_kbase
from system.model.map_control.slurry_policy_model.adaptive_feedback.response_estimator import (
    build_hierarchical_response_knowledge,
    estimate_event_responses,
)
from system.model.map_control.slurry_policy_model.adaptive_feedback.snapshot import build_initial_snapshot


def _synthetic_response_frame() -> pd.DataFrame:
    n = 160
    timestamps = pd.date_range("2026-01-01", periods=n, freq="10s")
    flow = np.full(n, 20.0)
    flow[50:] = 30.0

    outlet = np.zeros(n, dtype=float)
    outlet[0] = 20.0
    for index in range(1, n):
        if index < 55:
            step = 0.20
        elif index < 62:
            # Still rising after +Q, but much more slowly: this is onset evidence.
            step = 0.05
        else:
            step = -0.10
        outlet[index] = outlet[index - 1] + step

    ph = np.full(n, 6.0)
    for index in range(60, n):
        ph[index] = ph[index - 1] + 0.002

    return pd.DataFrame(
        {
            "date": timestamps,
            "yyq_SO2": 1800.0,
            "jyq_SO2": outlet,
            "yyq_LL": 800000.0,
            "xstshsjy_MD": 1088.0,
            "xstshsjy_LL": flow,
            "xstjy_PH": ph,
            "condition_label": "C2",
            "combined_pump_status": "1-1-1",
        }
    )


def test_kbase_calibration_preserves_engineering_omega_percent_relation():
    config = InitialTrainingConfig(
        sample_seconds=60,
        kbase_window_hours=1,
        kbase_min_windows=3,
        kbase_min_window_coverage=0.9,
        kbase_holdout_fraction=0.0,
    )
    n = 8 * 60
    timestamps = pd.date_range("2026-01-01", periods=n, freq="1min")
    inlet = np.full(n, 1800.0)
    outlet = np.full(n, 20.0)
    gas = np.full(n, 800000.0)
    rho = np.full(n, 1088.0)
    omega_fraction = (config.omega_k * rho + config.omega_c) / 100.0
    qraw = (
        (inlet - outlet)
        * gas
        / 1_000_000.0
        * 100.0
        / 64.0
        / (config.limestone_purity * omega_fraction * rho)
        * config.ca_s_reference
    )
    expected_kbase = 0.15
    frame = pd.DataFrame(
        {
            "date": timestamps,
            "yyq_SO2": inlet,
            "jyq_SO2": outlet,
            "yyq_LL": gas,
            "xstshsjy_MD": rho,
            "xstshsjy_LL": expected_kbase * qraw,
        }
    )

    result, _, stream = calibrate_kbase(frame, config, backtest_hours=(1,))
    assert abs(result.kbase - expected_kbase) < 1e-9
    assert np.allclose(stream["omega_percent"].dropna().to_numpy(), config.omega_k * 1088.0 + config.omega_c)


def test_so2_onset_can_be_detected_while_absolute_so2_is_still_rising():
    config = InitialTrainingConfig(
        minimum_global_effective_events=0.1,
        full_confidence_effective_events=1.0,
        full_confidence_independent_days=1,
        adaptive_confidence_threshold=0.1,
        minimum_physics_sign_consistency=0.5,
    )
    frame = _synthetic_response_frame()
    events, work = extract_action_events(frame, config)
    responses = estimate_event_responses(events, work, config)

    so2 = responses.loc[responses["response"].eq("SO2")].iloc[0]
    action_index = int(events.iloc[0]["action_index"])
    onset_index = action_index + int(round(so2["onset_seconds"] / config.sample_seconds))

    assert so2["response_status"] == "EFFECT_ESTIMATED"
    assert 0 < so2["onset_seconds"] < 100
    assert work.loc[onset_index, "jyq_SO2"] > work.loc[action_index, "jyq_SO2"]
    assert so2["phi"] < 0
    assert bool(so2["physics_sign_ok"])


def test_ph_has_independent_onset_and_effect_from_so2():
    config = InitialTrainingConfig(
        minimum_global_effective_events=0.1,
        full_confidence_effective_events=1.0,
        full_confidence_independent_days=1,
        adaptive_confidence_threshold=0.1,
        minimum_physics_sign_consistency=0.5,
    )
    events, work = extract_action_events(_synthetic_response_frame(), config)
    responses = estimate_event_responses(events, work, config)
    so2 = responses.loc[responses["response"].eq("SO2")].iloc[0]
    ph = responses.loc[responses["response"].eq("PH")].iloc[0]

    assert so2["onset_seconds"] != ph["onset_seconds"]
    assert so2["phi"] < 0
    assert ph["phi"] > 0


def test_sparse_condition_falls_back_to_global_instead_of_requiring_hold():
    config = InitialTrainingConfig(
        minimum_global_effective_events=0.1,
        full_confidence_effective_events=1.0,
        full_confidence_independent_days=1,
        adaptive_confidence_threshold=0.1,
        minimum_physics_sign_consistency=0.5,
    )
    events, work = extract_action_events(_synthetic_response_frame(), config)
    responses = estimate_event_responses(events, work, config)
    knowledge = build_hierarchical_response_knowledge(responses, config)

    c4_so2 = knowledge["conditions"]["C4"]["SO2"]["INCREASE"]
    assert c4_so2["local"]["event_count"] == 0
    assert c4_so2["effective_phi"] == knowledge["responses"]["SO2"]["INCREASE"]["phi_median"]
    assert c4_so2["recommended_online_source"] == "GLOBAL_FALLBACK"
    assert c4_so2["no_local_data_requires_hold"] is False


def test_initial_snapshot_does_not_freeze_or_learn_online_so2_target():
    config = InitialTrainingConfig()
    calibration_config = InitialTrainingConfig(
        sample_seconds=600,
        kbase_window_hours=24,
        kbase_min_windows=3,
        kbase_min_window_coverage=0.9,
        kbase_holdout_fraction=0.0,
    )
    kbase_frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=4 * 24 * 6, freq="10min"),
            "yyq_SO2": 1800.0,
            "jyq_SO2": 20.0,
            "yyq_LL": 800000.0,
            "xstshsjy_MD": 1088.0,
            "xstshsjy_LL": 20.0,
        }
    )
    kbase, _, _ = calibrate_kbase(kbase_frame, calibration_config)
    snapshot = build_initial_snapshot(
        snapshot_version="v001",
        config=config,
        kbase=kbase,
        response_knowledge={"responses": {}, "conditions": {}},
        source_rows=len(kbase_frame),
        learnable_action_events=0,
    )

    assert snapshot["runtime_contract"]["outlet_so2_target_source"] == "RUNTIME_REQUIRED"
    assert snapshot["runtime_contract"]["fixed_outlet_so2_target_in_snapshot"] is False
    assert "outlet_so2_target" not in snapshot["qbase"]
