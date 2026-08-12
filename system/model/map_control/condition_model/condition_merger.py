# -*- coding: utf-8 -*-
"""Structural evidence evaluation for automatic rectangular policy regions.

The evaluator never uses action events, confidence, or slurry-flow fields.
Missing liquid-gas or risk evidence is not treated as compatible in automatic
publication mode.
"""

import math
from typing import Dict, Iterable, List, Optional, Set, Tuple

from system.model.map_control.condition_model.condition_config import (
    ConditionModelConfig,
)
from system.model.map_control.condition_model.condition_schema import (
    GridCell,
)


class ConditionMerger:
    def __init__(self, config: ConditionModelConfig):
        self.config = config

    def evaluate_pair(self, first: GridCell, second: GridCell) -> Dict:
        merge = self.config.merge
        evidence = {
            "geometric_adjacent": self._adjacent(first, second),
            "first_coverage_status": first.coverage_status,
            "second_coverage_status": second.coverage_status,
            "first_sample_count": int(first.sample_count),
            "second_sample_count": int(second.sample_count),
            "mode": merge.mode,
        }

        if not merge.enabled or merge.mode == "disabled":
            return self._decision("MERGE_DISABLED", evidence, False)
        if not evidence["geometric_adjacent"]:
            return self._decision("NOT_ADJACENT", evidence, False)
        if "EMPTY" in {first.coverage_status, second.coverage_status}:
            return self._decision("INSUFFICIENT_DATA", evidence, False)

        required_samples = merge.auto_publication_sample_threshold
        evidence["required_auto_samples"] = required_samples
        if min(first.sample_count, second.sample_count) < required_samples:
            return self._decision("INSUFFICIENT_AUTO_SAMPLES", evidence, False)

        common_states = self._qualified_common_states(first, second)
        evidence["qualified_common_states"] = common_states
        evidence["required_common_state_samples"] = merge.min_common_state_samples
        if not common_states:
            return self._decision("INSUFFICIENT_COMMON_STATE", evidence, False)

        first_lg = self._liquid_gas_center(first)
        second_lg = self._liquid_gas_center(second)
        first_lg_count = self._numeric_count(first, "liquid_gas")
        second_lg_count = self._numeric_count(second, "liquid_gas")
        evidence.update({
            "first_mean_liquid_gas": first_lg,
            "second_mean_liquid_gas": second_lg,
            "first_liquid_gas_count": first_lg_count,
            "second_liquid_gas_count": second_lg_count,
            "minimum_metric_coverage_ratio": merge.min_metric_coverage_ratio,
        })

        first_lg_coverage = first_lg_count / max(first.sample_count, 1)
        second_lg_coverage = second_lg_count / max(second.sample_count, 1)
        evidence["first_liquid_gas_coverage_ratio"] = first_lg_coverage
        evidence["second_liquid_gas_coverage_ratio"] = second_lg_coverage
        if (
            first_lg is None
            or second_lg is None
            or min(first_lg_coverage, second_lg_coverage)
            < merge.min_metric_coverage_ratio
        ):
            return self._decision("INSUFFICIENT_LIQUID_GAS_EVIDENCE", evidence, False)

        lg_difference = self._relative_difference(first_lg, second_lg)
        evidence["liquid_gas_relative_difference"] = lg_difference
        evidence["liquid_gas_threshold"] = merge.max_liquid_gas_relative_difference
        evidence["liquid_gas_compatible"] = (
            lg_difference is not None
            and lg_difference <= merge.max_liquid_gas_relative_difference
        )

        pump_distance = self._distribution_distance(
            first.pump_distribution,
            second.pump_distribution,
        )
        evidence["pump_distribution_distance"] = pump_distance
        evidence["pump_distribution_threshold"] = merge.max_pump_distribution_distance
        evidence["pump_distribution_compatible"] = (
            pump_distance <= merge.max_pump_distribution_distance
        )

        first_risk_count = self._risk_valid_count(first)
        second_risk_count = self._risk_valid_count(second)
        first_risk = self._finite(first.statistics.get("risk_rate"))
        second_risk = self._finite(second.statistics.get("risk_rate"))
        evidence.update({
            "first_risk_valid_count": first_risk_count,
            "second_risk_valid_count": second_risk_count,
            "required_risk_samples": merge.min_risk_samples,
            "first_risk_rate": first_risk,
            "second_risk_rate": second_risk,
        })
        if (
            first_risk is None
            or second_risk is None
            or min(first_risk_count, second_risk_count) < merge.min_risk_samples
        ):
            return self._decision("INSUFFICIENT_RISK_EVIDENCE", evidence, False)

        risk_difference = abs(first_risk - second_risk)
        evidence["risk_rate_difference"] = risk_difference
        evidence["risk_rate_threshold"] = merge.max_risk_rate_difference
        evidence["risk_compatible"] = (
            risk_difference <= merge.max_risk_rate_difference
        )

        if not all(
            evidence[key]
            for key in (
                "liquid_gas_compatible",
                "pump_distribution_compatible",
                "risk_compatible",
            )
        ):
            return self._decision("STRUCTURAL_DIFFERENCE", evidence, False)

        return self._decision("AUTO_PROVISIONAL_ELIGIBLE", evidence, True)

    def evaluate_region_members(
        self,
        cells: Iterable[GridCell],
    ) -> Dict:
        members = list(cells)
        evidence: Dict = {
            "member_grid_ids": sorted(cell.grid_id for cell in members),
            "member_count": len(members),
        }
        if not members:
            return self._decision("EMPTY_REGION", evidence, False)
        if len(members) > self.config.merge.max_auto_region_cells:
            evidence["max_auto_region_cells"] = self.config.merge.max_auto_region_cells
            return self._decision("REGION_TOO_LARGE", evidence, False)
        if not self._is_rectangle(members):
            return self._decision("NON_RECTANGULAR", evidence, False)

        adjacent_pairs = [
            (left, right)
            for left, right in self._all_pairs(members)
            if self._adjacent(left, right)
        ]
        pair_decisions = [self.evaluate_pair(left, right) for left, right in adjacent_pairs]
        evidence["pair_decisions"] = pair_decisions
        if len(members) > 1 and (
            not pair_decisions
            or any(not item["allowed"] for item in pair_decisions)
        ):
            return self._decision("REGION_INCONSISTENT", evidence, False)

        region_difference = self._region_liquid_gas_difference(members)
        evidence["region_liquid_gas_relative_difference"] = region_difference
        evidence["region_liquid_gas_threshold"] = (
            self.config.merge.max_liquid_gas_relative_difference
        )
        if (
            region_difference is None
            or region_difference
            > self.config.merge.max_liquid_gas_relative_difference
        ):
            return self._decision("REGION_LIQUID_GAS_INCONSISTENT", evidence, False)

        return self._decision("AUTO_REGION_ELIGIBLE", evidence, True)

    def generate_candidates(
        self,
        catalog: Dict[str, GridCell],
        adjacency: Dict[str, List[str]],
    ) -> List[Tuple[str, str]]:
        del catalog
        return sorted({
            tuple(sorted((grid_id, neighbor)))
            for grid_id, neighbors in adjacency.items()
            for neighbor in neighbors
        })

    def candidate_sort_key(self, decision: Dict) -> Tuple:
        evidence = decision.get("evidence", {})
        return (
            evidence.get("liquid_gas_relative_difference", math.inf),
            evidence.get("pump_distribution_distance", math.inf),
            evidence.get("risk_rate_difference", math.inf),
            -min(
                int(evidence.get("first_sample_count", 0)),
                int(evidence.get("second_sample_count", 0)),
            ),
        )

    def _qualified_common_states(
        self,
        first: GridCell,
        second: GridCell,
    ) -> List[Dict]:
        result = []
        threshold = self.config.merge.min_common_state_samples
        for key in sorted(set(first.state_profiles) & set(second.state_profiles)):
            first_count = int((first.state_profiles.get(key) or {}).get("sample_count", 0))
            second_count = int((second.state_profiles.get(key) or {}).get("sample_count", 0))
            if min(first_count, second_count) >= threshold:
                result.append({
                    "state_key": key,
                    "first_sample_count": first_count,
                    "second_sample_count": second_count,
                })
        return result

    @staticmethod
    def _finite(value) -> Optional[float]:
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _liquid_gas_center(cls, cell: GridCell) -> Optional[float]:
        value = cell.statistics.get("mean_liquid_gas")
        if value is None:
            value = cell.statistics.get("median_liquid_gas")
        return cls._finite(value)

    @staticmethod
    def _numeric_count(cell: GridCell, name: str) -> int:
        numeric = (cell.accumulators or {}).get("numeric", {})
        try:
            return max(0, int((numeric.get(name) or {}).get("count", 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _risk_valid_count(cell: GridCell) -> int:
        try:
            return max(0, int(((cell.accumulators or {}).get("risk") or {}).get("valid_count", 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _adjacent(first: GridCell, second: GridCell) -> bool:
        return (
            abs(first.load_level - second.load_level)
            + abs(first.inlet_so2_level - second.inlet_so2_level)
            == 1
        )

    @staticmethod
    def _decision(code: str, evidence: Dict, allowed: bool) -> Dict:
        return {"decision": code, "allowed": allowed, "evidence": evidence}

    @staticmethod
    def _relative_difference(a, b) -> Optional[float]:
        if a is None or b is None:
            return None
        return abs(a - b) / max(abs(a), abs(b), 1e-9)

    @staticmethod
    def _distribution_distance(first: Dict[str, int], second: Dict[str, int]) -> float:
        first_total = sum(first.values())
        second_total = sum(second.values())
        if not first_total or not second_total:
            return 1.0
        keys = set(first) | set(second)
        return 0.5 * sum(
            abs(
                first.get(key, 0) / first_total
                - second.get(key, 0) / second_total
            )
            for key in keys
        )

    @staticmethod
    def _is_rectangle(cells: Iterable[GridCell]) -> bool:
        coordinates: Set[Tuple[int, int]] = {
            (cell.load_level, cell.inlet_so2_level)
            for cell in cells
        }
        p_values = {item[0] for item in coordinates}
        s_values = {item[1] for item in coordinates}
        return len(coordinates) == len(p_values) * len(s_values)

    @staticmethod
    def _all_pairs(cells: List[GridCell]):
        for index, first in enumerate(cells):
            for second in cells[index + 1 :]:
                yield first, second

    def _region_liquid_gas_difference(
        self,
        cells: List[GridCell],
    ) -> Optional[float]:
        values = [self._liquid_gas_center(cell) for cell in cells]
        if any(value is None for value in values):
            return None
        finite_values = [float(value) for value in values]
        return (
            max(finite_values) - min(finite_values)
        ) / max(max(map(abs, finite_values)), 1e-9)
