# -*- coding: utf-8 -*-
"""第一模块 condition + 第二模块 MFAC 的统一在线版本管理器。

正式在线版本只由 ``active_version.json`` 发布。第二模块的 canonical 身份是
``mfac``；历史 ``slurry_policy`` / ``policy_*`` 名称只在输入解析和输出兼容处保留，
不再作为运行时事实源，也不会恢复已删除的 ``slurry_policy_model`` 实现。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from system.model.map_control.condition_model.condition_config import (
    ConditionModelConfig,
    from_dict,
)
from system.model.map_control.condition_model.condition_schema import ConditionSnapshot
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
    """规范化后的 condition + MFAC 统一在线版本指针。

    ``policy_*`` properties are read-only migration aliases. New code must use
    the ``mfac_*`` fields.
    """

    integrated_version: str
    condition_version: str
    condition_snapshot_path: Path
    condition_snapshot_sha256: Optional[str]
    grid_condition_mapping_sha256: Optional[str]
    mfac_version: str
    mfac_snapshot_path: Path
    mfac_manifest_sha256: Optional[str]
    mfac_source_condition_version: str
    raw: Dict[str, Any]
    signature: str

    @property
    def policy_version(self) -> str:
        return self.mfac_version

    @property
    def policy_snapshot_path(self) -> Path:
        return self.mfac_snapshot_path

    @property
    def policy_manifest_sha256(self) -> Optional[str]:
        return self.mfac_manifest_sha256

    @property
    def policy_source_condition_version(self) -> str:
        return self.mfac_source_condition_version

    def validate_pair(self) -> None:
        observed = {
            self.integrated_version,
            self.condition_version,
            self.mfac_version,
            self.mfac_source_condition_version,
        }
        if len(observed) != 1:
            raise IntegratedVersionError(
                "第一模块/第二模块MFAC在线版本必须完全一致，实际为 %s"
                % sorted(observed)
            )


def normalize_pointer(value: Dict[str, Any]) -> IntegratedVersionPointer:
    """优先读取 canonical ``mfac`` block，并兼容旧 MFAC 指针格式。

    Compatibility parsing does not imply execution of the removed legacy
    second module. The normalized object always exposes MFAC as module 2.
    """

    if not isinstance(value, dict):
        raise IntegratedVersionError("active_version.json 根对象必须是字典")

    condition_block = value.get("condition")
    if not isinstance(condition_block, dict):
        condition_block = {}

    mfac_block = value.get("mfac")
    if not isinstance(mfac_block, dict):
        mfac_block = {}

    # Input-only compatibility for active pointers emitted during migration.
    legacy_block = value.get("slurry_policy")
    if not isinstance(legacy_block, dict):
        legacy_block = {}
    second_block = mfac_block or legacy_block

    integrated_version = _valid_version(
        value.get("integrated_version")
        or mfac_block.get("version")
        or value.get("mfac_version")
        or value.get("policy_version")
        or legacy_block.get("version")
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
    mfac_version = _valid_version(
        mfac_block.get("version")
        or value.get("mfac_version")
        or legacy_block.get("version")
        or value.get("policy_version")
        or value.get("model_version")
        or integrated_version,
        "mfac.version",
    )
    source_condition_version = _valid_version(
        mfac_block.get("source_condition_version")
        or value.get("mfac_source_condition_version")
        or legacy_block.get("source_condition_version")
        or value.get("source_condition_version")
        or value.get("condition_snapshot_version")
        or condition_version,
        "mfac.source_condition_version",
    )

    condition_path_value = (
        condition_block.get("snapshot_path")
        or condition_block.get("path")
        or value.get("condition_snapshot_path")
    )
    mfac_path_value = (
        second_block.get("snapshot_path")
        or second_block.get("path")
        or value.get("mfac_snapshot_path")
        or value.get("policy_snapshot_path")
    )
    if not str(condition_path_value or "").strip():
        raise IntegratedVersionError("active_version.json 缺少第一模块快照路径")
    if not str(mfac_path_value or "").strip():
        raise IntegratedVersionError("active_version.json 缺少MFAC版本产物路径")

    condition_hash = (
        condition_block.get("snapshot_sha256")
        or condition_block.get("sha256")
        or value.get("condition_snapshot_sha256")
    )
    mapping_hash = (
        condition_block.get("grid_condition_mapping_sha256")
        or second_block.get("grid_condition_mapping_sha256")
        or value.get("grid_condition_mapping_sha256")
    )
    manifest_hash = (
        second_block.get("manifest_sha256")
        or value.get("mfac_manifest_sha256")
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
        mfac_version=mfac_version,
        mfac_snapshot_path=Path(str(mfac_path_value)),
        mfac_manifest_sha256=(
            str(manifest_hash).strip() if manifest_hash else None
        ),
        mfac_source_condition_version=source_condition_version,
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
        mfac_loaded_version: Optional[str] = None,
        slurry_policy_loaded_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return canonical MFAC version status plus one legacy alias."""
        condition_version = str(condition_loaded_version or "")
        mfac_version = str(
            mfac_loaded_version
            if mfac_loaded_version not in (None, "")
            else (slurry_policy_loaded_version or "")
        )
        active_version = str(self._committed_version or "")
        consistent = bool(
            active_version
            and condition_version == active_version
            and mfac_version == active_version
        )
        return {
            "integrated_active_version": active_version,
            "condition_loaded_version": condition_version,
            "mfac_loaded_version": mfac_version,
            # Temporary output compatibility only.
            "slurry_policy_loaded_version": mfac_version,
            "version_consistent": consistent,
            "version_switch_state": self._switch_state,
            "version_switch_time": self._last_switch_time or "",
            "version_switch_error": self._switch_error or "",
            "active_version_file": str(self.active_file),
            "second_module_backend": "MFAC",
        }
