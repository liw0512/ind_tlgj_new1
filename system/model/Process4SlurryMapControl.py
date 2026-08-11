# -*- coding: utf-8 -*-
"""Wet-FGD slurry-control adapter for the copied Process4MapControl shell.

The legacy ProcessForMapConsole remains responsible for:
- DATA_COLLECTION -> MODEL_TRAINING -> NORMAL_OPERATION lifecycle;
- realtime consume/processing/snapshot threads;
- asynchronous DB queue/writer threads;
- periodic incremental-training checks;
- maintenance and communication-state handling.

This adapter replaces only the old algorithm hooks:
1. cluster + Q-learning + PH training -> condition_model + slurry_policy_model;
2. MapControPre online inference -> integrated condition/policy pipeline.

Database schemas/inserts and GUI structures intentionally remain untouched in
this integration step.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from system.model.Process4MapControl import ProcessForMapConsole as LegacyProcessForMapConsole
from system.model.config.process4map_config import PROCESS4MAP_CONFIG
from system.model.config.slurry_core_bridge_config import (
    SLURRY_CORE_BRIDGE_CONFIG,
    SLURRY_INPUT_FIELDS,
)
from system.base.config.SysConfig import config
from system.base.LogUntil import setup_log

logging = setup_log("process_for_slurry_mapconsole")


class ProcessForMapConsole(LegacyProcessForMapConsole):
    """p4pc shell using the new slurry-control core."""

    # Base __init__ uses self.titles, so overriding this class attribute makes
    # clean_data preserve the new core's realtime fields without modifying the
    # legacy DB schema in this step.
    titles = list(SLURRY_INPUT_FIELDS)
    limit = {
        key: value.copy()
        for key, value in PROCESS4MAP_CONFIG.limits.items()
    }

    def __init__(self, GLOBAL_DATA):
        self.slurry_core_config = dict(SLURRY_CORE_BRIDGE_CONFIG)
        self._slurry_pipeline = None
        self._slurry_pipeline_error: Optional[str] = None
        self._slurry_pipeline_lock = threading.RLock()

        # The base constructor starts check_system_state in a thread.  Hold that
        # state thread until this adapter finishes restoring an already-active
        # model after a server restart.
        self._slurry_bridge_ready = threading.Event()
        super().__init__(GLOBAL_DATA)

        # The copied p4pc already exposes this as a config value.  It is now the
        # single online judgement cadence for the new integrated model.
        self.snapshot_interval = float(
            self.process_config.runtime.snapshot_interval_seconds
        )

        self._restore_active_runtime_if_available()
        self._slurry_bridge_ready.set()
        logging.info(
            "供浆版 p4pc 初始化完成: model_judgement_interval=%ss, state=%s",
            self.snapshot_interval,
            self.system_state,
        )

    # ------------------------------------------------------------------
    # Lifecycle/state integration
    # ------------------------------------------------------------------
    def check_system_state(self):
        """Reuse the legacy p4pc state machine after bridge initialization."""
        self._slurry_bridge_ready.wait()
        return super().check_system_state()

    def _active_version_file(self) -> Path:
        return Path(self.slurry_core_config["active_version_file"])

    @staticmethod
    def _parse_activation_time(value: Any) -> Optional[datetime.datetime]:
        if value in (None, ""):
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except Exception:
            return None

    def _restore_active_runtime_if_available(self) -> bool:
        """Resume NORMAL_OPERATION when a valid active-version pointer exists."""
        active_path = self._active_version_file()
        if not active_path.is_file():
            logging.info(
                "未发现 active_version.json，保持 p4pc 初始状态 %s",
                self.system_state,
            )
            return False
        try:
            with active_path.open("r", encoding="utf-8") as stream:
                pointer = json.load(stream)
            version = str(
                pointer.get("integrated_version")
                or pointer.get("policy_version")
                or ""
            ).strip()
            if not version:
                raise ValueError("active_version.json 缺少 integrated_version")

            self.is_initial_training = False
            self.model_training_completed = True
            self.system_state = self.SystemState.NORMAL_OPERATION
            activated = self._parse_activation_time(pointer.get("activated_at"))
            self.last_training_time = activated or datetime.datetime.fromtimestamp(
                active_path.stat().st_mtime
            )

            # Load eagerly on restart so a broken pointer is visible in logs.
            # If this fails, insert_Mod will keep retrying while the service
            # remains alive; the active pointer itself is not modified here.
            self._ensure_slurry_pipeline(force=True)
            logging.info(
                "检测到已激活同版本模型 %s，服务器重启后直接恢复 NORMAL_OPERATION",
                version,
            )
            return True
        except Exception as exc:
            logging.error(
                "恢复 active_version.json 失败，继续按初始采集/训练流程运行: %s",
                exc,
            )
            self._slurry_pipeline = None
            self._slurry_pipeline_error = str(exc)
            return False

    # ------------------------------------------------------------------
    # Configuration/path helpers
    # ------------------------------------------------------------------
    def _core_path(self, key: str) -> str:
        value = str(self.slurry_core_config[key])
        return os.path.normpath(value)

    @staticmethod
    def _version_number(version: str) -> int:
        text = str(version).strip()
        if text.lower().startswith("v") and text[1:].isdigit():
            return int(text[1:])
        raise ValueError("版本必须是 v### 格式: %r" % version)

    def _next_version(self, version: str) -> str:
        return "v%03d" % (self._version_number(version) + 1)

    def _read_active_version(self) -> str:
        path = self._active_version_file()
        if not path.is_file():
            raise FileNotFoundError("增量训练前未找到 active_version.json: %s" % path)
        with path.open("r", encoding="utf-8") as stream:
            pointer = json.load(stream)
        version = str(
            pointer.get("integrated_version")
            or pointer.get("policy_version")
            or ""
        ).strip()
        self._version_number(version)
        return version

    def _training_mode_settings(self, mode):
        """Reuse p4pc data-source config and scale DB counts with cadence.

        CSV mode preserves the explicit minimum-record setting.  In database
        mode, p4pc writes/consumes model snapshots at the configured judgement
        interval, so changing 30s -> 10s also updates the expected records/day
        automatically.
        """
        settings = dict(super()._training_mode_settings(mode))
        if settings["source"] == "database":
            interval = max(0.001, float(self.snapshot_interval))
            records_per_day = max(1, int(round(24 * 60 * 60 / interval)))
            target = max(1, int(settings["days"]) * records_per_day)
            ratio = float(
                self.process_config.training.database_minimum_data_ratio
            )
            settings["database_record_limit"] = target
            settings["minimum_records"] = max(1, int(target * ratio))
        return settings

    # ------------------------------------------------------------------
    # New two-module offline training
    # ------------------------------------------------------------------
    def _training_env(self) -> tuple[str, Dict[str, str]]:
        project_root = self._project_root()
        env = os.environ.copy()
        python_paths = [
            project_root,
            os.path.dirname(__file__),
            os.path.join(project_root, "system"),
            os.path.join(project_root, "system", "model"),
            os.path.join(project_root, "system", "model", "map_control"),
        ]
        existing = env.get("PYTHONPATH")
        if existing:
            python_paths.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
        return project_root, env

    def _condition_paths_for_version(self, version: str) -> Dict[str, str]:
        root = Path(self._core_path("condition_snapshots_dir")) / version
        return {
            "snapshot": str(root / "condition_snapshot.json"),
            "report": str(root / "auto_merge_report.json"),
        }

    def _run_condition_initial(
        self,
        python_exe: str,
        env: Dict[str, str],
        project_root: str,
        training_csv: str,
        version: str,
    ) -> tuple[str, str]:
        paths = self._condition_paths_for_version(version)
        output_csv = self._core_path("initial_condition_output_csv")
        script = self._core_path("condition_initial_script")
        self._run_training_command(
            "initial-condition-model",
            [
                python_exe,
                script,
                "--input",
                training_csv,
                "--output",
                output_csv,
                "--snapshot-output",
                paths["snapshot"],
                "--merge-statistics-output",
                self._core_path("condition_merge_statistics"),
                "--auto-merge-report",
                paths["report"],
                "--snapshot-version",
                version,
            ],
            env,
            cwd=os.path.dirname(script),
        )
        if not os.path.isfile(paths["snapshot"]):
            raise FileNotFoundError(
                "第一模块初次训练完成但未找到快照: %s" % paths["snapshot"]
            )
        if not os.path.isfile(output_csv):
            raise FileNotFoundError(
                "第一模块初次训练完成但未找到标注 CSV: %s" % output_csv
            )
        return output_csv, paths["snapshot"]

    def _run_condition_incremental(
        self,
        python_exe: str,
        env: Dict[str, str],
        project_root: str,
        training_csv: str,
        active_version: str,
        target_version: str,
    ) -> tuple[str, str]:
        base_snapshot = self._condition_paths_for_version(active_version)["snapshot"]
        target_paths = self._condition_paths_for_version(target_version)
        output_csv = self._core_path("incremental_condition_output_csv")
        script = self._core_path("condition_incremental_script")
        if not os.path.isfile(base_snapshot):
            raise FileNotFoundError(
                "当前激活版本缺少第一模块快照: %s" % base_snapshot
            )
        self._run_training_command(
            "incremental-condition-model",
            [
                python_exe,
                script,
                "--base-snapshot",
                base_snapshot,
                "--input",
                training_csv,
                "--output",
                output_csv,
                "--snapshot-output",
                target_paths["snapshot"],
                "--merge-statistics-output",
                self._core_path("condition_merge_statistics"),
                "--auto-merge-report",
                target_paths["report"],
                "--snapshot-version",
                target_version,
            ],
            env,
            cwd=os.path.dirname(script),
        )
        if not os.path.isfile(target_paths["snapshot"]):
            raise FileNotFoundError(
                "第一模块增量训练完成但未找到快照: %s"
                % target_paths["snapshot"]
            )
        if not os.path.isfile(output_csv):
            raise FileNotFoundError(
                "第一模块增量训练完成但未找到标注 CSV: %s" % output_csv
            )
        return output_csv, target_paths["snapshot"]

    def _run_policy_initial(
        self,
        python_exe: str,
        env: Dict[str, str],
        project_root: str,
        labeled_csv: str,
        condition_snapshot: str,
    ) -> None:
        script = self._core_path("slurry_policy_initial_script")
        self._run_training_command(
            "initial-slurry-policy",
            [
                python_exe,
                script,
                "--input",
                labeled_csv,
                "--output",
                self._core_path("slurry_policy_output_root"),
                "--condition-snapshot",
                condition_snapshot,
                "--config",
                self._core_path("slurry_policy_config"),
            ],
            env,
            cwd=os.path.dirname(script),
        )

    def _run_policy_incremental(
        self,
        python_exe: str,
        env: Dict[str, str],
        project_root: str,
        labeled_csv: str,
        condition_snapshot: str,
        active_version: str,
    ) -> None:
        script = self._core_path("slurry_policy_incremental_script")
        previous = (
            Path(self._core_path("slurry_policy_output_root"))
            / "snapshots"
            / active_version
        )
        args = [
            python_exe,
            script,
            "--input",
            labeled_csv,
            "--output",
            self._core_path("slurry_policy_output_root"),
            "--previous",
            str(previous),
            "--condition-snapshot",
            condition_snapshot,
            "--config",
            self._core_path("slurry_policy_config"),
        ]
        if bool(self.slurry_core_config.get("activate_after_training", True)):
            args.append("--activate-after-success")
        self._run_training_command(
            "incremental-slurry-policy",
            args,
            env,
            cwd=os.path.dirname(script),
        )

    def _activate_version(
        self,
        python_exe: str,
        env: Dict[str, str],
        version: str,
    ) -> None:
        script = self._core_path("slurry_policy_activate_script")
        self._run_training_command(
            "activate-integrated-version",
            [
                python_exe,
                script,
                "--version",
                version,
                "--config",
                self._core_path("slurry_policy_config"),
            ],
            env,
            cwd=os.path.dirname(script),
        )

    def _do_training(self):
        """Replace legacy three-module training with the new two-module pair."""
        mode = "initial" if self.is_initial_training else "incremental"
        try:
            with self.training_lock:
                logging.info("=== 开始供浆核心 %s 训练流程 ===", mode)
                df, settings = self._load_training_data(mode)
                training_csv = self._save_training_work_csv(df, settings)

                project_root, env = self._training_env()
                python_exe = config.get("python_exe", "python")

                if mode == "initial":
                    version = str(
                        self.slurry_core_config.get("initial_version", "v001")
                    )
                    labeled_csv, condition_snapshot = self._run_condition_initial(
                        python_exe,
                        env,
                        project_root,
                        training_csv,
                        version,
                    )
                    self._run_policy_initial(
                        python_exe,
                        env,
                        project_root,
                        labeled_csv,
                        condition_snapshot,
                    )
                    if bool(
                        self.slurry_core_config.get(
                            "activate_after_training", True
                        )
                    ):
                        self._activate_version(python_exe, env, version)

                    # Only after both modules are complete and the atomic active
                    # pointer is published may p4pc enter normal operation.
                    self.is_initial_training = False
                    self.model_training_completed = True
                    self.last_training_time = datetime.datetime.now()
                    self.system_state = self.SystemState.NORMAL_OPERATION
                    self._ensure_slurry_pipeline(force=True)
                    logging.info(
                        "初次 condition_model + slurry_policy_model 训练完成并激活: %s",
                        version,
                    )
                    return

                if self.system_state != self.SystemState.NORMAL_OPERATION:
                    logging.warning(
                        "当前系统状态不是 NORMAL_OPERATION，禁止增量训练"
                    )
                    return

                active_version = self._read_active_version()
                target_version = self._next_version(active_version)
                labeled_csv, condition_snapshot = (
                    self._run_condition_incremental(
                        python_exe,
                        env,
                        project_root,
                        training_csv,
                        active_version,
                        target_version,
                    )
                )
                self._run_policy_incremental(
                    python_exe,
                    env,
                    project_root,
                    labeled_csv,
                    condition_snapshot,
                    active_version,
                )
                if not bool(
                    self.slurry_core_config.get("activate_after_training", True)
                ):
                    self._activate_version(
                        python_exe, env, target_version
                    )

                # Online pipeline is intentionally not rebuilt here.  Its
                # IntegratedVersionManager observes the new atomic pointer and
                # switches condition+policy together on a later online cycle.
                self.last_training_time = datetime.datetime.now()
                logging.info(
                    "增量 condition_model + slurry_policy_model 训练完成: %s -> %s；"
                    "在线线程继续运行并通过 active_version.json 原子热切换",
                    active_version,
                    target_version,
                )
        except Exception as exc:
            logging.error("供浆核心 %s 训练失败: %s", mode, exc)
            traceback.print_exc()
            if mode == "initial":
                # Allow the legacy state checker to return to data collection
                # and retry after the configured interval/data check.
                self.model_training_completed = False
                self.system_state = self.SystemState.DATA_COLLECTION
            # Incremental failure keeps NORMAL_OPERATION and the old active
            # in-memory model.  active_version.json is not changed by a failed
            # candidate training.
            raise
        finally:
            self.is_training = False

    # ------------------------------------------------------------------
    # Integrated online inference
    # ------------------------------------------------------------------
    def _integration_config(self) -> Dict[str, Any]:
        from system.model.map_control.condition_model.condition_config import (
            ONLINE_CONDITION_CLASSIFY_CONFIG,
        )

        bridge = copy.deepcopy(
            ONLINE_CONDITION_CLASSIFY_CONFIG.get(
                "slurry_policy_online", {}
            )
        )
        bridge["enabled"] = True
        bridge["config_spec"] = self._core_path("slurry_policy_config")
        bridge["external_version_management"] = True

        integrated = dict(bridge.get("integrated_version") or {})
        integrated.update(
            {
                "enabled": True,
                "active_version_file": self._core_path(
                    "active_version_file"
                ),
                "hot_reload_enabled": True,
                "reload_check_interval_seconds": max(
                    1.0, float(self.snapshot_interval)
                ),
                "verify_condition_snapshot_hash": True,
                "require_atomic_pair_switch": True,
                "reset_condition_stability_window": True,
                "preserve_runtime_control_state": True,
                "keep_current_version_on_failure": True,
            }
        )
        bridge["integrated_version"] = integrated
        return bridge

    def _ensure_slurry_pipeline(self, force: bool = False) -> bool:
        with self._slurry_pipeline_lock:
            if self._slurry_pipeline is not None and not force:
                return True
            try:
                from system.model.map_control.condition_model.online_condition_classifier import (
                    build_online_condition_policy_pipeline,
                )

                candidate = build_online_condition_policy_pipeline(
                    snapshot_path="active",
                    integration_config=self._integration_config(),
                )
                self._slurry_pipeline = candidate
                self._slurry_pipeline_error = None
                logging.info("新的 condition/policy 集成在线 Pipeline 已加载")
                return True
            except Exception as exc:
                self._slurry_pipeline_error = str(exc)
                if force:
                    self._slurry_pipeline = None
                logging.error("新的集成在线 Pipeline 暂不可用: %s", exc)
                return False

    def _runtime_target(
        self, data: Dict[str, Any], explicit_target: Any
    ) -> Optional[float]:
        if explicit_target not in (None, ""):
            try:
                return float(explicit_target)
            except (TypeError, ValueError):
                pass
        column = str(
            self.slurry_core_config.get(
                "target_column", "outlet_so2_target"
            )
        )
        value = data.get(column)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _safe_online_hold(
        data: Dict[str, Any],
        error: Optional[str],
    ) -> Dict[str, Any]:
        result = dict(data)
        result.update(
            {
                "condition_valid": False,
                "condition_stable": False,
                "version_consistent": False,
                "slurry_policy_decision_status": "BLOCKED",
                "slurry_policy_control_mode": "BLOCKED",
                "slurry_policy_action_id": "HOLD",
                "slurry_policy_action_family": "HOLD",
                "slurry_policy_action_direction": "HOLD",
                "slurry_policy_action_magnitude": "HOLD",
                "slurry_policy_recommended_valve_deltas": {},
                "slurry_policy_projected_valve_openings": {},
                "slurry_policy_reason_codes": [
                    "INTEGRATED_SLURRY_MODEL_UNAVAILABLE",
                    error or "UNKNOWN",
                ],
            }
        )
        return result

    def insert_Mod(self, data, target_so2, store_to_db=True):
        """Run the new integrated online core at p4pc's configured cadence.

        ``_snapshot_scheduler_loop`` in the copied p4pc calls this method every
        ``runtime.snapshot_interval_seconds`` while NORMAL_OPERATION.  Thus the
        old fixed 30-second model call is now a normal configurable p4pc value.
        """
        str_time = data.get("date", pd.Timestamp.now())

        if self.system_state != self.SystemState.NORMAL_OPERATION:
            result = dict(data)
        elif self._ensure_slurry_pipeline():
            try:
                result = dict(
                    self._slurry_pipeline.process(
                        dict(data),
                        target=self._runtime_target(data, target_so2),
                        execution_context={},
                    )
                )
            except Exception as exc:
                logging.error("供浆集成在线推理失败: %s", exc)
                traceback.print_exc()
                result = self._safe_online_hold(
                    data, str(exc)
                )
        else:
            result = self._safe_online_hold(
                data, self._slurry_pipeline_error
            )

        result["date"] = str_time
        result["model_seq"] = data.get("_snapshot_seq", -1)
        result["_write_target"] = (
            self.process_config.persistence.model_write_target
        )
        result["_is_valid"] = store_to_db
        if not store_to_db:
            result["_invalid_reason"] = "数据验证失败"

        send_copy = {
            key: value
            for key, value in result.items()
            if not str(key).startswith("_")
        }
        self.send_data = send_copy
        self.result = send_copy

        # The current DCS/GUI shell already reads GLOBAL_DATA['map_control'].
        # Publish the new core's complete output there; DB column adaptation is
        # intentionally deferred to the next step requested by the user.
        self._publish_map_control(send_copy)
        self.send()
        logging.info(
            "供浆模型结果: condition=%s, action=%s, magnitude=%s, version=%s",
            send_copy.get("condition_label"),
            send_copy.get("slurry_policy_action_family"),
            send_copy.get("slurry_policy_action_magnitude"),
            send_copy.get("integrated_active_version"),
        )
        return result

    def record_slurry_execution(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        """Future DCS writeback hook for the second module's WAITING_EFFECT state."""
        if not self._ensure_slurry_pipeline():
            raise RuntimeError(
                "integrated slurry pipeline unavailable: %s"
                % (self._slurry_pipeline_error or "UNKNOWN")
            )
        return dict(self._slurry_pipeline.record_execution(dict(feedback)))
