from __future__ import annotations

from typing import Any, Mapping, Sequence

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen

from .history_page import HistoryLineChart, _to_datetime


def _series_has_value(series: Sequence[Mapping[str, Any]]) -> bool:
    for item in series:
        for value in item.get("values", []):
            if value is not None:
                return True
    return False


def _draw_partial_gap_regions(chart: HistoryLineChart, painter: QPainter, plot: QRectF) -> None:
    """只渲染共享的数据库时间缺口；单测点缺值不做整图灰色遮罩。"""
    if chart._range_start is None or chart._range_end is None:
        return

    fill = QColor("#64748b")
    fill.setAlpha(38)
    text_color = QColor("#aebbd0")

    for gap in chart._gaps:
        gap_start = _to_datetime(gap.get("start"))
        gap_end = _to_datetime(gap.get("end"))
        if gap_start is None or gap_end is None:
            continue

        clipped_start = max(chart._range_start, gap_start)
        clipped_end = min(chart._range_end, gap_end)
        if clipped_end <= clipped_start:
            continue

        x1 = chart._x(clipped_start, plot)
        x2 = chart._x(clipped_end, plot)
        region = QRectF(x1, plot.top(), max(1.0, x2 - x1), plot.height())
        painter.fillRect(region, fill)

        # 窄缺口只保留灰色带；宽缺口才显示一次文字，避免挤成一团。
        if region.width() >= 115:
            painter.setPen(text_color)
            painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(region, Qt.AlignCenter, "无历史数据")


def _draw_empty_state(
    painter: QPainter,
    plot: QRectF,
    *,
    title: str,
    subtitle: str,
) -> None:
    overlay = QColor("#27364b")
    overlay.setAlpha(32)
    painter.fillRect(plot, overlay)

    center_y = plot.center().y()
    painter.setPen(QColor("#c8d6ea"))
    painter.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
    painter.drawText(
        QRectF(plot.left(), center_y - 25, plot.width(), 24),
        Qt.AlignHCenter | Qt.AlignVCenter,
        title,
    )

    painter.setPen(QColor("#7f93b0"))
    painter.setFont(QFont("Microsoft YaHei", 9))
    painter.drawText(
        QRectF(plot.left(), center_y + 2, plot.width(), 22),
        Qt.AlignHCenter | Qt.AlignVCenter,
        subtitle,
    )


def _paint_history_chart(self: HistoryLineChart, event) -> None:  # noqa: N802
    painter = QPainter(self)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setFont(QFont("Microsoft YaHei", 9))
    text_color = QColor("#9fb4d4")

    plot = QRectF(self.rect()).adjusted(68, 34, -78, -38)
    if plot.width() <= 20 or plot.height() <= 20:
        return

    if self._range_start is None or self._range_end is None or self._range_end <= self._range_start:
        _draw_empty_state(
            painter,
            plot,
            title="暂无可显示的历史范围",
            subtitle="请选择有效的开始和结束时间",
        )
        return

    left_series = self._visible_series(self._left_series)
    right_series = self._visible_series(self._right_series)
    visible_series = [*left_series, *right_series]
    left_visible = bool(left_series)
    right_visible = bool(right_series)
    has_records = bool(self._times)
    has_visible_values = _series_has_value(visible_series)

    left_low, left_high = self._left_range
    right_low, right_high = self._right_range
    grid_pen = QPen(QColor("#23344d"), 1)

    # 轴标题保留，方便知道当前图表在看什么；无数据时不画无意义的 Y 轴数值。
    if left_visible and self._left_unit:
        painter.setPen(text_color)
        painter.drawText(
            QRectF(plot.left(), 4, max(160.0, plot.width() * 0.45), 22),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"左轴：{self._left_unit}",
        )
    if right_visible and self._right_unit:
        painter.setPen(text_color)
        painter.drawText(
            QRectF(plot.center().x(), 4, max(160.0, plot.width() * 0.5), 22),
            Qt.AlignRight | Qt.AlignVCenter,
            f"右轴：{self._right_unit}",
        )

    for index in range(6):
        ratio = index / 5.0
        y = plot.top() + ratio * plot.height()
        painter.setPen(grid_pen)
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        if has_visible_values:
            painter.setPen(text_color)
            if left_visible:
                value = left_high - ratio * (left_high - left_low)
                painter.drawText(
                    QRectF(0, y - 9, plot.left() - 8, 18),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{value:.1f}",
                )
            if right_visible:
                value = right_high - ratio * (right_high - right_low)
                painter.drawText(
                    QRectF(plot.right() + 8, y - 9, self.width() - plot.right() - 8, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    f"{value:.1f}",
                )

    span = self._range_end - self._range_start
    tick_count = 5 if self.width() < 950 else 7
    for index in range(tick_count):
        ratio = index / max(1, tick_count - 1)
        x = plot.left() + ratio * plot.width()
        painter.setPen(grid_pen)
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        stamp = self._range_start + span * ratio
        if span.total_seconds() > 86400:
            label = stamp.strftime("%m-%d %H:%M")
            label_width = 112
        else:
            label = stamp.strftime("%H:%M")
            label_width = 84
        painter.setPen(text_color)
        painter.drawText(
            QRectF(x - label_width / 2.0, plot.bottom() + 7, label_width, 22),
            Qt.AlignHCenter | Qt.AlignTop,
            label,
        )

    # 1) 整个查询范围没有数据库记录：只显示一个空状态，不再叠加灰色缺口文字。
    if not has_records:
        _draw_empty_state(
            painter,
            plot,
            title="当前时间范围无历史数据",
            subtitle="数据库未记录；仅凭数据空窗不判断为机组停机",
        )
        return

    # 2) 数据库有记录，但当前勾选的测点都没有有效值：这是测点级缺失，不是整段未记录。
    if not has_visible_values:
        _draw_empty_state(
            painter,
            plot,
            title="所选测点暂无有效数据",
            subtitle="数据库存在记录，可尝试勾选其他历史曲线",
        )
        return

    # 3) 部分时间段所有历史记录缺失：只在对应时间范围画灰色带。
    _draw_partial_gap_regions(self, painter, plot)

    # 4) 单独某条曲线缺值：_draw_series 遇到 None 自动断线，不画整段灰色背景。
    self._draw_series(painter, plot, left_series, left_low, left_high)
    self._draw_series(painter, plot, right_series, right_low, right_high)

    if self._cursor_time is not None and self._range_start <= self._cursor_time <= self._range_end:
        x = self._x(self._cursor_time, plot)
        painter.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))


def apply_history_gap_display() -> None:
    """安装统一的历史缺失数据展示规则。"""
    if getattr(HistoryLineChart, "_gap_display_installed", False):
        return
    HistoryLineChart.paintEvent = _paint_history_chart
    HistoryLineChart._gap_display_installed = True
