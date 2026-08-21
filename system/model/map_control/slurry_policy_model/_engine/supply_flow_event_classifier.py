from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .supply_flow_event_detector import SupplyFlowEvent


# Batch 2B deliberately keeps the shape rules plant-independent.  The detector
# already converts plant-specific signal noise/scale into ``trigger_deadband``;
# classification therefore uses relative trajectory geometry rather than fixed
# m3/h thresholds.
_STEP_PERSISTENT_RATIO_MIN = 0.75
_BOOST_OVERSHOOT_RATIO_MIN = 0.20
_MAX_SIMPLE_TRANSITIONS = 3


@dataclass(frozen=True)
class SupplyFlowEventClassification:
    """Shape interpretation of one already-segmented slurry-flow event.

    This layer does not score SO2/pH effect and does not decide whether the
    event is good or bad.  It only describes how actual slurry flow evolved.
    """

    shape: str
    direction: str
    persistent_ratio: float
    return_ratio: float
    overshoot_delta_flow: float
    overshoot_ratio: float
    return_tolerance: float
    crosses_baseline: bool
    temporary_plateau: bool
    temporary_plateau_count: int
    flow_execution_profile: str
    classification_reason: str


def _safe_ratio(numerator: float, denominator: float) -> float:
    denominator = abs(float(denominator))
    if denominator <= 1e-12:
        return 0.0
    return abs(float(numerator)) / denominator


def _return_tolerance(event: SupplyFlowEvent) -> float:
    """Tolerance for saying the final plateau returned to the old baseline."""
    return max(
        float(event.trigger_deadband),
        2.5 * float(event.baseline_noise_sigma),
        1e-12,
    )


def _temporary_plateau_count(event: SupplyFlowEvent) -> int:
    """Count stable intermediate plateaus already proven by segmentation.

    Every elementary transition is closed only after ``stable_minutes`` of a
    stable platform.  Once nearby transitions are merged, all but the last
    platform are therefore temporary plateaus of the complete trajectory.
    """
    return max(0, int(event.transition_count) - 1)


def classify_supply_flow_event(
    event: SupplyFlowEvent,
) -> SupplyFlowEventClassification:
    """Classify one trajectory as HOLD/STEP/PULSE/BOOST_STEP/COMPLEX.

    Rule order matters:
    1. incomplete or clearly multi-directional events are conservative COMPLEX;
    2. final plateau back inside the old-baseline tolerance is PULSE;
    3. a sustained final plateau close to the extreme is STEP;
    4. a sustained final plateau with a material transient excess is BOOST_STEP;
    5. ambiguous geometry remains COMPLEX instead of being forced into a class.
    """
    tolerance = _return_tolerance(event)
    temporary_plateau_count = _temporary_plateau_count(event)
    temporary_plateau = temporary_plateau_count > 0
    baseline = float(event.baseline_flow)
    positive_excursion = float(event.peak_flow) - baseline
    negative_excursion = float(event.trough_flow) - baseline

    positive_active = positive_excursion >= tolerance
    negative_active = negative_excursion <= -tolerance
    crosses_baseline = bool(positive_active and negative_active)

    if event.max_abs_delta_flow < tolerance:
        return SupplyFlowEventClassification(
            shape="HOLD",
            direction="HOLD",
            persistent_ratio=0.0,
            return_ratio=0.0,
            overshoot_delta_flow=0.0,
            overshoot_ratio=0.0,
            return_tolerance=tolerance,
            crosses_baseline=False,
            temporary_plateau=False,
            temporary_plateau_count=0,
            flow_execution_profile="NO_EFFECTIVE_FLOW_CHANGE",
            classification_reason="BELOW_EFFECTIVE_DEADBAND",
        )

    if positive_active and not negative_active:
        direction = "INCREASE"
        directional_peak = positive_excursion
    elif negative_active and not positive_active:
        direction = "DECREASE"
        directional_peak = abs(negative_excursion)
    else:
        direction = "MIXED"
        directional_peak = float(event.max_abs_delta_flow)

    final_delta = float(event.final_delta_flow)
    persistent_ratio = _safe_ratio(final_delta, directional_peak)
    return_ratio = persistent_ratio

    if direction == "INCREASE":
        same_direction_final = final_delta > tolerance
        overshoot_delta = max(0.0, directional_peak - max(final_delta, 0.0))
    elif direction == "DECREASE":
        same_direction_final = final_delta < -tolerance
        overshoot_delta = max(0.0, directional_peak - max(-final_delta, 0.0))
    else:
        same_direction_final = False
        overshoot_delta = 0.0

    overshoot_ratio = _safe_ratio(overshoot_delta, directional_peak)

    if not bool(event.complete):
        return SupplyFlowEventClassification(
            shape="COMPLEX",
            direction=direction,
            persistent_ratio=persistent_ratio,
            return_ratio=return_ratio,
            overshoot_delta_flow=overshoot_delta,
            overshoot_ratio=overshoot_ratio,
            return_tolerance=tolerance,
            crosses_baseline=crosses_baseline,
            temporary_plateau=temporary_plateau,
            temporary_plateau_count=temporary_plateau_count,
            flow_execution_profile="INCOMPLETE_TRAJECTORY",
            classification_reason="INCOMPLETE_EVENT",
        )

    if crosses_baseline:
        return SupplyFlowEventClassification(
            shape="COMPLEX",
            direction="MIXED",
            persistent_ratio=persistent_ratio,
            return_ratio=return_ratio,
            overshoot_delta_flow=overshoot_delta,
            overshoot_ratio=overshoot_ratio,
            return_tolerance=tolerance,
            crosses_baseline=True,
            temporary_plateau=temporary_plateau,
            temporary_plateau_count=temporary_plateau_count,
            flow_execution_profile="CROSSED_BASELINE",
            classification_reason="SIGNIFICANT_EXCURSION_ON_BOTH_SIDES",
        )

    if int(event.transition_count) > _MAX_SIMPLE_TRANSITIONS:
        return SupplyFlowEventClassification(
            shape="COMPLEX",
            direction=direction,
            persistent_ratio=persistent_ratio,
            return_ratio=return_ratio,
            overshoot_delta_flow=overshoot_delta,
            overshoot_ratio=overshoot_ratio,
            return_tolerance=tolerance,
            crosses_baseline=False,
            temporary_plateau=temporary_plateau,
            temporary_plateau_count=temporary_plateau_count,
            flow_execution_profile="MULTI_STAGE_TRAJECTORY",
            classification_reason="TOO_MANY_INTERNAL_TRANSITIONS",
        )

    # A pulse is defined by the whole event returning to the original platform,
    # not by after-before.  This captures 0->60->0 and 10->50->~10 as one event.
    if abs(final_delta) <= tolerance:
        return SupplyFlowEventClassification(
            shape="PULSE",
            direction=direction,
            persistent_ratio=persistent_ratio,
            return_ratio=return_ratio,
            overshoot_delta_flow=directional_peak,
            overshoot_ratio=1.0,
            return_tolerance=tolerance,
            crosses_baseline=False,
            temporary_plateau=temporary_plateau,
            temporary_plateau_count=temporary_plateau_count,
            flow_execution_profile=(
                "TEMPORARY_PLATEAU_THEN_BASELINE"
                if temporary_plateau
                else "EXCURSION_THEN_BASELINE"
            ),
            classification_reason="RETURNED_TO_BASELINE",
        )

    # Final plateau opposite to the main excursion is not a simple step/pulse.
    if not same_direction_final:
        return SupplyFlowEventClassification(
            shape="COMPLEX",
            direction=direction,
            persistent_ratio=persistent_ratio,
            return_ratio=return_ratio,
            overshoot_delta_flow=overshoot_delta,
            overshoot_ratio=overshoot_ratio,
            return_tolerance=tolerance,
            crosses_baseline=False,
            temporary_plateau=temporary_plateau,
            temporary_plateau_count=temporary_plateau_count,
            flow_execution_profile="EXCURSION_THEN_OPPOSITE_PLATEAU",
            classification_reason="FINAL_PLATEAU_OPPOSES_MAIN_DIRECTION",
        )

    # Modest transient excess is treated as an ordinary physical STEP because
    # real pump/DCS execution is rarely an ideal mathematical step.
    if persistent_ratio >= _STEP_PERSISTENT_RATIO_MIN:
        return SupplyFlowEventClassification(
            shape="STEP",
            direction=direction,
            persistent_ratio=persistent_ratio,
            return_ratio=return_ratio,
            overshoot_delta_flow=overshoot_delta,
            overshoot_ratio=overshoot_ratio,
            return_tolerance=tolerance,
            crosses_baseline=False,
            temporary_plateau=temporary_plateau,
            temporary_plateau_count=temporary_plateau_count,
            flow_execution_profile=(
                "TEMPORARY_PLATEAU_THEN_FINAL_PLATEAU"
                if temporary_plateau
                else "DIRECT_TO_FINAL_PLATEAU"
            ),
            classification_reason="FINAL_PLATEAU_CLOSE_TO_MAIN_EXTREME",
        )

    # A BOOST_STEP must leave a real new plateau and also contain a meaningful
    # transient excess above/below that plateau.  It is not labelled good/bad.
    if overshoot_delta >= tolerance and overshoot_ratio >= _BOOST_OVERSHOOT_RATIO_MIN:
        return SupplyFlowEventClassification(
            shape="BOOST_STEP",
            direction=direction,
            persistent_ratio=persistent_ratio,
            return_ratio=return_ratio,
            overshoot_delta_flow=overshoot_delta,
            overshoot_ratio=overshoot_ratio,
            return_tolerance=tolerance,
            crosses_baseline=False,
            temporary_plateau=temporary_plateau,
            temporary_plateau_count=temporary_plateau_count,
            flow_execution_profile=(
                "BOOST_PLATEAU_THEN_FINAL_PLATEAU"
                if temporary_plateau
                else "BOOST_EXCURSION_THEN_FINAL_PLATEAU"
            ),
            classification_reason="SUSTAINED_PLATEAU_WITH_MATERIAL_TRANSIENT_BOOST",
        )

    return SupplyFlowEventClassification(
        shape="COMPLEX",
        direction=direction,
        persistent_ratio=persistent_ratio,
        return_ratio=return_ratio,
        overshoot_delta_flow=overshoot_delta,
        overshoot_ratio=overshoot_ratio,
        return_tolerance=tolerance,
        crosses_baseline=False,
        temporary_plateau=temporary_plateau,
        temporary_plateau_count=temporary_plateau_count,
        flow_execution_profile="AMBIGUOUS_TRAJECTORY",
        classification_reason="AMBIGUOUS_TRAJECTORY_GEOMETRY",
    )


def classify_supply_flow_events(
    events: Iterable[SupplyFlowEvent],
) -> list[SupplyFlowEventClassification]:
    return [classify_supply_flow_event(event) for event in events]


def classified_supply_flow_events_to_frame(
    events: Iterable[SupplyFlowEvent],
) -> pd.DataFrame:
    """Flatten event features plus shape labels for manual offline review."""
    rows: list[dict[str, object]] = []
    for event in events:
        classification = classify_supply_flow_event(event)
        row = dict(event.__dict__)
        row.update(classification.__dict__)
        rows.append(row)
    if not rows:
        columns = [
            *SupplyFlowEvent.__dataclass_fields__.keys(),
            *SupplyFlowEventClassification.__dataclass_fields__.keys(),
        ]
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)
