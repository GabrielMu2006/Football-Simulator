"""TeamCrestTextDelegate：表格球队名列的统一"队徽 + 文本"渲染。

抽自 teams_page 的私有 delegate，供比赛列表 / 球员目录 / 周报等全站复用，
保证队徽尺寸与绘制一致（默认 28px，可传入 crest_size 覆盖）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from football_simulator.ui_v2.components import TEXT_COLOR
from football_simulator.ui_v2.components.team_crest import draw_team_crest


class TeamCrestTextDelegate(QStyledItemDelegate):
    """球队名列：队徽 + 队名文本，保持基础选中/悬停行为，行激活由表格处理。"""

    def __init__(
        self,
        parent: Optional[object] = None,
        crest_size: int = 28,
        color: str = TEXT_COLOR,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._crest_size = crest_size
        self._color = QColor(color)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text or text == "—":
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = opt.rect.adjusted(8, 4, -8, -4)
        crest_size = min(min(rect.height(), self._crest_size), 32)
        crest_rect = QRect(
            rect.left(),
            rect.top() + (rect.height() - crest_size) // 2,
            crest_size,
            crest_size,
        )
        draw_team_crest(painter, crest_rect, text, size=crest_size)
        painter.setPen(self._color)
        painter.setFont(opt.font)
        text_rect = QRect(
            rect.left() + crest_size + 8,
            rect.top(),
            max(0, rect.width() - crest_size - 8),
            rect.height(),
        )
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()
