from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QBrush, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


POSITION_COLORS = {
    "GK": "#7dd3fc",
    "DF": "#86efac",
    "MF": "#facc15",
    "FW": "#fb7185",
}

STATUS_COLORS = {
    "一级联赛": "#8ec5ff",
    "次级联赛": "#9ae6b4",
    "杯赛": "#f9c74f",
    "待处理": "#fca5a5",
    "已完成": "#a7f3d0",
    "进行中": "#93c5fd",
    "未启用": "#94a3b8",
}

ZONE_BACKGROUNDS = {
    "champion": "#173d32",
    "promotion": "#153a30",
    "playoff": "#3a3217",
    "relegation": "#3b1f2a",
    "current": "#18355a",
}


class CardFrame(QFrame):
    def __init__(self, title: str, subtitle: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardFrame")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("titleLabel")
        title_label.setProperty("class", "cardTitle")
        title_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("subtitleLabel")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

        self.body_layout = layout


class TrendSparkline(QWidget):
    def __init__(self, title: str, line_color: str = "#7dd3fc", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.line_color = QColor(line_color)
        self.values: list[float] = []
        self.labels: list[str] = []
        self.setMinimumHeight(120)

    def sizeHint(self) -> QSize:
        return QSize(360, 130)

    def set_points(self, values: list[float], labels: list[str] | None = None) -> None:
        self.values = values
        self.labels = labels or []
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(10, 10, -10, -10)
        painter.setPen(QPen(QColor("#263a55"), 1))
        painter.setBrush(QColor("#101d2e"))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QColor("#8da6c4"))
        painter.drawText(rect.adjusted(12, 8, -12, -8), Qt.AlignTop | Qt.AlignLeft, self.title)
        chart = rect.adjusted(18, 34, -18, -18)
        painter.setPen(QPen(QColor("#23344d"), 1))
        for index in range(3):
            y = chart.top() + (chart.height() * index / 2)
            painter.drawLine(chart.left(), int(y), chart.right(), int(y))
        if not self.values:
            painter.setPen(QColor("#64748b"))
            painter.drawText(chart, Qt.AlignCenter, "暂无趋势数据")
            return
        min_value = min(self.values)
        max_value = max(self.values)
        span = max(max_value - min_value, 0.01)
        points: list[QPointF] = []
        for index, value in enumerate(self.values):
            x = chart.left() if len(self.values) == 1 else chart.left() + chart.width() * index / (len(self.values) - 1)
            y = chart.bottom() - chart.height() * (value - min_value) / span
            points.append(QPointF(x, y))
        painter.setPen(QPen(self.line_color, 3))
        for start, end in zip(points, points[1:]):
            painter.drawLine(start, end)
        painter.setBrush(self.line_color)
        painter.setPen(Qt.NoPen)
        for point in points:
            painter.drawEllipse(point, 4, 4)
        painter.setPen(QColor("#dfe9f7"))
        painter.drawText(chart.adjusted(0, -4, 0, 0), Qt.AlignTop | Qt.AlignRight, f"{max_value:.2f}")
        painter.drawText(chart.adjusted(0, 0, 0, 4), Qt.AlignBottom | Qt.AlignLeft, f"{min_value:.2f}")


def build_metric_card(title: str, value: str, note: str) -> CardFrame:
    card = CardFrame(title)
    value_label = QLabel(value)
    value_label.setStyleSheet("font-size: 28px; font-weight: 800; color: #f8fbff;")
    note_label = QLabel(note)
    note_label.setObjectName("subtitleLabel")
    card.body_layout.addWidget(value_label)
    card.body_layout.addWidget(note_label)
    card.body_layout.addStretch(1)
    card.value_label = value_label  # type: ignore[attr-defined]
    card.note_label = note_label  # type: ignore[attr-defined]
    return card


def section_header(text: str, note: str | None = None) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(3)
    title = QLabel(text)
    title.setStyleSheet("font-size: 18px; font-weight: 900; color: #f8fbff; letter-spacing: 0.5px;")
    layout.addWidget(title)
    if note:
        subtitle = QLabel(note)
        subtitle.setObjectName("subtitleLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
    return wrapper


def make_badge(text: str, color: str | None = None) -> QLabel:
    badge = QLabel(text)
    badge.setObjectName("badgeLabel")
    badge.setAlignment(Qt.AlignCenter)
    badge.setStyleSheet(f"background: {color or STATUS_COLORS.get(text, '#94a3b8')};")
    return badge


def money_text(value: float | None) -> str:
    return "待结算" if value is None else f"{value:.2f}M"


def player_stat_line(row) -> str:
    if row.player.position == "GK":
        return f"出 {row.appearances} | 扑 {row.successful_saves} | 零 {row.clean_sheets}"
    return (
        f"出 {row.appearances} | 进 {row.goals} | 助 {row.assists} | "
        f"创 {row.chances_created} | 防 {row.successful_defenses}"
    )


def make_two_column_info(rows: Iterable[tuple[str, str]]) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for label, value in rows:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)
        label_widget = QLabel(label)
        label_widget.setObjectName("subtitleLabel")
        value_widget = QLabel(value)
        value_widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row_layout.addWidget(label_widget, 1)
        row_layout.addWidget(value_widget, 2)
        layout.addWidget(row)
    layout.addStretch(1)
    return wrapper


def setup_table(table: QTableWidget, headers: list[str]) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setWordWrap(False)
    table.setSortingEnabled(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.setShowGrid(False)


def set_table_row(table: QTableWidget, row_index: int, values: list[str]) -> None:
    table.insertRow(row_index)
    for column, value in enumerate(values):
        item = QTableWidgetItem(value)
        item.setTextAlignment(Qt.AlignVCenter | (Qt.AlignRight if _looks_numeric(value) else Qt.AlignLeft))
        table.setItem(row_index, column, item)


def color_position_items(table: QTableWidget, position_column: int) -> None:
    for row in range(table.rowCount()):
        item = table.item(row, position_column)
        if item is None:
            continue
        color = POSITION_COLORS.get(item.text())
        if color:
            item.setForeground(QBrush(QColor(color)))
            item.setTextAlignment(Qt.AlignCenter)


def shade_row(table: QTableWidget, row_index: int, color: str) -> None:
    brush = QBrush(QColor(color))
    for column in range(table.columnCount()):
        item = table.item(row_index, column)
        if item is not None:
            item.setBackground(brush)


def resize_table(table: QTableWidget) -> None:
    table.resizeColumnsToContents()
    table.resizeRowsToContents()


def _looks_numeric(value: str) -> bool:
    stripped = value.replace("M", "").replace("%", "").replace("+", "").replace("-", "").replace(".", "")
    return bool(stripped) and stripped.isdigit()
