"""macOS presentation refinements; Windows keeps its existing UI behavior."""

from __future__ import annotations

import sys

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLayout, QPushButton,
    QSizePolicy, QStyle, QTabBar, QVBoxLayout, QWidget,
    QStyledItemDelegate, QStyleOption, QStyleOptionViewItem,
)

from football_simulator.ui import tokens as t


def enabled() -> bool:
    return sys.platform == "darwin"


class AdaptivePanels(QWidget):
    """Two existing panels reflow without rebuilding their controls or state."""

    def __init__(self, first: QWidget, second: QWidget, *, threshold: int = 1100) -> None:
        super().__init__()
        self.setObjectName("HonorsPanels")
        self._panels = (first, second)
        self._threshold = threshold
        self._wide = None
        self._grid = QGridLayout(self)
        self._grid.setSizeConstraint(QLayout.SetNoConstraint)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(t.S16)
        self._reflow(False)

    def _reflow(self, wide: bool) -> None:
        if wide == self._wide:
            return
        self._wide = wide
        for panel in self._panels:
            self._grid.removeWidget(panel)
        self._grid.setColumnStretch(0, 2 if wide else 1)
        self._grid.setColumnStretch(1, 3 if wide else 0)
        self._grid.addWidget(self._panels[0], 0, 0, Qt.AlignTop)
        self._grid.addWidget(self._panels[1], 0 if wide else 1, 1 if wide else 0, Qt.AlignTop)
        self.updateGeometry()

    def minimumSizeHint(self) -> QSize:
        # A wide layout must not make its own breakpoint impossible to leave.
        # Only the single-panel minimum constrains the page's width.
        width = max(panel.minimumSizeHint().width() for panel in self._panels)
        return QSize(width, self._grid.minimumSize().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow(self.width() >= self._threshold)


def default_player_scope() -> str:
    return "real" if enabled() else "all"


def player_scope_box(scope: str, on_change) -> QComboBox:
    box = QComboBox()
    box.setObjectName("PlayerScope")
    box.setAccessibleName("球员显示范围")
    box.addItem("仅真实球员", "real")
    box.addItem("全部球员", "all")
    box.setCurrentIndex(max(0, box.findData(scope)))
    box.currentIndexChanged.connect(lambda _: on_change(box.currentData()))
    return box


def visible_players(rows, world: dict, scope: str):
    if scope != "real":
        return list(rows)
    return [row for row in rows if row.get("is_real", world["players"].get(row.get("player_id"), {}).get("is_real", False))]


def section_tabs(items, selected: str, on_change) -> QTabBar:
    tabs = QTabBar()
    tabs.setObjectName("DetailSections")
    tabs.setAccessibleName("详情内容")
    tabs.setExpanding(False)
    tabs.setDrawBase(False)
    tabs.setUsesScrollButtons(True)
    for title, key in items:
        index = tabs.addTab(title)
        tabs.setTabData(index, key)
        if key == selected:
            tabs.setCurrentIndex(index)
    tabs.currentChanged.connect(lambda index: on_change(tabs.tabData(index)))
    return tabs


class Pagination(QWidget):
    """Shared bounded paging; mutable UI state survives refresh and navigation."""

    changed = Signal()

    def __init__(self, total: int, state: dict, *, default_size: int = 10) -> None:
        super().__init__()
        self.setObjectName("Pagination")
        self.total, self.state = total, state
        state.setdefault("page", 1)
        state.setdefault("page_size", default_size)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        self.summary = QLabel()
        self.summary.setObjectName("Meta")
        self.previous = QPushButton("上一页")
        self.previous.setObjectName("PagerPrevious")
        self.following = QPushButton("下一页")
        self.following.setObjectName("PagerNext")
        self.sizes = QComboBox()
        self.sizes.setAccessibleName("每页记录数")
        for size in sorted({default_size, 10, 20, 30}):
            self.sizes.addItem(f"每页 {size} 条", size)
        self.sizes.setCurrentIndex(max(0, self.sizes.findData(state["page_size"])))
        layout.addWidget(self.summary, 1)
        layout.addWidget(self.sizes)
        layout.addWidget(self.previous)
        layout.addWidget(self.following)
        self.previous.clicked.connect(lambda: self._move(-1))
        self.following.clicked.connect(lambda: self._move(1))
        self.sizes.currentIndexChanged.connect(self._resize_page)
        self._sync()

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.state["page_size"] - 1) // self.state["page_size"])

    def _sync(self) -> None:
        self.state["page"] = max(1, min(int(self.state["page"]), self.pages))
        page, size = self.state["page"], self.state["page_size"]
        first = (page - 1) * size + 1 if self.total else 0
        last = min(page * size, self.total)
        self.summary.setText(f"{first}–{last} / {self.total} 条 · 第 {page}/{self.pages} 页")
        self.previous.setEnabled(page > 1)
        self.following.setEnabled(page < self.pages)

    def _move(self, delta: int) -> None:
        self.state["page"] = max(1, min(self.pages, self.state["page"] + delta))
        self._sync()
        self.changed.emit()

    def _resize_page(self, _index) -> None:
        self.state.update(page=1, page_size=int(self.sizes.currentData()))
        self._sync()
        self.changed.emit()

    def rows(self, rows):
        start = (self.state["page"] - 1) * self.state["page_size"]
        return rows[start:start + self.state["page_size"]]


def paginate_table(table, rows, layout, state: dict, *, default_size: int = 10) -> Pagination:
    pager = Pagination(len(rows), state, default_size=default_size)

    def render() -> None:
        visible = pager.rows(rows)
        table.set_rows(visible)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(table.horizontalHeader().height() + table.verticalHeader().defaultSectionSize() * max(1, len(visible)) + 4)

    pager.changed.connect(render)
    render()
    layout.addWidget(table)
    layout.addWidget(pager)
    return pager


def paginate_widgets(rows, layout, state: dict, build, *, default_size: int = 4) -> Pagination:
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    body_layout.setSpacing(t.S10)
    pager = Pagination(len(rows), state, default_size=default_size)

    def render() -> None:
        while body_layout.count():
            widget = body_layout.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for row in pager.rows(rows):
            body_layout.addWidget(build(row))

    pager.changed.connect(render)
    render()
    layout.addWidget(body)
    layout.addWidget(pager)
    return pager


def with_team_clubs(world, rows, *, team_key: str = "team_id", club_key: str = "club"):
    """Copy rows and fill a club-name field from the row's explicit team id."""
    teams = (world or {}).get("teams") or {}
    result = []
    for row in rows:
        team_id = row.get(team_key)
        club = row.get(club_key) or (teams.get(team_id) or {}).get("club_en", "")
        result.append({**row, club_key: club})
    return result


def team_column(key: str = "team", label_text: str = "球队", *, size: int = 24, **extra):
    """The one team-name column shape used by every ranking table on macOS."""
    column = {"key": key, "label": label_text, "stretch": True, "kind": "link", "icon_key": "club", "icon_size": size}
    column.update(extra)
    return column


def standings_band(league: str, rank: int):
    if league == "premier" and 18 <= rank <= 20:
        return t.DANGER_BG, "降级区"
    if league == "second" and 1 <= rank <= 2:
        return t.SUCCESS_BG, "直接升级区"
    if league == "second" and 3 <= rank <= 6:
        return t.WARNING_BG, "升级附加赛区"
    return None, ""


def standings_legend(league: str) -> str:
    if league == "premier":
        return "分区线：第 4 / 8 / 12 / 16 名后 · 红色：第 18–20 名降级区"
    return "绿色：第 1–2 名直接升级 · 金色：第 3–6 名升级附加赛"


def team_result(dto: dict, team_id: str):
    """Per-match result, including extra time; aggregate advancement is separate."""
    if dto.get("status") != "completed" or team_id not in (dto["home_team_id"], dto["away_team_id"]):
        return None
    home = team_id == dto["home_team_id"]
    own, other = (dto["total_home"], dto["total_away"]) if home else (dto["total_away"], dto["total_home"])
    penalty = own == other and dto.get("home_pk") is not None and dto.get("away_pk") is not None
    if penalty:
        own, other = (dto["home_pk"], dto["away_pk"]) if home else (dto["away_pk"], dto["home_pk"])
    if own > other:
        return "win", "点球胜" if penalty else "胜", t.SUCCESS, t.SUCCESS_BG
    if own < other:
        return "loss", "点球负" if penalty else "负", t.DANGER, t.DANGER_BG
    return "draw", "平", t.NEUTRAL, t.RESULT_DRAW_BG


class LeagueBandDelegate(QStyledItemDelegate):
    """Paint zone backgrounds explicitly: application QSS masks BackgroundRole."""

    def paint(self, painter, option, index) -> None:
        table = self.parent()
        rank = int(table._rows[index.row()].get("rank", 0))
        color, _ = standings_band(table._league_style, rank)
        if not color:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.State_Selected)
        painter.save()
        painter.setClipRect(opt.rect)
        painter.fillRect(opt.rect, QColor(t.TABLE_SELECT if selected else color))
        painter.setFont(opt.font)
        painter.setPen(QColor(t.TEXT_STRONG) if selected else opt.palette.color(QPalette.Text))
        rect = opt.rect.adjusted(6, 0, -6, 0)
        if not opt.icon.isNull():
            edge = min(table.iconSize().width(), rect.height() - 4)
            opt.icon.paint(painter, QRect(rect.left(), rect.center().y() - edge // 2, edge, edge))
            rect.adjust(edge + 6, 0, 0, 0)
        painter.drawText(rect, opt.displayAlignment, opt.fontMetrics.elidedText(opt.text, Qt.ElideRight, rect.width()))
        if opt.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(t.ACCENT), 1, Qt.DotLine))
            painter.drawRect(opt.rect.adjusted(1, 1, -2, -2))
        painter.restore()


class ZoneRowDelegate(QStyledItemDelegate):
    """Paint a group-table zone row (background + left band), keeping text legible."""

    def paint(self, painter, option, index) -> None:
        table = self.parent()
        zone = (getattr(table, "_zone_rows", []) or [])
        tint = zone[index.row()] if index.row() < len(zone) else None
        if not tint:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.State_Selected)
        painter.save()
        painter.setClipRect(opt.rect)
        painter.fillRect(opt.rect, QColor(t.TABLE_SELECT if selected else tint["bg"]))
        painter.setFont(opt.font)
        painter.setPen(QColor(t.TEXT_STRONG) if selected else opt.palette.color(QPalette.Text))
        rect = opt.rect.adjusted(6, 0, -6, 0)
        if not opt.icon.isNull():
            edge = min(table.iconSize().width(), rect.height() - 4)
            opt.icon.paint(painter, QRect(rect.left(), rect.center().y() - edge // 2, edge, edge))
            rect.adjust(edge + 6, 0, 0, 0)
        text = opt.displayAlignment and opt.fontMetrics.elidedText(opt.text, Qt.ElideRight, rect.width()) or opt.text
        painter.drawText(rect, opt.displayAlignment, text)
        if opt.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(t.ACCENT), 1, Qt.DotLine))
            painter.drawRect(opt.rect.adjusted(1, 1, -2, -2))
        painter.restore()

    def paint_band(self, table, painter) -> None:
        for index, tint in enumerate(getattr(table, "_zone_rows", []) or []):
            if not tint:
                continue
            top = table.rowViewportPosition(index)
            height = table.rowHeight(index)
            painter.fillRect(0, top, 3, height, QColor(tint["border"]))


class NavigationCard(QAbstractButton):
    """One native button for the entire card, including its decorative children."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("CompetitionCard")
        self.setAccessibleName(f"查看赛事：{title}")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.setMinimumHeight(180)
        self.setStyleSheet(f"""
            QAbstractButton#CompetitionCard {{
                background: {t.CARD};
                border: 2px solid {t.BORDER};
                border-radius: {t.RADIUS_MD}px;
            }}
            QAbstractButton#CompetitionCard:hover {{ border-color: {t.PRIMARY_HOVER}; }}
            QAbstractButton#CompetitionCard:focus {{ border-color: {t.ACCENT}; }}
            QAbstractButton#CompetitionCard:pressed {{ background: {t.CARD_ALT}; }}
            QLabel#CardAction {{ color: {t.LINK}; font-weight: 700; }}
        """)

    def sizeHint(self) -> QSize:
        return self.layout().totalSizeHint() if self.layout() is not None else QSize(320, 180)

    def minimumSizeHint(self) -> QSize:
        return self.layout().totalMinimumSize() if self.layout() is not None else QSize(200, 180)

    def paintEvent(self, event) -> None:
        option = QStyleOption()
        option.initFrom(self)
        if self.isDown():
            option.state |= QStyle.State_Sunken
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.isEnabled() and not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class MatchTeamDelegate(QStyledItemDelegate):
    """Mirror team names and crests around the separate centered score column."""

    def __init__(self, side: str, parent=None) -> None:
        super().__init__(parent)
        self.side = side

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, icon = opt.text, QIcon(opt.icon)
        opt.text, opt.icon = "", QIcon()
        opt.features &= ~(QStyleOptionViewItem.HasDisplay | QStyleOptionViewItem.HasDecoration)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        painter.save()
        painter.setClipRect(opt.rect)
        painter.setFont(opt.font)
        rect = opt.rect.adjusted(10, 4, -10, -4)
        edge = min(32, rect.height())
        if self.side == "home":
            crest = QRect(rect.right() - edge + 1, rect.top() + (rect.height() - edge) // 2, edge, edge)
            text_rect = rect.adjusted(0, 0, -(edge + 8), 0)
            alignment = Qt.AlignRight | Qt.AlignVCenter
        else:
            crest = QRect(rect.left(), rect.top() + (rect.height() - edge) // 2, edge, edge)
            text_rect = rect.adjusted(edge + 8, 0, 0, 0)
            alignment = Qt.AlignLeft | Qt.AlignVCenter
        selected = bool(opt.state & QStyle.State_Selected)
        color = opt.palette.color(QPalette.HighlightedText if selected else QPalette.Text)
        painter.setPen(color)
        icon.paint(painter, crest, Qt.AlignCenter, QIcon.Normal)
        painter.drawText(text_rect, alignment, opt.fontMetrics.elidedText(text, Qt.ElideRight, max(0, text_rect.width())))
        painter.restore()


class EmptyStateText(QLabel):
    """Reserve the actual wrapped text height instead of QLabel's one-line minimum."""

    def __init__(self, text: str, object_name: str, *, wrap: bool = False) -> None:
        super().__init__(text)
        self.setObjectName(object_name)
        self.setWordWrap(wrap)
        self.setAlignment(Qt.AlignCenter)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.wordWrap():
            width = min(560, max(hint.width(), self.fontMetrics().horizontalAdvance(self.text()) + 2))
            return QSize(width, self.heightForWidth(width))
        return hint

    def _fit_height(self) -> None:
        height = self.heightForWidth(self.width()) if self.wordWrap() else self.sizeHint().height()
        height = max(self.fontMetrics().height(), height)
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_height()
