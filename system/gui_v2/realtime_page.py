from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from system.model.config.plant_config import PLANT_CONFIG

from .widgets import CardFrame, KeyValueRow, StatusPill


class SignalTile(QFrame):
    """一个轻量实时测点卡。"""

    def __init__(self, title: str, unit: str = "", digits: int = 1, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.unit = str(unit or "")
        self.digits = int(digits)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)

        self.title = QLabel(str(title))
        self.title.setProperty("role", "muted")
        self.value = QLabel("--")
        self.value.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.unit_label = QLabel(self.unit)
        self.unit_label.setProperty("role", "muted")

        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.unit_label)
        self.setMinimumHeight(96)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    def set_value(self, value: Any) -> None:
        number = self._number(value)
        if number is None:
            self.value.setText("--")
            return
        self.value.setText(f"{number:.{self.digits}f}")


class DeviceTile(QFrame):
    """泵/阀等设备实时卡。"""

    def __init__(
        self,
        title: str,
        unit: str = "",
        digits: int = 1,
        run_threshold: Optional[float] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.unit = str(unit or "")
        self.digits = int(digits)
        self.run_threshold = run_threshold

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title = QLabel(str(title))
        self.title.setStyleSheet("font-weight: 600;")
        self.status = StatusPill("无数据", "offline")
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.status)

        self.value = QLabel("--")
        self.value.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.detail = QLabel(self.unit)
        self.detail.setProperty("role", "muted")

        layout.addLayout(header)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        self.setMinimumHeight(108)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    def set_value(self, value: Any) -> None:
        number = self._number(value)
        if number is None:
            self.value.setText("--")
            self.status.set_state("offline", "无数据")
            return

        suffix = f" {self.unit}" if self.unit else ""
        self.value.setText(f"{number:.{self.digits}f}{suffix}")

        if self.run_threshold is None:
            self.status.set_state("normal", "在线")
        elif number > float(self.run_threshold):
            self.status.set_state("normal", "运行")
        else:
            self.status.set_state("offline", "停止")


class ValveTile(DeviceTile):
    def __init__(self, valve: Mapping[str, Any], parent=None):
        super().__init__(
            str(valve.get("display_name") or valve.get("valve_id") or "供浆阀"),
            unit="%",
            digits=1,
            run_threshold=None,
            parent=parent,
        )
        self.min_opening = float(valve.get("min_opening", 0.0))
        self.max_opening = float(valve.get("max_opening", 100.0))
        self.detail.setText(f"范围 {self.min_opening:.0f}–{self.max_opening:.0f} %")

    def set_value(self, value: Any) -> None:
        number = self._number(value)
        if number is None:
            self.value.setText("--")
            self.status.set_state("offline", "无数据")
            return
        self.value.setText(f"{number:.1f} %")
        if number < self.min_opening or number > self.max_opening:
            self.status.set_state("danger", "越界")
        else:
            self.status.set_state("normal", "正常")


class SectionCard(CardFrame):
    """标题 + 动态子控件网格。"""

    def __init__(self, title: str, columns: int = 3, parent=None):
        super().__init__(parent)
        self.columns = max(1, int(columns))
        self.items = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        title_label = QLabel(str(title))
        title_label.setProperty("role", "sectionTitle")
        root.addWidget(title_label)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        root.addLayout(self.grid)

    def add_item(self, widget: QWidget) -> None:
        index = len(self.items)
        row = index // self.columns
        column = index % self.columns
        self.grid.addWidget(widget, row, column)
        self.grid.setColumnStretch(column, 1)
        self.items.append(widget)


class SignalGroupCard(SectionCard):
    def __init__(self, title: str, specs: Iterable[Mapping[str, Any]], parent=None):
        super().__init__(title, columns=2, parent=parent)
        self.bindings: Dict[str, SignalTile] = {}
        for spec in specs:
            column = str(spec.get("column", "")).strip()
            if not column:
                continue
            tile = SignalTile(
                str(spec.get("display_name") or column),
                str(spec.get("unit") or ""),
                int(spec.get("digits", 1)),
            )
            self.add_item(tile)
            self.bindings[column] = tile

    def update_values(self, values: Mapping[str, Any]) -> None:
        for column, tile in self.bindings.items():
            tile.set_value(values.get(column))


class TowerProcessCard(SignalGroupCard):
    def __init__(self, tower: Mapping[str, Any], parent=None):
        self.tower = dict(tower)
        title = str(tower.get("display_name") or tower.get("tower_id") or "吸收塔")
        specs = list(tower.get("monitor_fields", []) or [])
        if not specs:
            ph_column = str(tower.get("ph_column", "")).strip()
            if ph_column:
                specs = [
                    {
                        "column": ph_column,
                        "display_name": "浆液 pH",
                        "unit": "",
                        "digits": 2,
                    }
                ]
        super().__init__(title, specs, parent=parent)


class TowerEquipmentCard(CardFrame):
    """一个塔的可变供浆设备：阀门、流量和供浆泵。"""

    def __init__(self, tower: Mapping[str, Any], parent=None):
        super().__init__(parent)
        self.tower = dict(tower)
        self.valve_bindings: Dict[str, ValveTile] = {}
        self.flow_bindings: Dict[str, SignalTile] = {}
        self.supply_pump_bindings: Dict[str, DeviceTile] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        tower_name = str(tower.get("display_name") or tower.get("tower_id") or "吸收塔")
        title = QLabel(f"{tower_name} · 供浆设备")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        self._build_valves(root)
        self._build_flows(root)
        self._build_supply_pumps(root)

    @staticmethod
    def _group_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600;")
        return label

    @staticmethod
    def _add_grid(root: QVBoxLayout, widgets: list[QWidget], columns: int = 4) -> None:
        if not widgets:
            return
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for index, widget in enumerate(widgets):
            row = index // columns
            column = index % columns
            grid.addWidget(widget, row, column)
            grid.setColumnStretch(column, 1)
        root.addLayout(grid)

    def _build_valves(self, root: QVBoxLayout) -> None:
        valves = list(self.tower.get("valves", []) or [])
        if not valves:
            return
        root.addWidget(self._group_title("供浆阀位"))
        widgets = []
        for valve in valves:
            column = str(valve.get("column", "")).strip()
            if not column:
                continue
            tile = ValveTile(valve)
            widgets.append(tile)
            self.valve_bindings[column] = tile
        self._add_grid(root, widgets)

    def _build_flows(self, root: QVBoxLayout) -> None:
        flows = list(self.tower.get("supply_flows", []) or [])
        if not flows:
            return
        root.addWidget(self._group_title("供浆流量"))
        widgets = []
        for flow in flows:
            column = str(flow.get("column", "")).strip()
            if not column:
                continue
            tile = SignalTile(
                str(flow.get("display_name") or flow.get("flow_id") or column),
                str(flow.get("unit") or "m³/h"),
                int(flow.get("digits", 1)),
            )
            widgets.append(tile)
            self.flow_bindings[column] = tile
        self._add_grid(root, widgets)

    def _monitor_supply_pumps(self) -> list[dict]:
        monitor_pumps = list(self.tower.get("monitor_supply_pumps", []) or [])
        if monitor_pumps:
            return [dict(item) for item in monitor_pumps]

        # 兼容固定频控制拓扑：若没有单独 GUI 配置，直接展示 current_column。
        result = []
        for pump in self.tower.get("supply_pumps", []) or []:
            result.append(
                {
                    "pump_id": pump.get("pump_id"),
                    "display_name": pump.get("display_name") or pump.get("pump_id"),
                    "value_column": pump.get("current_column"),
                    "unit": "A",
                    "digits": 1,
                    "run_threshold": pump.get("run_current_threshold"),
                }
            )
        return result

    def _build_supply_pumps(self, root: QVBoxLayout) -> None:
        pumps = self._monitor_supply_pumps()
        if not pumps:
            return
        root.addWidget(self._group_title("供浆泵"))
        widgets = []
        for pump in pumps:
            column = str(pump.get("value_column", "")).strip()
            if not column:
                continue
            tile = DeviceTile(
                str(pump.get("display_name") or pump.get("pump_id") or "供浆泵"),
                unit=str(pump.get("unit") or ""),
                digits=int(pump.get("digits", 1)),
                run_threshold=pump.get("run_threshold"),
            )
            widgets.append(tile)
            self.supply_pump_bindings[column] = tile
        self._add_grid(root, widgets)

    def update_values(self, values: Mapping[str, Any]) -> None:
        for bindings in (
            self.valve_bindings,
            self.flow_bindings,
            self.supply_pump_bindings,
        ):
            for column, widget in bindings.items():
                widget.set_value(values.get(column))


class TowerCirculationCard(CardFrame):
    """一个塔独占一行的浆液循环泵区域。

    单塔只生成一行；双塔分别生成一级塔、二级塔两行。不同塔的循环泵
    永远不会进入同一个网格。
    """

    def __init__(self, tower: Mapping[str, Any], parent=None):
        super().__init__(parent)
        self.tower = dict(tower)
        self.bindings: Dict[str, DeviceTile] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        tower_name = str(tower.get("display_name") or tower.get("tower_id") or "吸收塔")
        title = QLabel(f"{tower_name} · 浆液循环泵")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        pumps = list(tower.get("circulation_pumps", []) or [])
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        # 一个塔的全部循环泵固定放在同一行；泵数量由 config 决定。
        display_index = 0
        for pump in pumps:
            column = str(pump.get("value_column", "")).strip()
            if not column:
                continue
            tile = DeviceTile(
                str(pump.get("display_name") or pump.get("pump_id") or "循环泵"),
                unit=str(pump.get("unit") or ""),
                digits=int(pump.get("digits", 1)),
                run_threshold=pump.get("run_threshold"),
            )
            grid.addWidget(tile, 0, display_index)
            grid.setColumnStretch(display_index, 1)
            self.bindings[column] = tile
            display_index += 1

        root.addLayout(grid)

    def update_values(self, values: Mapping[str, Any]) -> None:
        for column, tile in self.bindings.items():
            tile.set_value(values.get(column))


class DataHealthCard(CardFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)

        title = QLabel("实时数据健康")
        title.setProperty("role", "sectionTitle")
        root.addWidget(title)

        self.connection = KeyValueRow("DCS 通讯", "--")
        self.latest_time = KeyValueRow("最新数据时间", "--")
        self.delay = KeyValueRow("数据延迟", "--")
        self.expired = KeyValueRow("数据状态", "--")
        self.jym = KeyValueRow("校验码 jym", "--")
        self.source = KeyValueRow("页面数据源", "--")
        for row in (
            self.connection,
            self.latest_time,
            self.delay,
            self.expired,
            self.jym,
            self.source,
        ):
            root.addWidget(row)

    def update_data(self, data: Mapping[str, Any]) -> None:
        connection = data.get("connection_status")
        if connection is None:
            connection_text = "未知"
        else:
            connection_text = "正常" if bool(connection) else "中断"
        self.connection.set_value(connection_text)

        date_value = data.get("date")
        self.latest_time.set_value("--" if date_value in (None, "") else str(date_value))

        age = data.get("data_age_seconds")
        try:
            age_text = f"{float(age):.1f} s"
        except (TypeError, ValueError, OverflowError):
            age_text = "--"
        self.delay.set_value(age_text)

        self.expired.set_value("过期" if bool(data.get("data_expired")) else "正常")
        jym = data.get("jym")
        self.jym.set_value("--" if jym in (None, "") else str(jym))
        self.source.set_value(str(data.get("ui_data_source", "--")))


class RealtimePage(QWidget):
    """配置驱动的实时工艺/设备监控页。"""

    def __init__(self, plant_config: Optional[Mapping[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.plant = dict(plant_config or PLANT_CONFIG)
        self.signal_groups = []
        self.tower_process_cards = []
        self.tower_equipment_cards = []
        self.tower_circulation_cards = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        title = QLabel("实时工艺与设备状态")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; padding: 6px;")
        root.addWidget(title)

        monitor = self.plant.get("realtime_monitor", {}) or {}
        inlet = SignalGroupCard("烟气入口", monitor.get("inlet_signals", []) or [])
        outlet = SignalGroupCard("净烟气出口", monitor.get("outlet_signals", []) or [])
        self.signal_groups.extend([inlet, outlet])

        enabled_towers = [
            tower
            for tower in self.plant.get("towers", []) or []
            if tower.get("enabled", True)
        ]

        process_grid = QGridLayout()
        process_grid.setHorizontalSpacing(12)
        process_grid.setVerticalSpacing(12)

        process_cards: list[QWidget] = [inlet]
        for tower in enabled_towers:
            card = TowerProcessCard(tower)
            process_cards.append(card)
            self.tower_process_cards.append(card)
        process_cards.append(outlet)

        # 最多三列，塔数量变化时自动换行。
        for index, card in enumerate(process_cards):
            row = index // 3
            column = index % 3
            process_grid.addWidget(card, row, column)
            process_grid.setColumnStretch(column, 1)
        root.addLayout(process_grid)

        # 供浆设备仍然按塔分块。
        for tower in enabled_towers:
            equipment = TowerEquipmentCard(tower)
            self.tower_equipment_cards.append(equipment)
            root.addWidget(equipment)

        # 循环泵单独按塔分行：一级塔一行、二级塔一行；单塔只出现一行。
        for tower in enabled_towers:
            if not list(tower.get("circulation_pumps", []) or []):
                continue
            circulation = TowerCirculationCard(tower)
            self.tower_circulation_cards.append(circulation)
            root.addWidget(circulation)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        auxiliary = SignalGroupCard(
            "公共辅助系统",
            monitor.get("auxiliary_signals", []) or [],
        )
        self.signal_groups.append(auxiliary)
        self.health = DataHealthCard()
        bottom.addWidget(auxiliary, 1)
        bottom.addWidget(self.health, 1)
        root.addLayout(bottom)
        root.addStretch(1)

    def update_data(self, data: Mapping[str, Any]) -> None:
        values = data.get("realtime_values")
        if not isinstance(values, Mapping):
            values = {}

        for group in self.signal_groups:
            group.update_values(values)
        for card in self.tower_process_cards:
            card.update_values(values)
        for card in self.tower_equipment_cards:
            card.update_values(values)
        for card in self.tower_circulation_cards:
            card.update_values(values)
        self.health.update_data(data)
