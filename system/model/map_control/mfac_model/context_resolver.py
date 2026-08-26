# -*- coding: utf-8 -*-
"""Resolve first-module condition output to a Scheme 2 MFAC context.

V1 follows the final ``condition_label`` by default.  A compatibility gate may
later publish per-base-condition overrides when a condition merge is valid for
scheme 1 but its MFAC sensitivities should remain separate.
"""

from typing import Any, Dict, Mapping, Optional

from .mfac_schema import MFACContextResolution


class MFACContextResolver:
    """Version-bound resolver from condition output to ``mfac_context_id``.

    Resolution order:

    1. explicit ``base_condition_overrides``;
    2. explicit ``condition_contexts``;
    3. deterministic context derived from final ``condition_label``.

    This means scheme 2 consumes scheme 1's final condition by default while
    retaining a non-invasive escape hatch for MFAC-incompatible merges.
    """

    def __init__(
        self,
        condition_snapshot_version: str,
        *,
        condition_contexts: Optional[Mapping[str, str]] = None,
        base_condition_overrides: Optional[Mapping[str, str]] = None,
    ) -> None:
        version = str(condition_snapshot_version or "").strip()
        if not version:
            raise ValueError("condition_snapshot_version is required")
        self.condition_snapshot_version = version
        self.condition_contexts = {
            str(key): str(value)
            for key, value in (condition_contexts or {}).items()
        }
        self.base_condition_overrides = {
            str(key): str(value)
            for key, value in (base_condition_overrides or {}).items()
        }

    @staticmethod
    def default_context_id(condition_label: str) -> str:
        label = str(condition_label or "").strip()
        if not label:
            raise ValueError("condition_label is required for default MFAC context")
        return f"MFAC-COND-{label}"

    def resolve(self, condition_output: Mapping[str, Any]) -> MFACContextResolution:
        version = str(condition_output.get("condition_snapshot_version", "")).strip()
        if version != self.condition_snapshot_version:
            raise ValueError(
                "condition snapshot version mismatch: "
                f"resolver={self.condition_snapshot_version}, row={version or '<empty>'}"
            )

        condition_label = str(condition_output.get("condition_label", "")).strip()
        base_condition_id = str(condition_output.get("base_condition_id", "")).strip()
        grid_id = str(condition_output.get("grid_id", "")).strip()
        policy_region_id = str(condition_output.get("policy_region_id", "")).strip()

        if base_condition_id and base_condition_id in self.base_condition_overrides:
            context_id = self.base_condition_overrides[base_condition_id]
            source = "BASE_CONDITION_OVERRIDE"
        elif condition_label and condition_label in self.condition_contexts:
            context_id = self.condition_contexts[condition_label]
            source = "CONDITION_MAPPING"
        elif condition_label:
            context_id = self.default_context_id(condition_label)
            source = "CONDITION_DEFAULT"
        elif base_condition_id:
            # Defensive fallback for incomplete historical rows.  Normal online
            # results are expected to contain a stable condition label.
            context_id = f"MFAC-BASE-{base_condition_id}"
            source = "BASE_CONDITION_FALLBACK"
        else:
            raise ValueError(
                "condition output must contain condition_label or base_condition_id"
            )

        return MFACContextResolution(
            condition_snapshot_version=version,
            condition_label=condition_label,
            base_condition_id=base_condition_id,
            grid_id=grid_id,
            policy_region_id=policy_region_id,
            mfac_context_id=context_id,
            resolution_source=source,
        )

    def to_artifact(self) -> Dict[str, Any]:
        return {
            "condition_snapshot_version": self.condition_snapshot_version,
            "condition_contexts": dict(self.condition_contexts),
            "base_condition_overrides": dict(self.base_condition_overrides),
        }

    @classmethod
    def from_artifact(cls, value: Mapping[str, Any]) -> "MFACContextResolver":
        data = dict(value)
        return cls(
            condition_snapshot_version=str(data.get("condition_snapshot_version", "")),
            condition_contexts=data.get("condition_contexts") or {},
            base_condition_overrides=data.get("base_condition_overrides") or {},
        )
