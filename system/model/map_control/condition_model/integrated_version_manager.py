# -*- coding: utf-8 -*-
"""第一、第二模块在线版本对的统一管理器。

正式在线版本只由 ``active_version.json`` 发布。第一模块增量快照可以先生成，
第二模块继续基于该固定版本训练；只有第二模块训练完成并通过激活脚本验证后，
在线端才会看到新的版本指针。

本管理器只负责：
1. 读取并规范化统一版本指针；
2. 校验并准备第一模块候选快照；
3. 记录版本切换状态；
4. 为集成在线管线提供轮询和审计字段。

第二模块候选策略的完整 manifest、PKL、配置和映射校验由
``PolicySnapshotLoader.prepare_pointer`` 完成。两个候选都成功后，集成管线才会
在同一个锁内提交，避免出现 condition vN + policy vM 的短暂错配。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from system.model.map_control.condition_model.condition_config import (
    ConditionModelConfig,
    from_dict,
)
from system.model.map_control.condition_model.condition_schema import (
    ConditionSnapshot,
)
from system.model.map_control.condition_model.snapshot_io import read_snapshot


class IntegratedVersionError(RuntimeError):
    """统一版本指针或候选版本无效。"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Dict[str, Any]) -> str:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_version(value: Any, field_name: str) -> str:
    version = str(value or "").strip()
    if not version.startswith("v") or not version[1:].isdigit():
        raise IntegratedVersionError(
            "%s 必须是 v### 格式，实际为 %r" % (field_name, value)
        )
    return version


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IntegratedVersionPointer:
    """规范化后的统一在线版本指针。"""

    integrated_version: str
    condition_version: str
    condition_snapshot_path: Path
    condition_snapshot_sha256: Optional[str]
    grid_condition_mapping_sha256: Optional[str]
    policy_version: str
    policy_snapshot_path: Path
    policy_manifest_sha256: Optional[str]
    policy_source_condition_version: str
    raw: Dict[str, Any]
    signature: str

    def validate_pair(self) -> None:
        observed = {
            self.integrated_version,
            self.condition_version,
            self.policy_version,
            self.policy_source_condition_version,
        }
        if len(observed) != 1:
            raise IntegratedVersionError(
                "第一/第二模块在线版本必须完全一致，实际为 %s"
                % sorted(observed)
            )


def normalize_pointer(value: Dict[str, Any]) -> IntegratedVersionPointer:
    """兼容新嵌套格式和旧扁平格式，并强制形成同版本对。"""

    if not isinstance(value, dict):
        raise IntegratedVersionError("active_version.json 根对象必须是字典")

    condition_block = value.get("condition")
    if not isinstance(condition_block, dict):
        condition_block = {}
    policy_block = value.get("slurry_policy")
    if not isinstance(policy_block, dict):
        policy_block = {}

    integrated_version = _valid_version(
        value.get("integrated_version")
        or value.get("policy_version")
        or policy_block.get("version")
        or value.get("condition_snapshot_version")
        or condition_block.get("version"),
        "integrated_version",
    )
    condition_version = _valid_version(
        condition_block.get("version")
        or value.get("condition_snapshot_version")
        or integrated_version,
        "condition.version",
    )
    policy_version = _valid_version(
        policy_block.get("version")
        or value.get("policy_version")
        or value.get("model_version")
        or integrated_version,
        "slurry_policy.version",
    )
    source_condition_version = _valid_version(
        policy_block.get("source_condition_version")
        or value.get("source_condition_version")
        or value.get("condition_snapshot_version")
        or condition_version,
        "slurry_policy.source_condition_version",
    )

    condition_path_value = (
        condition_block.get("snapshot_path")
        or condition_block.get("path")
        or value.get("condition_snapshot_path")
    )
    policy_path_value = (
        policy_block.get("snapshot_path")
        or policy_block.get("path")
        or value.get("policy_snapshot_path")
    )
    if not str(condition_path_value or "").strip():
        raise IntegratedVersionError("active_version.json 缺少第一模块快照路径")
    if not str(policy_path_value or "").strip():
        raise IntegratedVersionError("active_version.json 缺少第二模块快照路径")

    condition_hash = (
        condition_block.get("snapshot_sha256")
        or condition_block.get("sha256")
        or value.get("condition_snapshot_sha256")
    )
    mapping_hash = (
        condition_block.get("grid_condition_mapping_sha256")
        or policy_block.get("grid_condition_mapping_sha256")
        or value.get("grid_condition_mapping_sha256")
    )
    manifest_hash = (
        policy_block.get("manifest_sha256")
        or value.get("policy_manifest_sha256")
    )

    pointer = IntegratedVersionPointer(
        integrated_version=integrated_version,
        condition_version=condition_version,
        condition_snapshot_path=Path(str(condition_path_value)),
        condition_snapshot_sha256=(
            str(condition_hash).strip() if condition_hash else None
        ),
        grid_condition_mapping_sha256=(
            str(mapping_hash).strip() if mapping_hash else None
        ),
        policy_version=policy_version,
        policy_snapshot_path=Path(str(policy_path_value)),
        policy_manifest_sha256=(
            str(manifest_hash).strip() if manifest_hash else None
        ),
        policy_source_condition_version=source_condition_version,
        raw=dict(value),
        signature=_canonical_hash(value),
    )
    pointer.validate_pair()
    return pointer


class IntegratedVersionManager:
    """轮询统一版本指针并准备第一模块候选快照。"""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        active_path = str(self.config.get("active_version_file", "")).strip()
        if self.enabled and not active_path:
            raise IntegratedVersionError(
                "integrated_version.active_version_file 不能为空"
            )
        self.active_file = Path(active_path) if active_path else Path()
        self.hot_reload_enabled = bool(
            self.config.get("hot_reload_enabled", True)
        )
        self.reload_interval = max(
            0.0,
            float(self.config.get("reload_check_interval_seconds", 30.0)),
        )
        self.verify_condition_hash = bool(
            self.config.get("verify_condition_snapshot_hash", True)
        )
        self.keep_current_on_failure = bool(
            self.config.get("keep_current_version_on_failure", True)
        )

        self._last_check_monotonic = 0.0
        self._committed_signature: Optional[str] = None
        self._committed_version: Optional[str] = None
        self._last_switch_time: Optional[str] = None
        self._switch_state = "UNINITIALIZED"
        self._switch_error: Optional[str] = None
        self._rejected_signature: Optional[str] = None

    def read_pointer(self) -> IntegratedVersionPointer:
        if not self.enabled:
            raise IntegratedVersionError("统一版本管理已关闭")
        if not self.active_file.exists():
            raise IntegratedVersionError(
                "统一激活版本文件不存在: %s" % self.active_file
            )
        try:
            with self.active_file.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except Exception as exc:
            raise IntegratedVersionError(
                "active_version.json 读取失败: %s" % exc
            )
        return normalize_pointer(value)

    def startup_pointer(self) -> IntegratedVersionPointer:
        pointer = self.read_pointer()
        self._switch_state = "PREPARING_STARTUP_VERSION"
        return pointer

    def poll(self, force: bool = False) -> Optional[IntegratedVersionPointer]:
        """返回需要准备的新版本；没有变化时返回 ``None``。"""

        if not self.enabled or not self.hot_reload_enabled:
            return None
        now = time.monotonic()
        if not force and now - self._last_check_monotonic < self.reload_interval:
            return None
        self._last_check_monotonic = now

        pointer = self.read_pointer()
        if pointer.signature == self._committed_signature:
            return None
        if pointer.signature == self._rejected_signature and not force:
            return None
        self._switch_state = "PREPARING_CANDIDATE"
        self._switch_error = None
        return pointer

    def prepare_condition(
        self,
        pointer: IntegratedVersionPointer,
    ) -> Tuple[ConditionSnapshot, ConditionModelConfig]:
        path = pointer.condition_snapshot_path
        if not path.exists() or not path.is_file():
            raise IntegratedVersionError(
                "第一模块候选快照不存在: %s" % path
            )
        if (
            self.verify_condition_hash
            and pointer.condition_snapshot_sha256
            and _sha256_file(path) != pointer.condition_snapshot_sha256
        ):
            raise IntegratedVersionError(
                "第一模块 condition_snapshot.json 哈希与激活指针不一致"
            )

        snapshot = read_snapshot(str(path))
        snapshot_version = _valid_version(
            snapshot.snapshot_version,
            "condition_snapshot.snapshot_version",
        )
        if snapshot_version != pointer.condition_version:
            raise IntegratedVersionError(
                "第一模块快照版本 %s 与激活版本 %s 不一致"
                % (snapshot_version, pointer.condition_version)
            )
        config = from_dict(snapshot.grid_config)
        config.validate()
        return snapshot, config

    def commit(self, pointer: IntegratedVersionPointer, *, startup: bool) -> None:
        self._committed_signature = pointer.signature
        self._committed_version = pointer.integrated_version
        self._last_switch_time = utc_now_iso()
        self._switch_error = None
        self._rejected_signature = None
        self._switch_state = (
            "INITIALIZING_ACTIVE_VERSION"
            if startup
            else "INITIALIZING_NEW_CONDITION_WINDOW"
        )

    def mark_condition_window_stable(self) -> None:
        if self._committed_version is not None:
            self._switch_state = "STABLE"
            self._switch_error = None

    def reject(self, pointer: Optional[IntegratedVersionPointer], error: Exception) -> None:
        if pointer is not None:
            self._rejected_signature = pointer.signature
        self._switch_error = str(error)
        self._switch_state = "NEW_VERSION_REJECTED"

    @property
    def committed_version(self) -> Optional[str]:
        return self._committed_version

    @property
    def switch_state(self) -> str:
        return self._switch_state

    def status_fields(
        self,
        *,
        condition_loaded_version: Optional[str],
        slurry_policy_loaded_version: Optional[str],
    ) -> Dict[str, Any]:
        condition_version = str(condition_loaded_version or "")
        policy_version = str(slurry_policy_loaded_version or "")
        active_version = str(self._committed_version or "")
        consistent = bool(
            active_version
            and condition_version == active_version
            and policy_version == active_version
        )
        return {
            "integrated_active_version": active_version,
            "condition_loaded_version": condition_version,
            "slurry_policy_loaded_version": policy_version,
            "version_consistent": consistent,
            "version_switch_state": self._switch_state,
            "version_switch_time": self._last_switch_time or "",
            "version_switch_error": self._switch_error or "",
            "active_version_file": str(self.active_file),
        }
