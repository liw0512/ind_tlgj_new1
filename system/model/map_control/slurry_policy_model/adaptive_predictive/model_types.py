from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


RESPONSE_MODEL_SEMANTICS = "ADAPTIVE_PREDICTIVE_RESPONSE_V1"


@dataclass(frozen=True)
class ResponseChannelSpec:
    """One causal output channel in the predictive response model."""

    channel_id: str
    output_column: str
    tower_id: str | None
    manipulated_flow_columns: tuple[str, ...]
    disturbance_columns: tuple[str, ...]
    sample_seconds: int
    prediction_steps: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["manipulated_flow_columns"] = list(self.manipulated_flow_columns)
        value["disturbance_columns"] = list(self.disturbance_columns)
        return value


@dataclass
class ResponseModelArtifact:
    """Serializable metadata shared by offline identification and runtime.

    Coefficients/model payloads are intentionally represented as dictionaries
    at this layer so V1 ARX/FIR can later evolve without changing the snapshot
    loading contract.
    """

    model_semantics: str = RESPONSE_MODEL_SEMANTICS
    model_type: str = "UNTRAINED"
    source_policy_version: str | None = None
    source_condition_version: str | None = None
    channels: list[ResponseChannelSpec] = field(default_factory=list)
    model_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    validation: dict[str, dict[str, Any]] = field(default_factory=dict)
    identification_summary: dict[str, Any] = field(default_factory=dict)
    safety_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_semantics": self.model_semantics,
            "model_type": self.model_type,
            "source_policy_version": self.source_policy_version,
            "source_condition_version": self.source_condition_version,
            "channels": [item.to_dict() for item in self.channels],
            "model_payloads": self.model_payloads,
            "validation": self.validation,
            "identification_summary": self.identification_summary,
            "safety_metadata": self.safety_metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResponseModelArtifact":
        semantics = str(value.get("model_semantics", ""))
        if semantics != RESPONSE_MODEL_SEMANTICS:
            raise ValueError(
                "unsupported predictive response semantics: %r" % semantics
            )
        channels = [
            ResponseChannelSpec(
                channel_id=str(item["channel_id"]),
                output_column=str(item["output_column"]),
                tower_id=(
                    None if item.get("tower_id") is None else str(item["tower_id"])
                ),
                manipulated_flow_columns=tuple(
                    str(v) for v in item.get("manipulated_flow_columns", [])
                ),
                disturbance_columns=tuple(
                    str(v) for v in item.get("disturbance_columns", [])
                ),
                sample_seconds=int(item["sample_seconds"]),
                prediction_steps=int(item["prediction_steps"]),
            )
            for item in value.get("channels", [])
        ]
        return cls(
            model_semantics=semantics,
            model_type=str(value.get("model_type", "UNTRAINED")),
            source_policy_version=value.get("source_policy_version"),
            source_condition_version=value.get("source_condition_version"),
            channels=channels,
            model_payloads=dict(value.get("model_payloads") or {}),
            validation=dict(value.get("validation") or {}),
            identification_summary=dict(value.get("identification_summary") or {}),
            safety_metadata=dict(value.get("safety_metadata") or {}),
        )
