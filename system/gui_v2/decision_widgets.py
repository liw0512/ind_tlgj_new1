from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from .widgets import CardFrame, KeyValueRow, StatusPill


class OperatorActionCard(CardFrame):
    """首页操作员版供浆建议卡。

    机器标识仍由后端/Adapter 以 ``*_code`` 保留；本卡只显示中文文本，并使用
    原始 code 决定状态颜色，避免为了界面中文化改变算法协议。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("智能供浆建议")
        title.setProperty("role", "sectionTitle")
        self.mode = StatusPill("等待", "warning")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.mode)
        layout.addLayout(header)

        self.source = KeyValueRow("经验来源", "无可用经验")
        self.action = KeyValueRow("推荐动作", "保持当前供浆")
        self.magnitude = KeyValueRow("动作强度", "保持")
        self.delta = KeyValueRow("建议调整", "0.0 %")
        self.state = KeyValueRow("控制状态", "等待")

        self.reason_title = QLabel("决策说明")
        self.reason_title.setStyleSheet("font-weight: 600;")
        self.reason = QLabel("等待模型在线结果。")
        self.reason.setWordWrap(True)
        self.reason.setProperty("role", "muted")
        self.reason.setMinimumHeight(34)

        for row in (
            self.source,
            self.action,
            self.magnitude,
            self.delta,
            self.state,
        ):
            layout.addWidget(row)
        layout.addSpacing(3)
        layout.addWidget(self.reason_title)
        layout.addWidget(self.reason)
        layout.addStretch(1)
        self.setMinimumHeight(225)

    def update_values(
        self,
        *,
        source: str,
        action: str,
        magnitude: str,
        delta: str,
        state: str,
        mode: str,
        reason: str,
        state_code: Optional[str] = None,
        mode_code: Optional[str] = None,
    ) -> None:
        self.source.set_value(source)
        self.action.set_value(action)
        self.magnitude.set_value(magnitude)
        self.delta.set_value(delta)
        self.state.set_value(state)
        self.reason.setText(reason or "当前暂无需要向操作员提示的特殊决策原因。")

        raw_mode = str(mode_code if mode_code is not None else mode).upper()
        raw_state = str(state_code if state_code is not None else state).upper()
        if "BLOCKED" in raw_mode or "BLOCKED" in raw_state:
            pill_state = "danger"
        elif "FAST" in raw_mode or raw_mode in {
            "WAITING",
            "INITIALIZING",
            "MODEL_TRANSITION",
            "TARGET_TRANSITION",
            "CONDITION_TRANSITION",
        }:
            pill_state = "warning"
        else:
            pill_state = "normal"
        self.mode.set_state(pill_state, mode or "等待")
