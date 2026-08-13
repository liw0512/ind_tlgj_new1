from __future__ import annotations

import pickle
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from _engine.utils import read_json, safe_name, sha256_file
except ImportError:  # pragma: no cover
    from .._engine.utils import read_json, safe_name, sha256_file


class PolicySnapshotError(RuntimeError):
    pass


def _version(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("v") or not text[1:].isdigit():
        raise PolicySnapshotError("%s 缺少合法 v### 版本: %r" % (name, value))
    return text


class PolicySnapshotLoader:
    """加载并验证已正式激活的第二模块策略快照。

    支持两种运行方式：
    1. 独立第二模块在线运行：``refresh_if_needed`` 自行轮询 active_version.json；
    2. 第一、第二模块集成运行：上层先调用 ``prepare_pointer``，两个模块都
       准备成功后再调用 ``commit_prepared``，从而保证同版本原子切换。

    ``prepare_pointer`` 绝不会修改当前已加载模型；候选验证失败时，旧版本
    的目录、缓存和运行状态均保持不变。
    """

    def __init__(self, plant_config: dict, online_config: dict) -> None:
        self._configured_plant = plant_config
        self._online = online_config
        paths = plant_config["paths"]
        self.active_file = Path(paths["active_policy_version_file"])
        self.output_root = Path(paths["output_root"])
        self.condition_snapshots_dir = Path(paths["condition_snapshots_dir"])
        loading = online_config["model_loading"]
        self.verify_hashes = bool(loading.get("verify_manifest_hashes", True))
        self.reload_interval = float(
            loading.get("reload_check_interval_seconds", 30.0)
        )
        self.cache_size = max(
            1,
            int(loading.get("condition_policy_cache_size", 8)),
        )
        self.require_active = bool(
            loading.get("require_active_version_file", True)
        )
        self.allow_latest = bool(
            loading.get("allow_latest_snapshot_fallback", False)
        )

        self.snapshot_dir: Optional[Path] = None
        self.condition_snapshot_path: Optional[Path] = None
        self.policy_version: Optional[str] = None
        self.condition_version: Optional[str] = None
        self.effective_config: Dict[str, Any] = {}
        self.training_summary: Dict[str, Any] = {}
        self.manifest: Dict[str, Any] = {}
        self._manifest_files: Dict[str, Dict[str, Any]] = {}
        self._active_mtime: Optional[float] = None
        self._last_reload_check = 0.0
        self._condition_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._transient_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._transient_direction_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._plant_prior: Optional[Dict[str, Any]] = None

    @property
    def plant_config(self) -> dict:
        return self.effective_config.get("plant", self._configured_plant)

    @property
    def training_config(self) -> dict:
        return self.effective_config.get("training", {})

    @property
    def effective_disturbance(self) -> dict:
        return self.effective_config.get("disturbance", {})

    def _latest_complete_snapshot(self) -> Path:
        snapshots = self.output_root / "snapshots"
        candidates = []
        if snapshots.exists():
            for path in snapshots.glob("v*"):
                if path.is_dir() and path.name[1:].isdigit():
                    candidates.append(path)
        for path in sorted(
            candidates,
            key=lambda item: int(item.name[1:]),
            reverse=True,
        ):
            if (
                (path / "manifest.json").exists()
                and (path / "effective_config.json").exists()
            ):
                return path
        raise PolicySnapshotError("未找到完整第二模块策略快照")

    def read_active_pointer(self) -> Dict[str, Any]:
        if self.active_file.exists():
            try:
                value = read_json(self.active_file)
            except Exception as exc:
                raise PolicySnapshotError(
                    "active_version.json 读取失败: %s" % exc
                )
            if not isinstance(value, dict):
                raise PolicySnapshotError(
                    "active_version.json 根对象必须是字典"
                )
            return value
        if self.require_active and not self.allow_latest:
            raise PolicySnapshotError(
                "正式激活版本文件不存在: %s" % self.active_file
            )
        if not self.allow_latest:
            raise PolicySnapshotError("未允许按最新目录回退")
        latest = self._latest_complete_snapshot()
        version = latest.name
        return {
            "integrated_version": version,
            "policy_version": version,
            "condition_snapshot_version": version,
            "policy_snapshot_path": str(latest),
            "condition_snapshot_path": str(
                self.condition_snapshots_dir
                / version
                / "condition_snapshot.json"
            ),
        }

    # 保留旧私有名称，兼容已有调用。
    def _read_pointer(self) -> Dict[str, Any]:
        return self.read_active_pointer()

    def _resolve_paths(self, pointer: Dict[str, Any]) -> tuple:
        condition_block = pointer.get("condition")
        if not isinstance(condition_block, dict):
            condition_block = {}
        policy_block = pointer.get("slurry_policy")
        if not isinstance(policy_block, dict):
            policy_block = {}

        integrated_version = _version(
            pointer.get("integrated_version")
            or policy_block.get("version")
            or pointer.get("policy_version")
            or condition_block.get("version")
            or pointer.get("condition_snapshot_version"),
            "integrated_version",
        )
        version = _version(
            policy_block.get("version")
            or pointer.get("policy_version")
            or pointer.get("model_version")
            or integrated_version,
            "slurry_policy.version",
        )
        condition_version = _version(
            condition_block.get("version")
            or pointer.get("condition_snapshot_version")
            or integrated_version,
            "condition.version",
        )
        source_condition_version = _version(
            policy_block.get("source_condition_version")
            or pointer.get("source_condition_version")
            or condition_version,
            "slurry_policy.source_condition_version",
        )

        observed = {
            integrated_version,
            version,
            condition_version,
            source_condition_version,
        }
        if len(observed) != 1:
            raise PolicySnapshotError(
                "统一在线版本不一致: %s" % sorted(observed)
            )

        snapshot = Path(
            policy_block.get("snapshot_path")
            or policy_block.get("path")
            or pointer.get("policy_snapshot_path")
            or (self.output_root / "snapshots" / version)
        )
        condition_path = Path(
            condition_block.get("snapshot_path")
            or condition_block.get("path")
            or pointer.get("condition_snapshot_path")
            or (
                self.condition_snapshots_dir
                / condition_version
                / "condition_snapshot.json"
            )
        )
        pointer_hashes = {
            "condition_snapshot_sha256": str(
                condition_block.get("snapshot_sha256")
                or condition_block.get("sha256")
                or pointer.get("condition_snapshot_sha256")
                or ""
            ),
            "grid_condition_mapping_sha256": str(
                condition_block.get("grid_condition_mapping_sha256")
                or policy_block.get("grid_condition_mapping_sha256")
                or pointer.get("grid_condition_mapping_sha256")
                or ""
            ),
            "policy_manifest_sha256": str(
                policy_block.get("manifest_sha256")
                or pointer.get("policy_manifest_sha256")
                or ""
            ),
        }
        return (
            integrated_version,
            version,
            condition_version,
            source_condition_version,
            snapshot,
            condition_path,
            pointer_hashes,
        )

    def _load_manifest(self, snapshot: Path) -> tuple:
        manifest_path = snapshot / "manifest.json"
        if not manifest_path.exists():
            raise PolicySnapshotError(
                "策略快照缺少 manifest.json: %s" % snapshot
            )
        manifest = read_json(manifest_path)
        listed = {
            str(item.get("path", "")).replace("\\", "/"): item
            for item in manifest.get("files", [])
        }
        return manifest, listed

    def _verify_path(
        self,
        snapshot: Path,
        listed: Dict[str, Any],
        path: Path,
    ) -> None:
        if not path.exists():
            raise PolicySnapshotError("必要文件不存在: %s" % path)
        if not self.verify_hashes:
            return
        relative = str(path.relative_to(snapshot)).replace("\\", "/")
        item = listed.get(relative)
        if item is None:
            raise PolicySnapshotError("manifest 未登记文件: %s" % relative)
        if int(item.get("size", -1)) != path.stat().st_size:
            raise PolicySnapshotError("文件大小校验失败: %s" % relative)
        if str(item.get("sha256", "")) != sha256_file(path):
            raise PolicySnapshotError("文件哈希校验失败: %s" % relative)

    @staticmethod
    def _plant_signature(plant: dict) -> dict:
        towers = []
        for tower in plant.get("towers", []):
            if not tower.get("enabled", True):
                continue
            towers.append(
                {
                    "tower_id": str(tower.get("tower_id")),
                    "ph_column": str(tower.get("ph_column")),
                    "ph_safe_range": [
                        float(value)
                        for value in tower.get("ph_safe_range", [])
                    ],
                    "valves": [
                        {
                            "valve_id": str(valve.get("valve_id")),
                            "column": str(valve.get("column")),
                            "min_opening": float(valve.get("min_opening")),
                            "max_opening": float(valve.get("max_opening")),
                            "action_threshold": float(
                                valve.get("action_threshold")
                            ),
                        }
                        for valve in tower.get("valves", [])
                    ],
                    "supply_pumps": sorted(
                        [
                            {
                                "pump_id": str(pump.get("pump_id")),
                                "current_column": str(
                                    pump.get("current_column")
                                ),
                                "run_current_threshold": float(
                                    pump.get("run_current_threshold")
                                ),
                                "served_valve_ids": sorted(
                                    str(value)
                                    for value in (
                                        pump.get("served_valve_ids", []) or []
                                    )
                                ),
                            }
                            for pump in (tower.get("supply_pumps", []) or [])
                        ],
                        key=lambda item: (
                            item["pump_id"],
                            item["current_column"],
                        ),
                    ),
                }
            )
        return {
            "towers": towers,
            "safe_range": [
                float(value)
                for value in plant["outlet_so2_safe_range"]
            ],
        }

    def prepare_pointer(self, pointer: Dict[str, Any]) -> Dict[str, Any]:
        """完整验证候选指针，但不修改当前已加载版本。"""

        (
            integrated_version,
            version,
            condition_version,
            source_condition_version,
            snapshot,
            condition_path,
            pointer_hashes,
        ) = self._resolve_paths(pointer)
        if not snapshot.exists() or not snapshot.is_dir():
            raise PolicySnapshotError("策略快照目录不存在: %s" % snapshot)

        manifest_path = snapshot / "manifest.json"
        expected_manifest_hash = pointer_hashes["policy_manifest_sha256"]
        if (
            self.verify_hashes
            and expected_manifest_hash
            and sha256_file(manifest_path) != expected_manifest_hash
        ):
            raise PolicySnapshotError(
                "策略 manifest.json 哈希与激活指针不一致"
            )

        manifest, listed = self._load_manifest(snapshot)
        required = [
            snapshot / "effective_config.json",
            snapshot / "training_summary.json",
            snapshot / "condition_alignment.json",
            snapshot / "grid_condition_mapping.csv",
            snapshot / "global" / "plant_action_prior.pkl",
        ]
        for path in required:
            self._verify_path(snapshot, listed, path)

        effective = read_json(snapshot / "effective_config.json")
        summary = read_json(snapshot / "training_summary.json")
        manifest_version = str(manifest.get("policy_snapshot_version", ""))
        summary_version = str(summary.get("policy_snapshot_version", ""))
        effective_condition = str(
            effective.get("condition_alignment", {}).get(
                "condition_snapshot_version",
                "",
            )
        )
        observed_versions = {
            integrated_version,
            version,
            condition_version,
            source_condition_version,
            manifest_version,
            summary_version,
            str(manifest.get("condition_snapshot_version", "")),
            str(summary.get("condition_snapshot_version", "")),
            effective_condition,
        }
        observed_versions.discard("")
        if observed_versions != {version}:
            raise PolicySnapshotError(
                "第一/第二模块版本不一致: %s"
                % sorted(observed_versions)
            )

        mapping_hashes = {
            str(manifest.get("grid_condition_mapping_sha256", "")),
            str(summary.get("grid_condition_mapping_sha256", "")),
            str(
                effective.get("condition_alignment", {}).get(
                    "grid_condition_mapping_sha256",
                    "",
                )
            ),
            pointer_hashes["grid_condition_mapping_sha256"],
        }
        mapping_hashes.discard("")
        if len(mapping_hashes) > 1:
            raise PolicySnapshotError("grid-condition 映射哈希不一致")
        mapping_hash = next(iter(mapping_hashes), "")

        if not condition_path.exists():
            raise PolicySnapshotError(
                "第一模块候选快照不存在: %s" % condition_path
            )
        condition_hash = sha256_file(condition_path)
        expected_condition_hashes = {
            str(summary.get("condition_snapshot_sha256", "")),
            str(manifest.get("condition_snapshot_sha256", "")),
            pointer_hashes["condition_snapshot_sha256"],
        }
        expected_condition_hashes.discard("")
        if len(expected_condition_hashes) > 1:
            raise PolicySnapshotError(
                "第一模块 condition_snapshot 哈希记录不一致"
            )
        if (
            expected_condition_hashes
            and condition_hash not in expected_condition_hashes
        ):
            raise PolicySnapshotError(
                "第一模块 condition_snapshot.json 哈希不一致"
            )

        snapshot_plant = effective.get("plant", {})
        if self._plant_signature(snapshot_plant) != self._plant_signature(
            self._configured_plant
        ):
            raise PolicySnapshotError(
                "当前配置的塔/阀门/供浆泵/pH/SO2安全结构与离线快照不一致"
            )

        return {
            "pointer": dict(pointer),
            "integrated_version": integrated_version,
            "version": version,
            "condition_version": condition_version,
            "source_condition_version": source_condition_version,
            "snapshot": snapshot,
            "condition_path": condition_path,
            "condition_snapshot_sha256": condition_hash,
            "grid_condition_mapping_sha256": mapping_hash,
            "manifest_sha256": sha256_file(manifest_path),
            "manifest": manifest,
            "listed": listed,
            "effective": effective,
            "summary": summary,
        }

    # 兼容旧内部名称。
    def _prepare_snapshot(self, pointer: Dict[str, Any]) -> Dict[str, Any]:
        return self.prepare_pointer(pointer)

    def commit_prepared(self, prepared: Dict[str, Any]) -> bool:
        """提交已验证候选，并清空所有版本相关缓存。"""

        same = (
            self.policy_version == prepared["version"]
            and self.snapshot_dir == prepared["snapshot"]
        )
        if same:
            return False

        self.policy_version = prepared["version"]
        self.condition_version = prepared["condition_version"]
        self.snapshot_dir = prepared["snapshot"]
        self.condition_snapshot_path = prepared["condition_path"]
        self.manifest = prepared["manifest"]
        self._manifest_files = prepared["listed"]
        self.effective_config = prepared["effective"]
        self.training_summary = prepared["summary"]
        self._condition_cache.clear()
        self._transient_cache.clear()
        self._transient_direction_cache.clear()
        self._plant_prior = None
        self._active_mtime = (
            self.active_file.stat().st_mtime
            if self.active_file.exists()
            else None
        )
        return True

    def load_pointer(self, pointer: Dict[str, Any], force: bool = False) -> bool:
        prepared = self.prepare_pointer(pointer)
        same = (
            self.policy_version == prepared["version"]
            and self.snapshot_dir == prepared["snapshot"]
        )
        if same and not force:
            return False
        if same and force:
            # force=True 仍刷新配置和缓存，适用于显式重新验证。
            self.policy_version = None
        return self.commit_prepared(prepared)

    def load_active(self, force: bool = False) -> bool:
        pointer = self.read_active_pointer()
        prepared = self.prepare_pointer(pointer)
        same = (
            self.policy_version == prepared["version"]
            and self.snapshot_dir == prepared["snapshot"]
        )
        if same and not force:
            return False
        if same and force:
            self.policy_version = None
        return self.commit_prepared(prepared)

    def refresh_if_needed(self) -> bool:
        now = time.monotonic()
        if now - self._last_reload_check < self.reload_interval:
            return False
        self._last_reload_check = now
        mtime = (
            self.active_file.stat().st_mtime
            if self.active_file.exists()
            else None
        )
        if self.policy_version is None or mtime != self._active_mtime:
            return self.load_active(force=True)
        return False

    def _read_pickle(self, path: Path) -> Dict[str, Any]:
        if self.snapshot_dir is None:
            raise PolicySnapshotError("策略快照尚未加载")
        self._verify_path(self.snapshot_dir, self._manifest_files, path)
        with path.open("rb") as handle:
            value = pickle.load(handle)
        if str(value.get("policy_snapshot_version", "")) != self.policy_version:
            raise PolicySnapshotError(
                "PKL版本与激活版本不一致: %s" % path
            )
        return value

    def _put_lru(
        self,
        cache: OrderedDict,
        key: str,
        value: Dict[str, Any],
    ) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.cache_size:
            cache.popitem(last=False)

    def load_condition_bundle(self, condition_label: str) -> Dict[str, Any]:
        label = str(condition_label)
        if label in self._condition_cache:
            value = self._condition_cache[label]
            self._condition_cache.move_to_end(label)
            return value
        if self.snapshot_dir is None:
            self.load_active()
        root = (
            self.snapshot_dir
            / "conditions"
            / ("condition_label_%s" % safe_name(label))
        )
        bundle: Dict[str, Any] = {"local": {}, "neighbor": {}}
        local_path = root / "condition_policy.pkl"
        neighbor_path = root / "neighbor_state_policy.pkl"
        if local_path.exists():
            bundle["local"] = self._read_pickle(local_path).get(
                "state_action_profiles",
                {},
            )
        if neighbor_path.exists():
            bundle["neighbor"] = self._read_pickle(neighbor_path).get(
                "state_action_profiles",
                {},
            )
        self._put_lru(self._condition_cache, label, bundle)
        return bundle

    def load_transient(self, disturbance_mode: str) -> Dict[str, Any]:
        mode = str(disturbance_mode)
        if mode in self._transient_cache:
            value = self._transient_cache[mode]
            self._transient_cache.move_to_end(mode)
            return value
        if self.snapshot_dir is None:
            self.load_active()
        path = self.snapshot_dir / "transients" / safe_name(mode) / "policy.pkl"
        states = (
            self._read_pickle(path).get("state_action_profiles", {})
            if path.exists()
            else {}
        )
        self._put_lru(self._transient_cache, mode, states)
        return states

    def load_transient_direction(self, fast_direction: str) -> Dict[str, Any]:
        direction = str(fast_direction)
        if direction in self._transient_direction_cache:
            value = self._transient_direction_cache[direction]
            self._transient_direction_cache.move_to_end(direction)
            return value
        if self.snapshot_dir is None:
            self.load_active()
        path = self.snapshot_dir / "transient_direction" / safe_name(direction) / "policy.pkl"
        states = self._read_pickle(path).get("state_action_profiles", {}) if path.exists() else {}
        self._put_lru(self._transient_direction_cache, direction, states)
        return states

    def load_plant_prior(self) -> Dict[str, Any]:
        if self._plant_prior is not None:
            return self._plant_prior
        if self.snapshot_dir is None:
            self.load_active()
        path = self.snapshot_dir / "global" / "plant_action_prior.pkl"
        self._plant_prior = self._read_pickle(path).get(
            "state_action_profiles",
            {},
        )
        return self._plant_prior
