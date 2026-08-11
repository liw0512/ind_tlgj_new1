"""
配置说明（自动补全）
====================
本文件是泵结构和项目路径的唯一事实源。
换厂、改单塔/双塔、改变泵数量、字段顺序或泵功率时，优先修改 PumpConfig；
其他模块会从 PROJECT_CONFIG 自动读取，不要在多个文件重复修改同一组泵参数。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple


def _find_project_root() -> Path:
    """Return the repository root based on this file location."""
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ProjectPaths:
    root: Path = field(default_factory=_find_project_root)  # 项目根目录；默认根据当前文件位置自动定位，通常不需要修改

    @property
    def map_control_dir(self) -> Path:
        return self.root / "system" / "model" / "map_control"

    @property
    def files_dir(self) -> Path:
        return self.root / "files"

    @property
    def cluster_dir(self) -> Path:
        return self.map_control_dir / "cluster"

    @property
    def cluster_models_dir(self) -> Path:
        return self.cluster_dir / "models"

    @property
    def q_learning_dir(self) -> Path:
        return self.map_control_dir / "q_learning"

    @property
    def q_results_dir(self) -> Path:
        return self.q_learning_dir / "results" / "train"

    @property
    def q_update_results_dir(self) -> Path:
        return self.q_learning_dir / "results" / "update"

    @property
    def q_predict_results_dir(self) -> Path:
        return self.q_learning_dir / "results" / "predict"


@dataclass(frozen=True)
class PumpConfig:
    """Shared single-tower pump metadata used by cluster and Q-learning."""

    xst_pump_count: int = 5  # 一级塔循环泵数量；当前单塔为 5
    apt_pump_count: int = 0  # 二级塔循环泵数量；单塔设为 0，双塔填写实际数量
    min_running_pumps: int = 3  # 全系统最少运行泵数；需符合现场安全运行要求
    pump_current_mapping: Dict[int, str] = field(  # 泵位索引到电流字段映射；索引顺序必须与状态位一致
        default_factory=lambda: {
            0: "xstjyxhb_ADL",
            1: "xstjyxhb_BDL",
            2: "xstjyxhb_CDL",
            3: "xstjyxhb_DDL",
            4: "xstjyxhb_EDL",
        }
    )
    pump_status_columns: List[str] = field(  # 每台泵的 0/1 状态字段列表；顺序决定组合字符串位序
        default_factory=lambda: [
            "xst_ADL_status",
            "xst_BDL_status",
            "xst_CDL_status",
            "xst_DDL_status",
            "xst_EDL_status",
        ]
    )
    pump_power_config: Dict[str, int] = field(  # 泵名称到额定/等效功率映射，单位通常为 kW
        default_factory=lambda: {
            "xst_ADL": 42,
            "xst_BDL": 42,
            "xst_CDL": 41,
            "xst_DDL": 39,
            "xst_EDL": 36,
        }
    )
    pump_powers: List[int] = field(default_factory=lambda: [42, 42, 41, 39, 36])  # 按状态位顺序排列的功率列表；必须与泵数量相等
    # Legal actions are count-based, not a whitelist of named pump combinations.
    valid_pump_patterns: List[Tuple[int, int]] = field(  # 合法泵数模式，格式为 (一级塔运行台数, 二级塔运行台数)
        default_factory=lambda: [(3, 0), (4, 0), (5, 0)]
    )
    forbidden_pump_combinations: List[str] = field(default_factory=list)  # 明确禁止的具体组合字符串；为空表示不设置具体白名单/黑名单


@dataclass(frozen=True)
class ProjectConfig:
    paths: ProjectPaths = field(default_factory=ProjectPaths)  # 统一项目路径配置对象，通常使用默认值
    pump: PumpConfig = field(default_factory=PumpConfig)  # 统一泵结构配置对象；换厂和改单/双塔时优先修改 PumpConfig

    def ensure_runtime_dirs(self) -> None:
        for path in [
            self.paths.cluster_models_dir,
            self.paths.q_results_dir,
            self.paths.q_update_results_dir,
            self.paths.q_predict_results_dir,
        ]:
            os.makedirs(path, exist_ok=True)


PROJECT_CONFIG = ProjectConfig()

