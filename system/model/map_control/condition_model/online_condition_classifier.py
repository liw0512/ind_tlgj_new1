# -*- coding: utf-8 -*-
"""Read-only V3 online condition classifier with majority stabilization.

The classifier keeps the fixed-grid and published-region semantics of the
first module.  Each realtime sample is first mapped to a raw ``grid_id`` and
raw ``condition_label``.  The formal online ``condition_label`` is then the
mode of the most recent configured number of raw labels (default: 6).

No action-event, confidence, online merge, online grid update, hysteresis, or
consecutive-hit dwell logic is used here.
"""

import argparse
import sys
import threading
from collections import Counter, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from system.model.map_control.condition_model.condition_config import (
    ONLINE_CONDITION_CLASSIFY_CONFIG,
    ConditionModelConfig,
    from_dict,
)
from system.model.map_control.condition_model.condition_schema import (
    ConditionSnapshot,
    OnlineConditionResult,
)
from system.model.map_control.condition_model.grid_definition import locate_grid
from system.model.map_control.condition_model.initial_condition_builder import (
    build_state_key,
    condition_label_from_snapshot,
    get_condition_axis_values,
)
from system.model.map_control.condition_model.snapshot_io import (
    read_latest_available_snapshot,
)
from system.model.map_control.condition_model.online_condition_policy_bridge import (
    SlurryPolicyOnlineBridge,
    csv_safe_row,
)
from system.model.map_control.condition_model.integrated_version_manager import (
    IntegratedVersionError,
    IntegratedVersionManager,
    IntegratedVersionPointer,
)
from system.model.map_control.fast_change_mode import FastChangeHistoryManager


class OnlineConditionClassifier:
    """Classify realtime samples and stabilize the published condition label.

    Stability policy:
    1. append each valid raw ``condition_label`` to a sliding window;
    2. choose the label with the largest count;
    3. if several labels tie, keep the last stable label when it is among the
       tied labels; otherwise choose the tied label occurring most recently;
    4. the result becomes formally stable only after the window is full.

    During warm-up, the majority of the currently available samples is still
    returned for observability, while ``condition_stable`` remains ``False``.
    Downstream automatic control must check both ``condition_valid`` and
    ``condition_stable`` before issuing an ordinary economic action.
    """

    def __init__(
        self,
        config: ConditionModelConfig,
        snapshot: ConditionSnapshot,
    ):
        self.config = config
        self.snapshot = snapshot
        self._window_size = int(config.online.stability_window_size)
        self._label_window: Deque[str] = deque(maxlen=self._window_size)
        self._grid_window: Deque[str] = deque(maxlen=self._window_size)
        self._last_stable_condition_label: Optional[str] = None
        self._last_stable_grid_id: Optional[str] = None

    def reset_stability(self) -> None:
        """Clear online window state without modifying the loaded snapshot."""

        self._label_window.clear()
        self._grid_window.clear()
        self._last_stable_condition_label = None
        self._last_stable_grid_id = None

    def blocked_result(
        self,
        realtime: Dict[str, Any],
        reason: str,
    ) -> OnlineConditionResult:
        """Return a safe invalid result without advancing the majority window."""
        return self._invalid_result(
            build_state_key(realtime),
            str(reason),
        )

    def classify(self, realtime: Dict[str, Any]) -> OnlineConditionResult:
        state_key = build_state_key(realtime)
        try:
            load_value, inlet_so2 = get_condition_axis_values(
                realtime,
                self.config,
            )
            raw_grid_id, clipped, clip_axis = locate_grid(
                load_value,
                inlet_so2,
                self.config,
            )
            raw_condition_label = condition_label_from_snapshot(
                raw_grid_id,
                self.snapshot,
                self.config,
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            return self._invalid_result(state_key, str(exc))

        (
            stable_condition_label,
            stable_grid_id,
            switch_state,
            condition_stable,
            majority_count,
            majority_tied,
        ) = self._update_majority(raw_grid_id, raw_condition_label)

        if not stable_grid_id or stable_grid_id not in self.snapshot.grid_catalog:
            return self._invalid_result(
                state_key,
                "stable grid is unavailable in the loaded snapshot",
                raw_grid_id=raw_grid_id,
                raw_condition_label=raw_condition_label,
                clipped=clipped,
                clip_axis=clip_axis,
            )

        cell = self.snapshot.grid_catalog[stable_grid_id]
        region = self.snapshot.policy_regions.get(cell.policy_region_id)
        source = self._resolve_source(cell, region, state_key)
        region_status = region.status if region else "INDEPENDENT"
        economic_allowed = (
            condition_stable
            and not clipped
            and cell.coverage_status == "MATURE"
            and (
                source == "LOCAL_GRID"
                or (
                    source == "MERGED_REGION"
                    and region_status == "AUTO_CONFIRMED_MERGE"
                )
            )
        )

        reason = None
        if not condition_stable:
            reason = (
                "MAJORITY_WINDOW_WARMUP_"
                f"{len(self._label_window)}_OF_{self._window_size}"
            )

        return OnlineConditionResult(
            # Compatibility fields consumed by downstream online policy code.
            grid_id=stable_grid_id,
            condition_label=stable_condition_label,
            policy_region_id=cell.policy_region_id,
            state_key=state_key,
            coverage_status=cell.coverage_status,
            experience_source=source,
            # Explicit raw/stable audit fields.
            raw_grid_id=raw_grid_id,
            raw_condition_label=raw_condition_label,
            stable_grid_id=stable_grid_id,
            stable_condition_label=stable_condition_label,
            condition_valid=True,
            condition_stable=condition_stable,
            out_of_range_clipped=clipped,
            clip_axis=clip_axis,
            condition_switch_state=switch_state,
            stability_mode="MAJORITY",
            stability_window_size=self._window_size,
            stability_sample_count=len(self._label_window),
            majority_count=majority_count,
            majority_tied=majority_tied,
            economic_exploration_allowed=economic_allowed,
            reason=reason,
        )

    def _update_majority(
        self,
        raw_grid_id: str,
        raw_condition_label: str,
    ) -> Tuple[str, str, str, bool, int, bool]:
        self._grid_window.append(str(raw_grid_id))
        self._label_window.append(str(raw_condition_label))

        counts = Counter(self._label_window)
        maximum = max(counts.values())
        tied_labels = {
            label
            for label, count in counts.items()
            if count == maximum
        }
        majority_tied = len(tied_labels) > 1
        selected_label = self._select_tied_label(tied_labels)
        selected_grid = self._latest_grid_for_label(selected_label)

        window_full = len(self._label_window) >= self._window_size
        if not window_full:
            switch_state = "INITIALIZING"
            condition_stable = False
        elif self._last_stable_condition_label is None:
            switch_state = "INITIALIZED"
            condition_stable = True
            self._last_stable_condition_label = selected_label
            self._last_stable_grid_id = selected_grid
        elif selected_label != self._last_stable_condition_label:
            switch_state = "SWITCHED"
            condition_stable = True
            self._last_stable_condition_label = selected_label
            self._last_stable_grid_id = selected_grid
        else:
            switch_state = "STABLE"
            condition_stable = True
            self._last_stable_grid_id = selected_grid

        return (
            selected_label,
            selected_grid,
            switch_state,
            condition_stable,
            int(maximum),
            majority_tied,
        )

    def _select_tied_label(self, tied_labels: set) -> str:
        if (
            self._last_stable_condition_label is not None
            and self._last_stable_condition_label in tied_labels
        ):
            return self._last_stable_condition_label

        # No established stable label can break the tie.  Use the most recent
        # occurrence among the tied labels so the result is deterministic and
        # responsive without adding another threshold.
        for label in reversed(self._label_window):
            if label in tied_labels:
                return label
        raise RuntimeError("majority label window is unexpectedly empty")

    def _latest_grid_for_label(self, condition_label: str) -> str:
        pairs: List[Tuple[str, str]] = list(
            zip(self._grid_window, self._label_window)
        )
        for grid_id, label in reversed(pairs):
            if label == condition_label:
                return grid_id
        if self._last_stable_grid_id:
            return self._last_stable_grid_id
        raise RuntimeError(
            f"no grid found for selected condition label: {condition_label}"
        )

    def _invalid_result(
        self,
        state_key: str,
        reason: str,
        *,
        raw_grid_id: Optional[str] = None,
        raw_condition_label: Optional[str] = None,
        clipped: bool = False,
        clip_axis: str = "none",
    ) -> OnlineConditionResult:
        stable_grid_id = self._last_stable_grid_id
        stable_label = self._last_stable_condition_label
        policy_region_id: Optional[str] = None
        coverage_status = "INVALID"

        if stable_grid_id and stable_grid_id in self.snapshot.grid_catalog:
            cell = self.snapshot.grid_catalog[stable_grid_id]
            policy_region_id = cell.policy_region_id
            coverage_status = cell.coverage_status

        return OnlineConditionResult(
            grid_id=stable_grid_id,
            condition_label=stable_label,
            policy_region_id=policy_region_id,
            state_key=state_key,
            coverage_status=coverage_status,
            experience_source="BASELINE_ONLY",
            raw_grid_id=raw_grid_id,
            raw_condition_label=raw_condition_label,
            stable_grid_id=stable_grid_id,
            stable_condition_label=stable_label,
            condition_valid=False,
            condition_stable=False,
            out_of_range_clipped=clipped,
            clip_axis=clip_axis,
            condition_switch_state="INVALID",
            stability_mode="MAJORITY",
            stability_window_size=self._window_size,
            stability_sample_count=len(self._label_window),
            majority_count=0,
            majority_tied=False,
            economic_exploration_allowed=False,
            reason=reason,
        )

    def _resolve_source(self, cell, region, state_key: str) -> str:
        """Resolve first-module statistical experience for the stable grid."""

        profile = cell.state_profiles.get(state_key)
        if (
            profile
            and int(profile.get("sample_count", 0))
            >= self.config.merge.min_observed_samples
        ):
            return "LOCAL_GRID"

        if region and len(region.member_grid_ids) > 1:
            if region.status == "AUTO_CONFIRMED_MERGE":
                return "MERGED_REGION"
            if (
                region.status == "AUTO_PROVISIONAL_MERGE"
                and self.config.online.allow_provisional_region_fallback
            ):
                return "MERGED_REGION"

        if cell.sample_count:
            return "PLANT_GLOBAL"
        return "BASELINE_ONLY"



def _base_condition_id(
    grid_id: Optional[str],
    snapshot: ConditionSnapshot,
    config: ConditionModelConfig,
) -> str:
    if not grid_id:
        return ""
    cell = snapshot.grid_catalog.get(grid_id)
    if cell is None:
        return ""
    return str(
        (cell.axis_1_level - 1) * config.axis_2.cell_count
        + cell.axis_2_level
    )


def _preserving_update(
    base: Dict[str, Any],
    updates: Dict[str, Any],
    *,
    collision_prefix: str = "input_original__",
) -> Dict[str, Any]:
    """Append generated fields while preserving colliding input values.

    Normal raw realtime input does not contain condition output fields.  This
    collision handling exists for replay files that may already contain an old
    ``condition_label`` or policy column.  No original value is silently lost.
    """

    output = dict(base)
    for key, value in updates.items():
        if key in output:
            preserved_key = "%s%s" % (collision_prefix, key)
            suffix = 2
            while preserved_key in output:
                preserved_key = "%s%s__%d" % (
                    collision_prefix,
                    key,
                    suffix,
                )
                suffix += 1
            output[preserved_key] = output[key]
        output[key] = value
    return output


def condition_result_fields(
    result: OnlineConditionResult,
    snapshot: ConditionSnapshot,
    config: ConditionModelConfig,
) -> Dict[str, Any]:
    """Build the complete first-module online interface for one sample."""

    stable_cell = (
        snapshot.grid_catalog.get(result.stable_grid_id)
        if result.stable_grid_id
        else None
    )
    stable_region = (
        snapshot.policy_regions.get(stable_cell.policy_region_id)
        if stable_cell is not None
        else None
    )
    region_status = (
        stable_region.status
        if stable_region is not None and stable_region.status
        else ("INDEPENDENT" if stable_cell is not None else "INVALID")
    )
    region_member_count = (
        len(stable_region.member_grid_ids)
        if stable_region is not None
        else (1 if stable_cell is not None else 0)
    )

    return {
        "condition_snapshot_version": snapshot.snapshot_version,
        # Current single-sample identity.
        "raw_grid_id": result.raw_grid_id or "",
        "raw_base_condition_id": _base_condition_id(
            result.raw_grid_id,
            snapshot,
            config,
        ),
        "raw_condition_label": result.raw_condition_label or "",
        # Majority-stabilized identity.
        "stable_grid_id": result.stable_grid_id or "",
        "stable_base_condition_id": _base_condition_id(
            result.stable_grid_id,
            snapshot,
            config,
        ),
        "stable_condition_label": result.stable_condition_label or "",
        # Compatibility interface consumed by the second module.
        "grid_id": result.grid_id or "",
        "base_condition_id": _base_condition_id(
            result.grid_id,
            snapshot,
            config,
        ),
        "condition_label": result.condition_label or "",
        "policy_region_id": result.policy_region_id or "",
        "region_status": region_status,
        "region_member_count": region_member_count,
        "coverage_status": result.coverage_status,
        "state_key": result.state_key,
        "condition_experience_source": result.experience_source,
        "condition_valid": result.condition_valid,
        "condition_stable": result.condition_stable,
        "out_of_range_clipped": result.out_of_range_clipped,
        "clip_axis": result.clip_axis,
        "condition_switch_state": result.condition_switch_state,
        "stability_mode": result.stability_mode,
        "stability_window_size": result.stability_window_size,
        "stability_sample_count": result.stability_sample_count,
        "majority_count": result.majority_count,
        "majority_tied": result.majority_tied,
        "economic_exploration_allowed": (
            result.economic_exploration_allowed
        ),
        "condition_reason": result.reason or "OK",
    }


def enrich_condition_row(
    realtime: Dict[str, Any],
    result: OnlineConditionResult,
    snapshot: ConditionSnapshot,
    config: ConditionModelConfig,
) -> Dict[str, Any]:
    """Return original input plus every first-module online output field."""

    return _preserving_update(
        dict(realtime),
        condition_result_fields(result, snapshot, config),
    )


class OnlineConditionPolicyPipeline:
    """第一模块在线识别 + 第二模块在线策略的同版本集成入口。

    正式运行时，第一模块和第二模块共同读取 ``active_version.json``。
    新版本切换采用两阶段流程：

    1. 在临时对象中加载第一模块候选快照；
    2. 在临时对象中加载并验证第二模块候选策略；
    3. 检查 condition/policy/source-condition 版本完全一致；
    4. 在本对象锁内一次性替换两个正式对象。

    任一候选失败时继续使用旧版本对。版本切换后第一模块 6 点窗口清空，
    但第二模块的 commanded/effective target、WAITING_EFFECT、反向锁、FAST
    状态和实际动作历史通过 runtime state 复制到候选策略，不会因模型升级丢失。
    """

    def __init__(
        self,
        config: ConditionModelConfig,
        snapshot: ConditionSnapshot,
        *,
        integration_config: Optional[Dict[str, Any]] = None,
        slurry_policy: Optional[Any] = None,
        policy_factory: Optional[Any] = None,
        version_manager: Optional[IntegratedVersionManager] = None,
        active_pointer: Optional[IntegratedVersionPointer] = None,
    ) -> None:
        self.lock = threading.RLock()
        self.config = config
        self.snapshot = snapshot
        self.classifier = OnlineConditionClassifier(config, snapshot)
        # FAST detector is independent from condition/policy model hot reload and therefore
        # keeps its short-window/runtime state across integrated model version switches.
        self.fast_change_manager = FastChangeHistoryManager(persist_runtime=True)
        self.version_manager = version_manager

        bridge_config = dict(
            integration_config
            if integration_config is not None
            else ONLINE_CONDITION_CLASSIFY_CONFIG.get(
                "slurry_policy_online",
                {},
            )
        )
        if self.version_manager is not None:
            bridge_config["external_version_management"] = True

        self.policy_bridge = SlurryPolicyOnlineBridge(
            bridge_config,
            policy_instance=slurry_policy,
            policy_factory=policy_factory,
            initial_active_pointer=(
                dict(active_pointer.raw)
                if active_pointer is not None
                else None
            ),
        )

        if active_pointer is not None and self.version_manager is not None:
            if self.policy_bridge.enabled:
                self._validate_loaded_pair(active_pointer)
            self.version_manager.commit(active_pointer, startup=True)

    def _policy_versions(self) -> Dict[str, Optional[str]]:
        return self.policy_bridge.loaded_versions()

    def _validate_loaded_pair(
        self,
        pointer: IntegratedVersionPointer,
        *,
        policy: Optional[Any] = None,
    ) -> None:
        if policy is None:
            versions = self._policy_versions()
        else:
            status = dict(policy.status())
            versions = {
                "policy_version": status.get("model_version"),
                "condition_version": status.get(
                    "condition_snapshot_version"
                ),
            }
        observed = {
            str(self.snapshot.snapshot_version),
            str(pointer.integrated_version),
            str(pointer.condition_version),
            str(pointer.policy_version),
            str(pointer.policy_source_condition_version),
            str(versions.get("policy_version") or ""),
            str(versions.get("condition_version") or ""),
        }
        observed.discard("")
        if observed != {pointer.integrated_version}:
            raise IntegratedVersionError(
                "第一/第二模块候选版本未形成完整同版本对: %s"
                % sorted(observed)
            )

    def _maybe_reload_integrated_pair(self) -> None:
        if self.version_manager is None or not self.policy_bridge.enabled:
            return
        pointer = self.version_manager.poll()
        if pointer is None:
            return

        try:
            # 先复制现场运行事实。即使在线日志关闭，也能原样传入候选策略。
            runtime_state = self.policy_bridge.export_runtime_state()
            candidate_snapshot, candidate_config = (
                self.version_manager.prepare_condition(pointer)
            )
            candidate_policy = self.policy_bridge.create_candidate(
                pointer.raw,
                initial_runtime_state=runtime_state,
            )

            # 校验时暂时使用候选第一模块快照版本，不修改正式对象。
            candidate_status = dict(candidate_policy.status())
            observed = {
                str(candidate_snapshot.snapshot_version),
                str(pointer.integrated_version),
                str(pointer.condition_version),
                str(pointer.policy_version),
                str(pointer.policy_source_condition_version),
                str(candidate_status.get("model_version") or ""),
                str(
                    candidate_status.get("condition_snapshot_version")
                    or ""
                ),
            }
            observed.discard("")
            if observed != {pointer.integrated_version}:
                raise IntegratedVersionError(
                    "候选同版本对校验失败: %s" % sorted(observed)
                )

            candidate_classifier = OnlineConditionClassifier(
                candidate_config,
                candidate_snapshot,
            )

            # 本方法在 pipeline.lock 内执行。外部线程只能看到完整旧版本对或
            # 完整新版本对，不会看到只替换其中一个模块的中间状态。
            self.config = candidate_config
            self.snapshot = candidate_snapshot
            self.classifier = candidate_classifier
            self.policy_bridge.replace_policy(
                candidate_policy,
                mark_reloaded=True,
            )
            self.version_manager.commit(pointer, startup=False)
        except Exception as exc:
            self.version_manager.reject(pointer, exc)
            if not self.version_manager.keep_current_on_failure:
                raise

    def _version_fields(self) -> Dict[str, Any]:
        versions = self._policy_versions()
        if self.version_manager is None:
            condition_version = str(self.snapshot.snapshot_version)
            policy_version = str(versions.get("policy_version") or "")
            return {
                "integrated_active_version": (
                    condition_version
                    if condition_version == policy_version
                    else ""
                ),
                "condition_loaded_version": condition_version,
                "slurry_policy_loaded_version": policy_version,
                "version_consistent": bool(
                    condition_version
                    and condition_version == policy_version
                ),
                "version_switch_state": "STATIC",
                "version_switch_time": "",
                "version_switch_error": "",
                "active_version_file": "",
            }
        return self.version_manager.status_fields(
            condition_loaded_version=self.snapshot.snapshot_version,
            slurry_policy_loaded_version=versions.get("policy_version"),
        )

    def reset_condition_stability(self) -> None:
        with self.lock:
            self.classifier.reset_stability()

    def process(
        self,
        realtime: Dict[str, Any],
        *,
        target: Optional[Any] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            self._maybe_reload_integrated_pair()
            original = dict(realtime)
            # FAST is owned by the first-module online pipeline and must run before
            # condition majority stabilization.  Calibration/invalid guard frames are
            # frozen here: neither FAST short-window state nor the condition majority
            # window is allowed to advance.  P4PC does not own or call FAST directly.
            guard_reason = self.fast_change_manager.input_guard_reason(original)
            if guard_reason is not None:
                fast_context = self.fast_change_manager.blocked_online_context(
                    original,
                    target=target,
                    reason=guard_reason,
                )
                original = _preserving_update(original, fast_context)
                condition_result = self.classifier.blocked_result(
                    original,
                    "UPSTREAM_INPUT_GUARD_BLOCKED:%s" % guard_reason,
                )
            else:
                fast_context = self.fast_change_manager.evaluate_online(
                    original, target=target
                )
                original = _preserving_update(original, fast_context)
                condition_result = self.classifier.classify(original)
            if (
                self.version_manager is not None
                and condition_result.condition_stable
            ):
                self.version_manager.mark_condition_window_stable()

            first_module_output = enrich_condition_row(
                original,
                condition_result,
                self.snapshot,
                self.config,
            )
            first_module_output = _preserving_update(
                first_module_output,
                self._version_fields(),
            )
            return self.policy_bridge.process(
                first_module_output,
                target=target,
                execution_context=execution_context,
            )

    def process_condition_only(
        self,
        realtime: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self.lock:
            original = dict(realtime)
            result = self.classifier.classify(original)
            output = enrich_condition_row(
                original,
                result,
                self.snapshot,
                self.config,
            )
            return _preserving_update(output, self._version_fields())

    def record_execution(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        """将 MainControl 的真实执行反馈传给当前正式第二模块。"""

        with self.lock:
            return self.policy_bridge.record_execution(feedback)

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "condition_snapshot_version": self.snapshot.snapshot_version,
                "condition_window_size": (
                    self.config.online.stability_window_size
                ),
                "integrated_version": self._version_fields(),
                "slurry_policy": self.policy_bridge.status(),
                "fast_change": self.fast_change_manager.status(),
            }


def build_online_condition_policy_pipeline(
    snapshot_path: Optional[str] = None,
    *,
    integration_config: Optional[Dict[str, Any]] = None,
    slurry_policy: Optional[Any] = None,
    policy_factory: Optional[Any] = None,
) -> OnlineConditionPolicyPipeline:
    """创建需要长期复用的第一模块 -> 第二模块集成在线对象。

    默认 ``snapshot_path='active'``：使用统一 active_version.json 启动并热更新。
    显式传入具体 condition_snapshot.json 时进入静态单版本模式，主要用于测试。
    """

    bridge_config = dict(
        integration_config
        if integration_config is not None
        else ONLINE_CONDITION_CLASSIFY_CONFIG.get(
            "slurry_policy_online",
            {},
        )
    )
    selected_path = (
        snapshot_path
        or ONLINE_CONDITION_CLASSIFY_CONFIG.get("snapshot_path", "active")
    )
    selected_text = str(selected_path).strip()
    integrated_cfg = dict(bridge_config.get("integrated_version") or {})
    use_integrated = bool(integrated_cfg.get("enabled", True)) and (
        selected_text.lower() == "active"
    )

    if use_integrated:
        manager = IntegratedVersionManager(integrated_cfg)
        pointer = manager.startup_pointer()
        snapshot, config = manager.prepare_condition(pointer)
        return OnlineConditionPolicyPipeline(
            config,
            snapshot,
            integration_config=bridge_config,
            slurry_policy=slurry_policy,
            policy_factory=policy_factory,
            version_manager=manager,
            active_pointer=pointer,
        )

    snapshot, _ = read_latest_available_snapshot(selected_text)
    config = from_dict(snapshot.grid_config)
    return OnlineConditionPolicyPipeline(
        config,
        snapshot,
        integration_config=bridge_config,
        slurry_policy=slurry_policy,
        policy_factory=policy_factory,
    )


def online_condition_csv(
    snapshot_path: str,
    input_csv_path: str,
    output_csv_path: str,
    merge_statistics_json_path: str = "",
    encoding: str = "utf-8-sig",
    *,
    invoke_slurry_policy: bool = True,
    target: Optional[float] = None,
    target_column: Optional[str] = None,
    slurry_policy_config_spec: Optional[str] = None,
) -> str:
    """Sequentially replay CSV through module 1 and optionally module 2.

    Final CSV column groups are:
    1. every original input column;
    2. every first-module online condition field;
    3. every second-module decision field with ``slurry_policy_`` prefix.

    Rows remain in the file's existing order because both majority condition
    stabilization and online policy state are sequential.
    """

    del merge_statistics_json_path

    integration_config = dict(
        ONLINE_CONDITION_CLASSIFY_CONFIG.get("slurry_policy_online", {})
    )
    integration_config["enabled"] = bool(invoke_slurry_policy)
    if target_column is not None:
        integration_config["target_column"] = target_column
    if slurry_policy_config_spec is not None:
        integration_config["config_spec"] = slurry_policy_config_spec

    pipeline = build_online_condition_policy_pipeline(
        snapshot_path=snapshot_path,
        integration_config=integration_config,
    )
    resolved_snapshot_path = (
        str(pipeline.version_manager.active_file)
        if pipeline.version_manager is not None
        else str(snapshot_path)
    )
    frame = pd.read_csv(input_csv_path, encoding=encoding)

    rows: List[Dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        if invoke_slurry_policy:
            enriched = pipeline.process(row, target=target)
        else:
            enriched = pipeline.process_condition_only(row)
        rows.append(csv_safe_row(enriched))

    target_path = Path(output_csv_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(
        target_path,
        index=False,
        encoding="utf-8-sig",
    )
    print(
        "在线工况与供浆策略处理完成: "
        f"input={input_csv_path}, output={target_path}, "
        f"snapshot={resolved_snapshot_path}, "
        f"version={pipeline.snapshot.snapshot_version}, rows={len(rows)}, "
        f"stability=MAJORITY/{pipeline.config.online.stability_window_size}, "
        f"slurry_policy={'ENABLED' if invoke_slurry_policy else 'DISABLED'}"
    )
    return str(target_path)


def run_configured_online_classify() -> str:
    integration = dict(
        ONLINE_CONDITION_CLASSIFY_CONFIG.get("slurry_policy_online", {})
    )
    return online_condition_csv(
        snapshot_path=ONLINE_CONDITION_CLASSIFY_CONFIG["snapshot_path"],
        input_csv_path=ONLINE_CONDITION_CLASSIFY_CONFIG["input_csv_path"],
        output_csv_path=ONLINE_CONDITION_CLASSIFY_CONFIG["output_csv_path"],
        merge_statistics_json_path=ONLINE_CONDITION_CLASSIFY_CONFIG.get(
            "merge_statistics_json_path",
            "",
        ),
        encoding=ONLINE_CONDITION_CLASSIFY_CONFIG.get(
            "encoding",
            "utf-8-sig",
        ),
        invoke_slurry_policy=bool(integration.get("enabled", True)),
        target_column=integration.get("target_column"),
        slurry_policy_config_spec=integration.get("config_spec"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run first-module majority condition classification and pass the "
            "complete enriched row into the second-module online policy"
        )
    )
    parser.add_argument(
        "--snapshot",
        default=None,
        help="第一模块快照 JSON；不填则读取 condition_config.py",
    )
    parser.add_argument(
        "--merge-statistics",
        default=None,
        help="兼容参数，在线正式标签不再读取该文件",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="输入 CSV；不填则读取 condition_config.py",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="最终输出 CSV；不填则读取 condition_config.py",
    )
    parser.add_argument(
        "--encoding",
        default=None,
        help="输入 CSV 编码；不填则读取 condition_config.py",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=None,
        help="覆盖整份 CSV 的固定净烟气 SO2 控制目标",
    )
    parser.add_argument(
        "--target-column",
        default=None,
        help="从输入 CSV 读取动态 SO2 目标的列名",
    )
    parser.add_argument(
        "--slurry-policy-config",
        default=None,
        help="可选的第二模块统一配置文件路径/模块规格",
    )
    parser.add_argument(
        "--condition-only",
        action="store_true",
        help="仅执行第一模块，不调用第二模块在线策略",
    )
    args = parser.parse_args()

    integration = dict(
        ONLINE_CONDITION_CLASSIFY_CONFIG.get("slurry_policy_online", {})
    )
    output = online_condition_csv(
        snapshot_path=(
            args.snapshot
            or ONLINE_CONDITION_CLASSIFY_CONFIG["snapshot_path"]
        ),
        input_csv_path=(
            args.input
            or ONLINE_CONDITION_CLASSIFY_CONFIG["input_csv_path"]
        ),
        output_csv_path=(
            args.output
            or ONLINE_CONDITION_CLASSIFY_CONFIG["output_csv_path"]
        ),
        merge_statistics_json_path=(
            args.merge_statistics
            or ONLINE_CONDITION_CLASSIFY_CONFIG.get(
                "merge_statistics_json_path",
                "",
            )
        ),
        encoding=(
            args.encoding
            or ONLINE_CONDITION_CLASSIFY_CONFIG.get(
                "encoding",
                "utf-8-sig",
            )
        ),
        invoke_slurry_policy=(
            False
            if args.condition_only
            else bool(integration.get("enabled", True))
        ),
        target=args.target,
        target_column=(
            args.target_column
            if args.target_column is not None
            else integration.get("target_column")
        ),
        slurry_policy_config_spec=(
            args.slurry_policy_config
            if args.slurry_policy_config is not None
            else integration.get("config_spec")
        ),
    )
    print(output)


if __name__ == "__main__":
    main()
