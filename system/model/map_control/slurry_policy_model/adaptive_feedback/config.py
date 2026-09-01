from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class InitialTrainingConfig:
    """Configuration for Module-2 offline initial identification.

    The configuration deliberately contains no online outlet-SO2 target. Historical
    Qbase calibration uses the measured historical outlet concentration; the online
    target is a runtime input owned by the future online module.
    """

    timestamp_column: str = "date"
    inlet_so2_column: str = "yyq_SO2"
    outlet_so2_column: str = "jyq_SO2"
    gas_flow_column: str = "yyq_LL"
    density_column: str = "xstshsjy_MD"
    actual_flow_column: str = "xstshsjy_LL"
    ph_column: str = "xstjy_PH"
    condition_column: str = "condition_label"
    topology_columns: Tuple[str, ...] = ("combined_pump_status",)

    sample_seconds: int = 10
    max_gap_multiple: float = 3.0

    # Engineering-sheet Qbase constants. omega = k * rho + c is in percent.
    omega_k: float = 0.0013
    omega_c: float = 1.3
    ca_s_reference: float = 1.70
    limestone_purity: float = 0.90

    # Kbase calibration uses long-horizon material balance.
    kbase_window_hours: int = 24
    kbase_min_window_coverage: float = 0.75
    kbase_min_windows: int = 3
    kbase_holdout_fraction: float = 0.30

    # Action extraction.
    min_action_delta_m3h: float = 4.0
    candidate_flow_diff_m3h: float = 2.0
    action_cluster_seconds: int = 40
    action_pre_seconds: int = 60
    action_post_seconds: int = 60
    action_refractory_seconds: int = 180
    max_response_horizon_seconds: int = 420

    # Event quality. A is strict, B admits moderate disturbances with lower weight.
    grade_a_inlet_so2_change: float = 80.0
    grade_b_inlet_so2_change: float = 180.0
    grade_a_gas_relative_change: float = 0.03
    grade_b_gas_relative_change: float = 0.08
    grade_a_weight: float = 1.0
    grade_b_weight: float = 0.35

    # Response onset detection. Slopes are engineering rates per minute.
    slope_window_seconds: int = 40
    onset_search_start_seconds: int = 20
    onset_search_end_seconds: int = 240
    onset_persistence_windows: int = 2
    so2_min_slope_improvement_per_min: float = 0.12
    ph_min_slope_improvement_per_min: float = 0.003

    # Effect is evaluated later than onset and relative to the pre-action local trend.
    effect_start_after_onset_seconds: int = 40
    effect_end_after_onset_seconds: int = 180
    effect_tail_seconds: int = 40

    # Hierarchical statistics.
    known_condition_labels: Tuple[str, ...] = (
        "C1",
        "C2",
        "C3",
        "C4",
        "EDGE_LOW",
        "EDGE_HIGH",
    )
    minimum_global_effective_events: float = 3.0
    full_confidence_effective_events: float = 20.0
    full_confidence_independent_days: int = 7
    shrinkage_reference_weight: float = 20.0
    adaptive_confidence_threshold: float = 0.55
    minimum_physics_sign_consistency: float = 0.70

    def validate(self) -> None:
        if self.sample_seconds <= 0:
            raise ValueError("sample_seconds must be positive")
        if self.kbase_window_hours <= 0:
            raise ValueError("kbase_window_hours must be positive")
        if not 0.0 < self.kbase_min_window_coverage <= 1.0:
            raise ValueError("kbase_min_window_coverage must be in (0, 1]")
        if not 0.0 <= self.kbase_holdout_fraction < 0.5:
            raise ValueError("kbase_holdout_fraction must be in [0, 0.5)")
        if self.min_action_delta_m3h <= 0:
            raise ValueError("min_action_delta_m3h must be positive")
        if self.candidate_flow_diff_m3h <= 0:
            raise ValueError("candidate_flow_diff_m3h must be positive")
        if self.onset_persistence_windows <= 0:
            raise ValueError("onset_persistence_windows must be positive")
        if not 0.5 <= self.minimum_physics_sign_consistency <= 1.0:
            raise ValueError("minimum_physics_sign_consistency must be in [0.5, 1]")
        if not 0.0 < self.grade_b_weight <= self.grade_a_weight:
            raise ValueError("event weights must satisfy 0 < B <= A")
