from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .supply_flow_event_detector import SupplyFlowEvent
from .supply_flow_event_classifier import SupplyFlowEventClassification


@dataclass(frozen=True)
class SupplyFlowContextClassification:
    """Attribution context of a supply-flow event.

    This layer does not judge whether an action is good or bad. It only decides
    whether the event is suitable for learning an independent slurry response.
    """

    context: str
    learning_eligible: bool
    circulation_change: bool
    major_process_transition: bool
    reason: str


def classify_supply_flow_context(
    event: SupplyFlowEvent,
    shape: SupplyFlowEventClassification,
    frame: pd.DataFrame | None = None,
    *,
    timestamp_column: str = "timestamp",
    circulation_columns: Iterable[str] = (),
    process_transition_columns: Iterable[str] = (),
) -> SupplyFlowContextClassification:
    """Classify event attribution without attempting system identification.

    CLEAN events are the only default source for independent slurry-effect
    learning.  Compound events are preserved because they are valuable for
    future coordinated pump/slurry policies.
    """
    if frame is None or frame.empty:
        return SupplyFlowContextClassification(
            context="UNRESOLVED_COMPOUND",
            learning_eligible=False,
            circulation_change=False,
            major_process_transition=False,
            reason="NO_CONTEXT_SIGNALS",
        )

    start = event.start_time
    end = event.end_time
    window = frame
    if timestamp_column in frame.columns:
        ts = pd.to_datetime(frame[timestamp_column], errors="coerce")
        window = frame.loc[(ts >= start) & (ts <= end)]

    circulation_change = False
    for column in circulation_columns:
        if column in window.columns:
            values = pd.to_numeric(window[column], errors="coerce").dropna()
            if not values.empty and float(values.max() - values.min()) > 0:
                circulation_change = True
                break

    major_transition = False
    for column in process_transition_columns:
        if column in window.columns:
            values = pd.to_numeric(window[column], errors="coerce").dropna()
            if not values.empty and float(values.max() - values.min()) > 0:
                major_transition = True
                break

    if circulation_change:
        return SupplyFlowContextClassification(
            context="COORDINATED",
            learning_eligible=False,
            circulation_change=True,
            major_process_transition=major_transition,
            reason="CIRCULATION_CHANGED_DURING_EVENT",
        )

    if major_transition:
        return SupplyFlowContextClassification(
            context="TRANSIENT",
            learning_eligible=False,
            circulation_change=False,
            major_process_transition=True,
            reason="PROCESS_STATE_CHANGED_DURING_EVENT",
        )

    if shape.shape == "COMPLEX":
        return SupplyFlowContextClassification(
            context="UNRESOLVED_COMPOUND",
            learning_eligible=False,
            circulation_change=False,
            major_process_transition=False,
            reason="FLOW_SHAPE_NOT_SIMPLE",
        )

    return SupplyFlowContextClassification(
        context="CLEAN",
        learning_eligible=True,
        circulation_change=False,
        major_process_transition=False,
        reason="ISOLATED_SUPPLY_FLOW_EVENT",
    )


def classify_supply_flow_contexts(
    rows: Iterable[tuple[SupplyFlowEvent, SupplyFlowEventClassification]],
    **kwargs,
) -> list[SupplyFlowContextClassification]:
    return [classify_supply_flow_context(event, shape, **kwargs) for event, shape in rows]
