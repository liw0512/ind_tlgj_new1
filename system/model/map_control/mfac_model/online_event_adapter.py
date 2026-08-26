# -*- coding: utf-8 -*-
"""Convert online process-response evidence into canonical MFAC events."""

from typing import Any, Mapping, Optional

from .mfac_eligibility import StrictMFACEligibilityGate
from .mfac_schema import ActionResponseEvent
from .process_response import ProcessResponseEvent


class OnlineResponseToMFACAdapter:
    """Build canonical ``ActionResponseEvent`` from one online response event.

    The adapter preserves rejected/insufficient events for audit.  It does not
    update ``phi`` itself; callers must pass the resulting event to the online
    adaptation stage only when ``learning_eligible`` is true.
    """

    def __init__(self, gate: StrictMFACEligibilityGate) -> None:
        self.gate = gate

    def adapt(
        self,
        response: ProcessResponseEvent,
        *,
        condition_label: str,
        base_condition_id: str,
        grid_id: str = "",
        policy_region_id: str = "",
        equipment_changed: bool = False,
        extra_metadata: Optional[Mapping[str, Any]] = None,
    ) -> ActionResponseEvent:
        completed = response.status == "COMPLETED"
        qbase_available = (
            response.qbase_before is not None
            and response.qbase_after is not None
            and response.qbase_drift is not None
        )
        target_available = response.so2_target is not None
        context_available = bool(
            response.condition_snapshot_version and response.mfac_context_id
        )

        evidence = {
            "flow_shape": "STEP",
            "flow_disturbance_class": "STEADY" if not response.fast_overlap else "FAST",
            "scheme1_valid": completed and response.data_quality_ok,
            "effect_complete": completed,
            "flow_context_eligible": (
                completed
                and response.data_quality_ok
                and not response.fast_overlap
                and not response.condition_changed
                and not response.target_changed
            ),
            "followup_action_in_response": response.censor_reason
            == "SUPERSEDED_BY_NEW_REACHED_EVENT",
            "circulation_changed": False,
            "major_process_transition": False,
            "equipment_changed": bool(equipment_changed),
            "context_stability_evidence_available": context_available,
            "condition_context_changed": response.condition_changed,
            "target_evidence_available": target_available,
            "target_changed": response.target_changed,
            "qbase_evidence_available": qbase_available,
            "qbase_before": response.qbase_before,
            "qbase_after": response.qbase_after,
            "qbase_drift": response.qbase_drift,
            "delta_q_actual": response.delta_q_actual,
            "delta_so2": response.delta_so2,
        }
        decision = self.gate.evaluate(evidence)
        phi_event = decision.metrics.get("phi_event")

        metadata = {
            "response_event_id": response.response_event_id,
            "tracking_event_id": response.tracking_event_id,
            "response_status": response.status,
            "response_censor_reason": response.censor_reason,
            "eligibility_decision": decision.to_dict(),
            "execution_delay_seconds": response.metadata.get(
                "execution_delay_seconds"
            ),
            "process_delay_seconds": response.metadata.get(
                "delay_onset_seconds"
            ),
            "response_metadata": dict(response.metadata or {}),
        }
        metadata.update(dict(extra_metadata or {}))

        return ActionResponseEvent(
            event_id=f"MFAC-ONLINE-{response.response_event_id}",
            condition_snapshot_version=response.condition_snapshot_version,
            condition_label=str(condition_label or ""),
            base_condition_id=str(base_condition_id or ""),
            grid_id=str(grid_id or ""),
            policy_region_id=str(policy_region_id or ""),
            mfac_context_id=response.mfac_context_id,
            action_start_time=response.target_change_time,
            action_reached_time=response.actual_flow_reached_time,
            response_start_time=response.response_start_time,
            response_end_time=response.response_end_time,
            action_source="ONLINE_DCS_APPLIED_TARGET",
            q_before=response.q_before,
            q_after=response.q_after,
            delta_q_actual=response.delta_q_actual,
            qbase_before=response.qbase_before,
            qbase_after=response.qbase_after,
            qbase_drift=response.qbase_drift,
            so2_target=response.so2_target,
            so2_before=response.so2_before,
            so2_after=response.so2_after,
            delta_so2=response.delta_so2,
            ph_before=response.ph_before,
            ph_after=response.ph_after,
            delta_ph=response.delta_ph,
            inlet_so2_change=response.inlet_so2_change,
            load_change=None,
            fast_overlap=response.fast_overlap,
            equipment_changed=bool(equipment_changed),
            target_changed=response.target_changed,
            condition_changed=response.condition_changed,
            data_quality_ok=response.data_quality_ok,
            learning_eligible=decision.eligible,
            reject_reason="|".join(decision.reasons),
            phi_event=float(phi_event) if phi_event is not None else None,
            quality_score=None,
            metadata=metadata,
        )
