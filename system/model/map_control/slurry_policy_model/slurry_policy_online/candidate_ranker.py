from __future__ import annotations

from typing import List, Optional

from .action_utils import profile_action
from .demand_analyzer import MAGNITUDE_ORDER
from .types import Candidate, ControlDemand


class CandidateRanker:
    """按“当前目标需要”而不是固定离线总分选择动作。

    离线模型只描述每种历史动作通常造成多大的 SO2 变化以及其安全/稳定性；
    在线使用当前 ``current_so2 - effective_target`` 与历史 ``delta_outlet_so2``
    中位数计算动作后的预计剩余偏差。
    """

    @staticmethod
    def _target_metrics(candidate: Candidate, demand: ControlDemand) -> tuple[float, float, float, float | None]:
        profile = candidate.profile
        distribution = profile.get("so2_effect", {}).get("delta_distribution", {}) or {}
        raw_delta = distribution.get("median")
        try:
            historical_delta = float(raw_delta)
        except (TypeError, ValueError):
            historical_delta = None

        current_error = float(demand.error)
        if historical_delta is None:
            predicted_so2 = float(demand.current_so2)
            residual_error = current_error
            target_match_score = 0.0
        else:
            predicted_so2 = float(demand.current_so2) + historical_delta
            residual_error = predicted_so2 - float(demand.effective_target)
            denominator = max(abs(current_error), 1e-6)
            target_match_score = 1.0 - min(abs(residual_error) / denominator, 1.0)

        candidate.evaluation.update(
            {
                "historical_so2_delta_median": historical_delta,
                "predicted_so2_after": predicted_so2,
                "predicted_remaining_error": residual_error,
                "target_match_score": target_match_score,
            }
        )
        return target_match_score, residual_error, predicted_so2, historical_delta

    def rank(self, candidates: List[Candidate], demand: ControlDemand) -> Optional[Candidate]:
        if not candidates:
            return None
        effect_priority = {
            direction: len(demand.acceptable_effect_directions) - index
            for index, direction in enumerate(demand.acceptable_effect_directions)
        }
        for candidate in candidates:
            profile = candidate.profile
            action = profile_action(profile)
            effect = str(profile.get("so2_effect", {}).get("dominant_direction", "UNKNOWN"))
            reliability = profile.get("reliability", {})
            support = profile.get("support", {})
            magnitude = str(action.get("magnitude", "UNKNOWN")).upper()
            target_match_score, residual_error, _predicted_so2, _historical_delta = self._target_metrics(
                candidate, demand
            )

            # 目标匹配是同一经验层级内的首要排序项：能安全、稳定地把当前偏差
            # 消除得更接近 0 的动作优先。历史安全、稳定、可靠性随后用于打破接近的
            # 目标匹配结果；最后才偏好更小动作。
            candidate.rank_key = (
                effect_priority.get(effect, 0),
                float(target_match_score),
                -abs(float(residual_error)),
                float(reliability.get("safety_history_score", 0.0)),
                float(profile.get("stability", {}).get("stable_response_ratio", 0.0)),
                float(reliability.get("total_score", 0.0)),
                float(profile.get("so2_effect", {}).get("direction_consistency", 0.0)),
                float(support.get("effective_weighted_event_count", 0.0)),
                -MAGNITUDE_ORDER.get(magnitude, 99),
                str(candidate.action_id),
            )
        return max(candidates, key=lambda item: item.rank_key)
