"""原生 Qt 组件：卡片、页头、数据表、队徽、比分焦点区、空状态。

这些组件只使用 tokens/theme，页面不直接写颜色。队徽复用既有 40 队徽资源，
文字与比分始终由原生 QLabel 渲染，不烘焙进图片。
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, List, Optional

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QTabBar,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOption,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from football_simulator.ui import tokens as t
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.team_crest import TeamCrest, crest_pixmap
from football_simulator.ui.theme import status_variant


def label(text: str = "", object_name: str = "", *, wrap: bool = False, selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(wrap)
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse if selectable else Qt.NoTextInteraction)
    return widget


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def card(*, alt: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("CardAlt" if alt else "Card")
    return frame


class Badge(QLabel):
    def __init__(self, text: str, variant: str = "neutral") -> None:
        super().__init__(text)
        self.setObjectName("Badge")
        self.set_variant(variant)

    def set_variant(self, variant: str) -> None:
        self.setProperty("variant", variant)
        self.style().unpolish(self)
        self.style().polish(self)


class EntityLink(QPushButton):
    """可键盘激活的实体链接；携带稳定 ID，不依赖显示名。"""

    def __init__(self, text: str, kind: str, entity_id: str, *, compact: bool = False) -> None:
        super().__init__(text)
        self.setObjectName("Link")
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.kind = kind
        self.entity_id = entity_id
        self.setAccessibleName(f"{text}（{kind}）")
        if compact:
            font = self.font()
            font.setPointSizeF(max(8.5, font.pointSizeF() - 1))
            self.setFont(font)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.isEnabled() and not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class PageScroll(QScrollArea):
    """页面唯一主纵向滚动面；内容可完整显示时为 0 个滚动面。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.body = QWidget()
        self.body.setObjectName("PageBody")
        self.layout = QVBoxLayout(self.body)
        self.layout.setContentsMargins(t.S32, t.S24, t.S32, t.S32)
        self.layout.setSpacing(t.S20)
        # 关键：让 body 的最小高度跟随内容，QScrollArea 才会出现单一主滚动面，
        # 而不是把所有卡片压缩到视口高度。
        self.layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.setWidget(self.body)

    def reset(self) -> None:
        def clear(layout) -> None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.setParent(None)
                    widget.deleteLater()
                nested = item.layout()
                if nested is not None:
                    clear(nested)

        clear(self.layout)


class PageHeader(QWidget):
    def __init__(self, eyebrow: str = "", title: str = "", subtitle: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S4)
        self.eyebrow = label(eyebrow, "Eyebrow")
        self.title = label(title, "PageTitle", wrap=True)
        self.subtitle = label(subtitle, "PageSubtitle", wrap=True)
        layout.addWidget(self.eyebrow)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)

    def set_text(self, eyebrow: str = None, title: str = None, subtitle: str = None) -> None:
        if eyebrow is not None:
            self.eyebrow.setText(eyebrow)
        if title is not None:
            self.title.setText(title)
        if subtitle is not None:
            self.subtitle.setText(subtitle)


class SectionTitle(QWidget):
    def __init__(self, title: str, subtitle: str = "", action: Optional[QWidget] = None) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S12)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(label(title, "SectionTitle"))
        if subtitle:
            column.addWidget(label(subtitle, "Meta", wrap=True))
        layout.addLayout(column, 1)
        if action is not None:
            layout.addWidget(action, 0, Qt.AlignTop)


class Metric(QWidget):
    def __init__(self, value: str, caption: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.value = label(value, "BigMetric")
        self.caption = label(caption, "MetricLabel")
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, value: str, caption: Optional[str] = None) -> None:
        self.value.setText(value)
        if caption is not None:
            self.caption.setText(caption)


class DataTable(QTableWidget):
    """统一可读表格；点击链接列/双击行发出稳定实体事件。"""

    row_activated = Signal(dict)
    cell_activated = Signal(dict, str)

    def __init__(self) -> None:
        super().__init__()
        self._rows: List[dict] = []
        self._columns: List[dict] = []
        self._league_style = ""
        self._zone_rows = []
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.setIconSize(QSize(32, 32))
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setFixedHeight(36)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setFocusPolicy(Qt.StrongFocus)
        self.enter_activates = True
        self._last_cell_action = (-1, -1, 0.0)
        self.cellClicked.connect(self._on_cell_clicked)
        self.cellDoubleClicked.connect(self._on_double_clicked)

    def minimumSizeHint(self):
        """按声明列宽报告最小尺寸，不跟随当前视口宽度。

        QTableView 在 Stretch / StretchLastSection 下的默认 minimumSizeHint 会随
        当前宽度变化：窗口变窄后表格仍报告旧宽度，页面主体因此无法回到窄布局，
        出现"缩窄后水平溢出"（Windows 字体更宽时更明显）。这里用列声明宽度求和，
        结果稳定且与实际列需求一致；表格自身仍可横向滚动。
        """
        fixed = sum(int(column.get("width", 90)) for column in self._columns if not column.get("stretch"))
        stretch = 160 if any(column.get("stretch") for column in self._columns) else 0
        width = fixed + stretch + 2 * self.frameWidth() + 4
        return QSize(max(200, width), super().minimumSizeHint().height())

    def set_columns(self, columns: List[dict]) -> None:
        self._columns = columns
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels([column["label"] for column in columns])
        # macOS: descriptive columns absorb spare width; pure numeric tables
        # share it evenly instead of stretching the final statistic alone.
        even_widths = macos.enabled() and not any(column.get("stretch") for column in columns)
        for index, column in enumerate(columns):
            if column.get("stretch") or even_widths:
                self.horizontalHeader().setSectionResizeMode(index, QHeaderView.Stretch)
            else:
                self.setColumnWidth(index, int(column.get("width", 90)))
                self.horizontalHeader().setSectionResizeMode(index, QHeaderView.Fixed)
            if "alignment" in column:
                self.horizontalHeaderItem(index).setTextAlignment(column["alignment"])
            elif macos.enabled():
                self.horizontalHeaderItem(index).setTextAlignment((Qt.AlignRight if column.get("numeric") else Qt.AlignLeft) | Qt.AlignVCenter)
            if column.get("team_side"):
                self.setItemDelegateForColumn(index, macos.MatchTeamDelegate(column["team_side"], self))
        self.horizontalHeader().setStretchLastSection(not macos.enabled())

    def fit_columns(self) -> None:
        """Size fixed columns from the widest real value at the final font.

        A fixed declaration such as 72px is only a lower bound: '146.28' plus
        cell padding did not fit and was elided to '146…'. Headers are measured
        too so digits stay under their label.
        """
        if not macos.enabled() or not self._columns:
            return
        metrics = QFontMetrics(self.font())
        header_metrics = QFontMetrics(self.horizontalHeader().font())
        # 表头 QSS 有 6px 左右内边距，单元格 6px；留 26px 覆盖两者与排序箭头余量。
        padding = 26
        header = self.horizontalHeader()
        for index, column in enumerate(self._columns):
            if header.sectionResizeMode(index) == QHeaderView.Stretch:
                continue
            width = header_metrics.horizontalAdvance(str(column.get("label", ""))) + padding
            for row in self._rows:
                value = row.get(column["key"], "")
                if isinstance(value, float):
                    decimals = column.get("decimals")
                    text = f"{value:.{int(decimals)}f}" if decimals is not None else f"{value:g}"
                elif value is None:
                    text = "—"
                else:
                    text = str(value)
                width = max(width, metrics.horizontalAdvance(text) + padding)
            self.setColumnWidth(index, max(int(column.get("width", 90)), min(width, 300)))

    def set_rows(self, rows: Iterable[dict]) -> None:
        self._rows = list(rows)
        self.setRowCount(len(self._rows))
        for row_index, row in enumerate(self._rows):
            for col_index, column in enumerate(self._columns):
                value = row.get(column["key"], "")
                if isinstance(value, float):
                    decimals = column.get("decimals")
                    text = f"{value:.{int(decimals)}f}" if decimals is not None else f"{value:g}"
                elif value is None:
                    text = "—"
                else:
                    text = str(value)
                item = QTableWidgetItem(text)
                icon_key = column.get("icon_key")
                if icon_key and row.get(icon_key):
                    item.setIcon(QIcon(crest_pixmap(str(row[icon_key]), int(column.get("icon_size", 32)))))
                if column.get("kind") in ("link", "match"):
                    item.setForeground(QColor(t.LINK))
                if column.get("kind") == "status":
                    status_kind = str(row.get("status_kind", ""))
                    item.setForeground(QColor(t.SUCCESS if status_kind == "qualified" else t.DANGER if status_kind == "eliminated" else t.WARNING))
                    font = item.font()
                    font.setUnderline(False)
                    font.setWeight(QFont.DemiBold)
                    item.setFont(font)
                if column.get("numeric"):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    font = item.font()
                    font.setFamilies(["SF Mono", "Menlo", "Consolas", "monospace"])
                    item.setFont(font)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if "alignment" in column:
                    item.setTextAlignment(column["alignment"])
                if column.get("tooltip_key"):
                    item.setToolTip(str(row.get(column["tooltip_key"], "")))
                elif column.get("kind") in ("link", "match"):
                    item.setToolTip(text)
                self.setItem(row_index, col_index, item)
        self.fit_columns()
        self._apply_league_bands()

    def set_zone_rows(self, zones) -> None:
        """Colored group-table rows (success/warning/danger) on macOS only.

        The delegate paints both the row background and the narrow side band, so
        alternating row colours or QSS cannot hide the zone colour.
        """
        self._zone_rows = list(zones) if macos.enabled() else []
        if self._zone_rows:
            self.setItemDelegate(macos.ZoneRowDelegate(self))
        self.viewport().update()

    def set_league_style(self, league: str) -> None:
        self._league_style = league if macos.enabled() and league in ("premier", "second") else ""
        if self._league_style:
            self.setItemDelegate(macos.LeagueBandDelegate(self))
        self._apply_league_bands()
        self.viewport().update()

    def _apply_league_bands(self) -> None:
        if not self._league_style:
            return
        for index, row in enumerate(self._rows):
            color, caption = macos.standings_band(self._league_style, int(row.get("rank", 0)))
            for column in range(self.columnCount()):
                item = self.item(index, column)
                if item is not None and color:
                    item.setBackground(QColor(color))
                    item.setToolTip(" · ".join(filter(None, (item.toolTip(), caption))))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if getattr(self, "_zone_rows", None):
            painter = QPainter(self.viewport())
            for index, tint in enumerate(self._zone_rows):
                if not tint:
                    continue
                top = self.rowViewportPosition(index)
                height = self.rowHeight(index)
                painter.fillRect(0, top, 3, height, QColor(tint["border"]))
            painter.end()
        if not self._league_style:
            return
        boundaries = (4, 8, 12, 16) if self._league_style == "premier" else (2, 6)
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(t.ACCENT if self._league_style == "premier" else t.PRIMARY), 2))
        for index, row in enumerate(self._rows):
            if row.get("rank") in boundaries:
                y = self.rowViewportPosition(index) + self.rowHeight(index) - 1
                if 0 <= y < self.viewport().height():
                    painter.drawLine(0, y, min(self.horizontalHeader().length(), self.viewport().width()), y)
        painter.end()

    def hide_optional_columns(self, width: int) -> None:
        for index, column in enumerate(self._columns):
            if column.get("optional"):
                self.setColumnHidden(index, width < int(column.get("show_at", 1360)))

    def _on_cell_clicked(self, row_index: int, column_index: int) -> None:
        if 0 <= row_index < len(self._rows) and 0 <= column_index < len(self._columns):
            column = self._columns[column_index]
            if column.get("kind") in ("link", "match"):
                self._last_cell_action = (row_index, column_index, time.monotonic())
                self.cell_activated.emit(self._rows[row_index], column["key"])

    def _on_double_clicked(self, row_index: int, column_index: int) -> None:
        # 双击在链接/比分单元格上不重复压栈：第一次单击已经完成动作。
        last_row, last_col, when = self._last_cell_action
        if (last_row, last_col) == (row_index, column_index) and time.monotonic() - when < 0.8:
            return
        if 0 <= row_index < len(self._rows):
            self.row_activated.emit(self._rows[row_index])

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.enter_activates:
            row_index = self.currentRow()
            if 0 <= row_index < len(self._rows):
                self.row_activated.emit(self._rows[row_index])
                event.accept()
                return
        super().keyPressEvent(event)


_ATMOSPHERE: Optional[QPixmap] = None


def _atmosphere_pixmap() -> Optional[QPixmap]:
    global _ATMOSPHERE
    if _ATMOSPHERE is None:
        from football_simulator.runtime import resource_root

        path = resource_root() / "assets" / "atmosphere" / "stadium_night.png"
        if path.is_file():
            pixmap = QPixmap(str(path))
            _ATMOSPHERE = pixmap if not pixmap.isNull() else QPixmap()
    return _ATMOSPHERE or None


class FocusScoreCard(QFrame):
    """深色焦点比赛区：转播式大比分，队名与比分独立排版。"""

    detail_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("FocusPanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(t.S24, t.S20, t.S24, t.S20)
        outer.setSpacing(t.S12)
        self.meta_row = QHBoxLayout()
        self.competition = label("", "FocusMeta", wrap=True)
        self.venue = label("", "FocusMeta", wrap=True)
        for meta_label in (self.competition, self.venue):
            meta_label.setMinimumWidth(0)
        self.meta_row.addWidget(self.competition, 1)
        self.meta_row.addWidget(self.venue, 0, Qt.AlignRight)
        outer.addLayout(self.meta_row)
        self.center = QHBoxLayout()
        self.center.setSpacing(t.S16)
        self.home_box = self._team_box()
        self.score_label = label("VS", "Score")
        self.score_label.setAlignment(Qt.AlignCenter)
        self.score_label.setMinimumWidth(150)
        self.away_box = self._team_box()
        self.center.addLayout(self.home_box["layout"], 1)
        self.center.addWidget(self.score_label, 0, Qt.AlignCenter)
        self.center.addLayout(self.away_box["layout"], 1)
        outer.addLayout(self.center)
        self.status_row = QHBoxLayout()
        self.status = label("", "FocusMeta", wrap=True)
        self.detail = QPushButton("查看比赛")
        self.detail.setObjectName("FocusAction")
        self.detail.clicked.connect(lambda: self.detail_requested.emit(getattr(self, "match_id", "")))
        self.status_row.addWidget(self.status, 1)
        self.status_row.addWidget(self.detail, 0)
        outer.addLayout(self.status_row)
        self.events_label = label("", "FocusMeta", wrap=True)
        self.events_label.setMinimumWidth(0)
        self.events_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.events_label.hide()
        outer.addWidget(self.events_label)
        self.meaning_label = label("", "FocusMeta", wrap=True)
        self.meaning_label.setMinimumWidth(0)
        self.meaning_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.meaning_label.hide()
        outer.addWidget(self.meaning_label)
        self.aggregate_label = label("", "FocusMeta", wrap=True)
        self.aggregate_label.setMinimumWidth(0)
        self.aggregate_label.hide()
        outer.addWidget(self.aggregate_label)
        self.match_id = ""
        self._tie = None
        self.setMinimumHeight(250)
        self.setMaximumHeight(460)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        atmosphere = _atmosphere_pixmap()
        if atmosphere is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 18, 18)
        painter.setClipPath(clip)
        scaled = atmosphere.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        source_x = max(0, (scaled.width() - self.width()) // 2)
        source_y = max(0, (scaled.height() - self.height()) // 2)
        source = QRectF(source_x, source_y, min(scaled.width(), self.width()), min(scaled.height(), self.height()))
        painter.setOpacity(0.62)
        painter.drawPixmap(QRectF(self.rect()), scaled, source)
        painter.setOpacity(1.0)
        painter.fillRect(self.rect(), QColor(4, 12, 9, 135))
        painter.end()

    def _team_box(self):
        layout = QVBoxLayout()
        layout.setSpacing(t.S8)
        layout.setAlignment(Qt.AlignCenter)
        crest_holder = QHBoxLayout()
        crest_holder.setAlignment(Qt.AlignCenter)
        name = label("", "FocusTeam", wrap=True)
        name.setAlignment(Qt.AlignCenter)
        layout.addLayout(crest_holder)
        layout.addWidget(name)
        return {"layout": layout, "crest_holder": crest_holder, "name": name, "crest": None}

    def _set_team(self, box, world, team_id):
        holder = box["crest_holder"]
        while holder.count():
            item = holder.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if team_id and team_id in world["teams"]:
            crest = TeamCrest(world["teams"][team_id]["club_en"], 72)
            holder.addWidget(crest)
        box["name"].setText(_team_short(world, team_id))

    def set_match(self, world, dto: dict, events=None, meaning: str = "", tie: Optional[dict] = None, event_rows=None) -> None:
        self.match_id = dto["match_id"]
        self._tie = tie
        self.competition.setText(f"{dto['competition_label']} · 第 {dto['week']} 周" + (f" · {dto['stage_text']}" if dto["stage_text"] else ""))
        venue = dto["venue"]
        self.venue.setText(("中立场：" if dto["venue_mode"] == "neutral" else "") + venue.split("（")[0])
        self._set_team(self.home_box, world, dto["home_team_id"])
        self._set_team(self.away_box, world, dto["away_team_id"])
        if dto["status"] == "completed":
            self.score_label.setText(f"{dto['total_home']}–{dto['total_away']}")
            details = []
            if tie:
                legs = tie.get("legs") or []
                explicit = dto.get("leg")
                leg_number = int(explicit) if explicit in (1, 2, "1", "2") else None
                if leg_number is None:
                    leg_number = next((leg.get("leg") for leg in legs if leg.get("match_id") == self.match_id), None)
                details.append(f"本场 · 第 {leg_number} 回合" if leg_number else "本场")
            if dto["home_pk"] is not None:
                details.append(f"点球 {dto['home_pk']}–{dto['away_pk']} 决胜")
            elif dto["has_extra_time"]:
                details.append("加时 2×15 分钟后决胜")
            else:
                details.append(dto["status_text"])
            if not tie:
                details.insert(0, "本场")
            if dto["venue_mode"] == "neutral":
                details.append(dto["neutral_reason"] or "中立场：双方无主场加成")
            self.status.setText(" · ".join(details))
        else:
            self.score_label.setText("VS")
            slot = _week_slot(dto["week"])
            self.status.setText(
                f"未赛 · 第 {dto['week']} 周 · "
                + ("主队主场" if dto["venue_mode"] == "home" else "中立场")
                + (f" · {dto['stage_text']}" if dto["stage_text"] else "")
            )
        self.aggregate_label.hide()
        if tie and tie.get("totals"):
            totals = tie["totals"]
            parts = [f"{row['team']} {row['goals']}" for row in totals]
            completed = [leg for leg in tie.get("legs", []) if leg.get("status") == "completed"]
            if len(completed) >= 2:
                label_text = "总比分：" + " – ".join(parts)
            elif len(completed) == 1:
                label_text = "暂时总分（次回合未赛）：" + " – ".join(parts)
            else:
                label_text = "两回合尚未开赛"
            self.aggregate_label.setText(label_text)
            self.aggregate_label.show()
        self.detail.setEnabled(bool(self.match_id))
        rows = list(event_rows or [])
        if rows:
            # Player-facing wording built from structured facts (no slot labels).
            self.events_label.setText("\n".join(row["text"] for row in rows[:2]))
            self.events_label.show()
        elif events:
            import re

            lines = []
            for event in events[:2]:
                text = str(event.get("text") or "")
                text = re.sub(r"^\d+'\s*", "", text)
                minute = event.get("minute")
                lines.append(f"{minute}' {text}" if minute is not None else text)
            self.events_label.setText("\n".join(lines))
            self.events_label.show()
        else:
            self.events_label.hide()
        if meaning:
            self.meaning_label.setText(meaning)
            self.meaning_label.show()
        else:
            self.meaning_label.hide()


class MatchRowCard(QFrame):
    """浅色比赛行：比赛/周报/赛程复用。"""

    clicked = Signal(str)

    def __init__(self, world, dto: dict, *, perspective_team_id: Optional[str] = None) -> None:
        super().__init__()
        self.setObjectName("CardAlt")
        self.match_id = dto["match_id"]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(t.S12, t.S10, t.S12, t.S10)
        layout.setSpacing(t.S12)
        left = QVBoxLayout()
        left.setSpacing(2)
        competition_label = label(dto["competition_label"] + (f" · {dto['stage_text']}" if dto["stage_text"] else ""), "Meta")
        week_label = label(f"第 {dto['week']} 周" + (" · 中立场" if dto["venue_mode"] == "neutral" else ""), "Meta")
        # 比赛行里的文字是唯一可压缩的部分：固定 0 最小宽度 + Ignored 水平策略，
        # 让行宽由徽章、比分和队徽决定。否则 Windows 字体度量下单个行卡片会被
        # 文字顶到 700+ px，双列页面必然溢出视口（横向滚动是关闭的）。
        for meta in (competition_label, week_label):
            meta.setMinimumWidth(0)
            meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            meta.setToolTip(meta.text())
        left.addWidget(competition_label)
        left.addWidget(week_label)
        if macos.enabled():
            left.addWidget(venue_label(dto))
        layout.addLayout(left, 2)
        teams = QHBoxLayout()
        teams.setSpacing(t.S8)
        home = QLabel(_team_short(world, dto["home_team_id"]))
        away = QLabel(_team_short(world, dto["away_team_id"]))
        home.setObjectName("Strong")
        away.setObjectName("Strong")
        for name in (home, away):
            name.setMinimumWidth(0)
            name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            name.setToolTip(name.text())
        home_crest = TeamCrest(_team_club(world, dto["home_team_id"]), 36)
        away_crest = TeamCrest(_team_club(world, dto["away_team_id"]), 36)
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(home)
        names.addWidget(away)
        crests = QVBoxLayout()
        crests.setSpacing(2)
        crests.addWidget(home_crest)
        crests.addWidget(away_crest)
        teams.addLayout(crests, 0)
        teams.addLayout(names, 1)
        layout.addLayout(teams, 3)
        score = label(
            f"{dto['total_home']}–{dto['total_away']}" if dto["status"] == "completed" else "vs",
            "CardTitle",
        )
        score.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        score.setMinimumWidth(72)
        layout.addWidget(score, 0)
        right = QVBoxLayout()
        right.setSpacing(2)
        badge = Badge(dto["status_text"], status_variant(dto["status_text"]))
        result = macos.team_result(dto, perspective_team_id) if macos.enabled() and perspective_team_id else None
        if result:
            kind, caption, color, background = result
            self.setProperty("teamResult", kind)
            self.setAccessibleName(f"{caption} · {dto['home_name']} {dto['score']} {dto['away_name']}")
            self.setStyleSheet(f"QFrame#CardAlt {{ background: {background}; border: 1px solid {t.BORDER}; border-left: 5px solid {color}; border-radius: {t.RADIUS_SM}px; }}")
            badge.setText(caption)
            badge.setStyleSheet(f"color: {t.CARD}; background: {color}; border: none; border-radius: 5px; padding: 5px 10px; font-weight: 700;")
        right.addWidget(badge, 0, Qt.AlignRight)
        if dto["home_pk"] is not None:
            right.addWidget(label(f"PK {dto['home_pk']}–{dto['away_pk']}", "Meta"), 0, Qt.AlignRight)
        layout.addLayout(right, 1)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("打开比赛详情")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.match_id)
        super().mousePressEvent(event)


class CompactMatchRow(QAbstractButton):
    """Narrow-column match row: meta line, mirrored crest/name, fixed score.

    The homepage right column is too tight for the full MatchRowCard (club names
    collapsed to 54px). This row gives the score a fixed centre and lets both
    names wrap inside half the remaining width, so a full club name is readable
    at 1280 and 1440 without a tooltip.
    """

    clicked_match = Signal(str)

    def __init__(self, world, dto: dict) -> None:
        super().__init__()
        self.match_id = dto.get("match_id") or ""
        self.setObjectName("CompactMatchRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self.setAccessibleName(f"{dto.get('home_name', '')} 对 {dto.get('away_name', '')}，{dto.get('score', '')}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(t.S10, t.S8, t.S10, t.S8)
        outer.setSpacing(2)
        meta = dto.get("competition_label", "")
        if dto.get("stage_text"):
            meta = f"{meta} · {dto['stage_text']}"
        meta_label = label(f"{meta} · 第 {dto.get('week', 0)} 周", "Meta")
        meta_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        meta_label.setMinimumWidth(0)
        outer.addWidget(meta_label)
        row = QHBoxLayout()
        row.setSpacing(t.S6)
        home_name = label(dto.get("home_name", ""), "Strong", wrap=True)
        away_name = label(dto.get("away_name", ""), "Strong", wrap=True)
        for name_label in (home_name, away_name):
            name_label.setMinimumWidth(0)
            # Equal, compressible halves keep the score dead-centre and let a long
            # club name wrap instead of pushing the crest out of the row.
            name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        home_name.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        away_name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        home_crest = TeamCrest((world.get("teams", {}).get(dto.get("home_team_id")) or {}).get("club_en", ""), 20)
        away_crest = TeamCrest((world.get("teams", {}).get(dto.get("away_team_id")) or {}).get("club_en", ""), 20)
        score = label(dto.get("score", "vs"), "CompactScore")
        score.setAlignment(Qt.AlignCenter)
        score.setFixedWidth(46)
        row.addWidget(home_crest, 0, Qt.AlignVCenter)
        row.addWidget(home_name, 1)
        row.addWidget(score, 0)
        row.addWidget(away_name, 1)
        row.addWidget(away_crest, 0, Qt.AlignVCenter)
        outer.addLayout(row)
        if macos.enabled():
            outer.addWidget(venue_label(dto))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.setToolTip("")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.isEnabled() and not event.isAutoRepeat():
                self.clicked_match.emit(self.match_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        option = QStyleOption()
        option.initFrom(self)
        if self.isDown():
            option.state |= QStyle.State_Sunken
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)

    def sizeHint(self) -> QSize:
        return self.layout().totalSizeHint() if self.layout() is not None else QSize(240, 56)

    def minimumSizeHint(self) -> QSize:
        return self.layout().totalMinimumSize() if self.layout() is not None else QSize(160, 48)


class CupEmblem(QWidget):
    """六杯原生主视觉：稳定 ID → 几何徽章；不含文字/名称，可安全改名。"""

    COLORS = {
        "aureole": ("#1F4D3A", "#D8B36A"),
        "confluence": ("#223B52", "#9CC3D5"),
        "starpath": ("#3A2B4E", "#C9B6E4"),
        "ember": ("#4B2A22", "#E0A06A"),
        "ascendant": ("#3D351E", "#E3C97A"),
        "zenith": ("#262626", "#E7D9B0"),
    }

    def __init__(self, competition_id: str, size: int = 64) -> None:
        super().__init__()
        self.competition_id = competition_id
        self.setFixedSize(size, size)
        from football_simulator.game import rules

        self.setAccessibleName(f"{rules.display_name(competition_id, 'zh')} 赛事徽章")
        self.setToolTip(rules.competition_label(competition_id))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        primary, accent = self.COLORS.get(self.competition_id, ("#2E3A2F", "#D8B36A"))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        path = QPainterPath()
        path.moveTo(rect.left() + rect.width() * 0.18, rect.top())
        path.lineTo(rect.right() - rect.width() * 0.18, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + rect.height() * 0.22)
        path.lineTo(rect.right(), rect.center().y() + rect.height() * 0.12)
        path.quadTo(rect.center().x(), rect.bottom(), rect.left(), rect.center().y() + rect.height() * 0.12)
        path.lineTo(rect.left(), rect.top() + rect.height() * 0.22)
        path.quadTo(rect.left(), rect.top(), rect.left() + rect.width() * 0.18, rect.top())
        painter.setPen(QPen(QColor(accent), 2))
        painter.setBrush(QColor(primary))
        painter.drawPath(path)
        # 星点数量＝稳定杯赛编号；纯几何、无文字。
        try:
            from football_simulator.game import rules

            number = rules.CUP_META[self.competition_id]["cup_number"]
        except Exception:
            number = 1
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(accent))
        center_x = self.width() / 2
        for index in range(number):
            x = center_x + (index - (number - 1) / 2) * 7
            painter.drawEllipse(QRectF(x - 2, self.height() * 0.44, 4, 4))
        painter.setBrush(QColor(accent).lighter(115))
        painter.drawEllipse(QRectF(center_x - 7, self.height() * 0.56, 14, 14))
        painter.end()


class EmptyState(QWidget):
    def __init__(self, title: str, body: str = "", action: Optional[QWidget] = None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.S24, t.S32, t.S24, t.S32)
        layout.setSpacing(t.S8)
        layout.setAlignment(Qt.AlignCenter)
        if macos.enabled():
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            layout.setSizeConstraint(QLayout.SetMinimumSize)
            title_label = macos.EmptyStateText(title, "EmptyTitle", wrap=True)
        else:
            title_label = label(title, "EmptyTitle")
        layout.addWidget(title_label, 0, Qt.AlignCenter)
        if body:
            body_label = macos.EmptyStateText(body, "EmptyBody", wrap=True) if macos.enabled() else label(body, "EmptyBody", wrap=True)
            body_label.setAlignment(Qt.AlignCenter)
            body_label.setMaximumWidth(560)
            layout.addWidget(body_label, 0, Qt.AlignCenter)
        if action is not None:
            layout.addWidget(action, 0, Qt.AlignCenter)


class KeyValue(QWidget):
    def __init__(
        self,
        key: str,
        value: str,
        *,
        wrap: bool = False,
        vertical: bool = False,
        key_min_width: int = 110,
    ) -> None:
        super().__init__()
        layout = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(t.S4 if vertical else t.S12)
        self.key = label(key, "Meta")
        self.value = label(value, "Strong", wrap=wrap)
        self.key.setMinimumWidth(0 if vertical else key_min_width)
        self.value.setMinimumWidth(0)
        if wrap:
            self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.key, 0, Qt.AlignTop)
        layout.addWidget(self.value, 1)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class StatGrid(QWidget):
    def __init__(self, items: List[tuple]) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(t.S24)
        layout.setVerticalSpacing(t.S12)
        for index, (value, caption) in enumerate(items):
            layout.addWidget(Metric(str(value), caption), 0, index)
        for index in range(len(items)):
            layout.setColumnStretch(index, 1)


def _team_short(world: dict, team_id: Optional[str]) -> str:
    if not team_id or team_id not in world["teams"]:
        return "待定"
    return world["teams"][team_id]["club_en"]


def _team_club(world: dict, team_id: Optional[str]) -> str:
    if not team_id or team_id not in world["teams"]:
        return "Unknown"
    return world["teams"][team_id]["club_en"]


def _week_slot(week: int):
    from football_simulator.game import rules

    try:
        return rules.week_slot(week)
    except KeyError:
        return {}


BRACKET_NODE_SPEC = {
    "crest": 24,
    "gap": 8,
    "score_width": 34,
    "pad_x": 12,
    "pad_y": 8,
    "row_height": 26,
    "min_width": 260,
    "max_width": 320,
}


def bracket_name_font() -> QFont:
    """The one name font shared by round cards and the painted panorama."""
    sample = label("测试", "BracketTeam")
    sample.ensurePolished()
    font = QFont(sample.font())
    sample.deleteLater()
    return font


def bracket_name_metrics() -> QFontMetrics:
    return QFontMetrics(bracket_name_font())


def bracket_node_width(nodes, metrics: Optional[QFontMetrics] = None) -> int:
    """Measured card width so long club names are never squeezed into 196px."""
    spec = BRACKET_NODE_SPEC
    nodes = list(nodes)
    metrics = metrics or bracket_name_metrics()
    longest = 0
    for node in nodes:
        for team in node.get("teams") or []:
            if not team.get("team_id"):
                continue
            longest = max(longest, metrics.horizontalAdvance(team.get("name") or ""))
    width = spec["pad_x"] * 2 + spec["crest"] + spec["gap"] + longest + spec["gap"] + spec["score_width"]
    extra = 120 if macos.enabled() and any(node.get("two_leg") for node in nodes) else 0
    return max(spec["min_width"], min(spec["max_width"], width)) + extra


def node_special_badge(node: dict) -> str:
    """Only a real penalty/extra-time outcome survives; no generic footer."""
    status = node.get("status_text") or ""
    if status.startswith("点球"):
        return status
    if "加时" in status or node.get("decided_by") == "extra_time":
        return "加时"
    return ""


def match_venue_text(dto: dict) -> str:
    venue = str(dto.get("venue") or "").split("（")[0]
    return ("中立场 · " if dto.get("venue_mode") == "neutral" else "") + venue if venue else ""


def node_venue_lines(node: dict) -> list:
    if not macos.enabled():
        return []
    return list(node.get("venues") or [])


def venue_font() -> QFont:
    font = QFont()
    font.setPixelSize(13)
    return font


def venue_text_height(text: str, width: int) -> int:
    metrics = QFontMetrics(venue_font())
    return metrics.boundingRect(QRect(0, 0, max(40, width), 1000), Qt.TextWordWrap, text).height() + 3


def node_venue_height(node: dict, width: int) -> int:
    return sum(venue_text_height(text, width - 2 * BRACKET_NODE_SPEC["pad_x"]) for text in node_venue_lines(node))


def venue_label(dto: dict) -> QLabel:
    item = label(match_venue_text(dto), "MatchVenue", wrap=True)
    item.setStyleSheet(f"color: {t.MUTED}; font-size: 13px;")
    item.setMinimumWidth(0)
    item.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return item


class BracketNode(QFrame):
    """Shared round-view card: crest, club name, score, and an optional path menu.

    Two compact lines by default; penalties/extra time occupy their own badge row
    instead of a repeated score footer. Path links live behind the 路径 menu.
    """

    activated = Signal(str)
    path_activated = Signal(str, str)  # group key, target tie id

    def __init__(self, world, node: dict, *, show_paths: bool = False) -> None:
        super().__init__()
        self.node = node
        spec = BRACKET_NODE_SPEC
        self.setObjectName("BracketNode")
        self.setProperty("winnerSide", bool(node.get("winner_id")))
        self.match_id = (node.get("match_ids") or [""])[0]
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(spec["min_width"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(spec["pad_x"], spec["pad_y"], spec["pad_x"], spec["pad_y"])
        layout.setSpacing(2)
        two_leg = macos.enabled() and bool(node.get("two_leg"))
        legs = node.get("legs") or []
        if two_leg:
            self.setMinimumWidth(spec["min_width"] + 120)
            headers = QHBoxLayout()
            headers.setSpacing(spec["gap"])
            headers.addStretch(1)
            for index, title in enumerate(("首回合", "次回合")):
                leg = legs[index] if index < len(legs) else {}
                button = QPushButton(title)
                button.setObjectName(f"BracketLeg{index + 1}")
                button.setFixedSize(46, 24)
                button.setEnabled(bool(leg.get("match_id")))
                button.setCursor(Qt.PointingHandCursor)
                button.setStyleSheet(f"font-size: 11px; color: {t.PRIMARY}; padding: 0; min-height: 0; border: 0; background: transparent;")
                button.setToolTip(f"{title} · 第 {leg.get('week', '—')} 周 · {leg.get('score') or '未赛'}")
                button.clicked.connect(lambda _checked=False, match_id=leg.get("match_id"): self.activated.emit(match_id))
                headers.addWidget(button)
            aggregate = label("总比分" if all(leg.get("completed") for leg in legs) and len(legs) == 2 else "暂合计", "Meta")
            aggregate.setObjectName("BracketAggregateHeading")
            aggregate.setFixedWidth(46)
            aggregate.setAlignment(Qt.AlignCenter)
            aggregate.setStyleSheet("font-size: 11px;")
            headers.addWidget(aggregate)
            layout.addLayout(headers)
        for team in (node.get("teams") or [])[:2]:
            row = QHBoxLayout()
            row.setSpacing(spec["gap"])
            crest = TeamCrest(team.get("name") or "", spec["crest"])
            row.addWidget(crest, 0, Qt.AlignTop | Qt.AlignVCenter)
            name = label(team.get("name") or "待定", "BracketTeam", wrap=True)
            name.setMinimumWidth(0)
            name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            name.setWordWrap(True)
            if team.get("winner"):
                name.setStyleSheet(f"color: {t.TEXT_STRONG}; font-weight: 800;")
            else:
                name.setStyleSheet(f"color: {t.TEXT if team.get('team_id') else t.SOFT};")
            row.addWidget(name, 1, Qt.AlignVCenter)
            if two_leg:
                for index in range(2):
                    leg = legs[index] if index < len(legs) else {}
                    leg_score = (leg.get("scores") or {}).get(team.get("team_id"))
                    cell = label("—" if leg_score is None else str(leg_score), "BracketLegScore")
                    cell.setProperty("teamId", team.get("team_id"))
                    cell.setProperty("leg", index + 1)
                    cell.setFixedWidth(46)
                    cell.setAlignment(Qt.AlignCenter)
                    row.addWidget(cell, 0, Qt.AlignVCenter)
            value = team.get("score")
            score = label("—" if value is None else str(value), "BracketScore")
            score.setFixedWidth(46 if two_leg else spec["score_width"])
            score.setAlignment((Qt.AlignCenter if macos.enabled() else Qt.AlignRight) | Qt.AlignVCenter)
            if macos.enabled():
                score.setStyleSheet(
                    f"background: {t.SUCCESS_BG if team.get('winner') else t.CARD_ALT};"
                    f"color: {t.PRIMARY if team.get('winner') else t.TEXT_STRONG};"
                    "border-radius: 4px; padding: 1px 2px; font-size: 17px; font-weight: 600;"
                )
            row.addWidget(score, 0, Qt.AlignTop | Qt.AlignVCenter)
            layout.addLayout(row)
        badge = node_special_badge(node)
        if badge:
            chip = label(badge, "Meta")
            chip.setStyleSheet(f"color: {t.TEXT_STRONG}; background: {t.CARD_ALT}; border-radius: 5px; padding: 1px 6px;")
            layout.addWidget(chip, 0, Qt.AlignLeft)
        for text in node_venue_lines(node):
            venue = label(text, "MatchVenue", wrap=True)
            venue.setStyleSheet(f"color: {t.MUTED}; font-size: 13px;")
            venue.setMinimumWidth(0)
            layout.addWidget(venue)
        if show_paths and ((node.get("paths_in") or []) or (node.get("paths_out") or [])):
            self._add_path_menu(layout, node)
        self.setToolTip(node.get("status_text") or "")
        self.setStyleSheet(
            f"QFrame#BracketNode {{ background: {t.CARD}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_SM}px; }}"
            f"QFrame#BracketNode:hover {{ border-color: {t.PRIMARY_HOVER}; }}"
            f"QFrame#BracketNode:focus {{ border-color: {t.ACCENT}; }}"
        )

    def _add_path_menu(self, layout: QVBoxLayout, node: dict) -> None:
        menu = QMenu(self)
        for path in (node.get("paths_in") or []):
            action = menu.addAction(path.get("text", "来源"))
            action.triggered.connect(
                lambda _checked=False, g=path.get("group"), t=path.get("tie_id"): self.path_activated.emit(g, t)
            )
        for path in (node.get("paths_out") or []):
            action = menu.addAction(path.get("text", "下一站"))
            action.triggered.connect(
                lambda _checked=False, g=path.get("group"), t=path.get("tie_id"): self.path_activated.emit(g, t)
            )
        button = QPushButton("路径")
        button.setObjectName("PathMenu")
        button.setCursor(Qt.PointingHandCursor)
        button.setMenu(menu)
        layout.addWidget(button, 0, Qt.AlignLeft)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.match_id:
            self.activated.emit(self.match_id)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.match_id:
            self.activated.emit(self.match_id)
            event.accept()
            return
        super().keyPressEvent(event)


class BracketBoard(QWidget):
    """Readable bracket: measured round columns, group sections, real path links.

    Double elimination never shows all three groups at once: the reader picks
    胜者组 / 败者组 / 总决赛, and the visible rounds are decided by measuring the
    final font against the actual content width (not a fixed 980px threshold).
    Every node keeps the full club name; when the round does not fit, a round
    selector switches the content and the drawn connectors follow it.
    """

    match_requested = Signal(str)
    round_changed = Signal(str)
    group_changed = Signal(str)

    GAP = t.S18

    def __init__(self, world, model: dict, *, round_key: str = "", group_key: str = "") -> None:
        super().__init__()
        self.world = world
        self.model = model
        self._nodes = {}
        self._visible = []
        self._signature = None
        self._round_key = round_key or ""
        self._group_key = ""
        sample = label("测试", "BracketTeam")
        sample.ensurePolished()
        self.name_metrics = sample.fontMetrics()
        sample.deleteLater()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(t.S8)

        self.group_tabs = None
        groups = model.get("groups") or []
        if model.get("mode") == "groups" and groups:
            tabs = QTabBar()
            tabs.setObjectName("BracketGroups")
            tabs.setAccessibleName("选择签表分区")
            tabs.setExpanding(False)
            tabs.setDrawBase(False)
            for item in groups:
                index = tabs.addTab(item.get("label", ""))
                tabs.setTabData(index, item.get("key", ""))
            tabs.currentChanged.connect(self._on_group_tab)
            outer.addWidget(tabs, 0, Qt.AlignLeft)
            self.group_tabs = tabs

        self.selector_row = QWidget()
        selector_layout = QHBoxLayout(self.selector_row)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(t.S8)
        self.round_box = QComboBox()
        self.round_box.setObjectName("BracketRound")
        self.round_box.setAccessibleName("选择轮次")
        self.round_box.currentIndexChanged.connect(self._on_round_box)
        selector_layout.addWidget(self.round_box, 0)
        self.hint = label("", "Meta", wrap=True)
        self.hint.setMinimumWidth(0)
        selector_layout.addWidget(self.hint, 1)
        outer.addWidget(self.selector_row)

        self.columns_holder = QWidget()
        self.columns_layout = QHBoxLayout(self.columns_holder)
        self.columns_layout.setContentsMargins(0, 0, 0, 0)
        self.columns_layout.setSpacing(self.GAP)
        outer.addWidget(self.columns_holder)

        if model.get("pending"):
            outer.addWidget(label(model["pending"], "Meta", wrap=True))
        # Constructor-time selection is resolved once, then applied to the
        # internal state, the tab bar, the round box and the content together.
        # Nothing is emitted here: the page has not connected yet and reads the
        # final selection after construction instead.
        initial_group = ""
        if model.get("mode") == "groups":
            initial_group = group_key or model.get("current_group") or ""
        self._apply_selection(initial_group, round_key, notify=False)

    # -- public state ---------------------------------------------------
    @property
    def group_key(self) -> str:
        return self._group_key

    @property
    def round_key(self) -> str:
        return self._round_key

    def selection(self):
        """Fully resolved (group_key, round_key) currently applied."""
        return self._group_key, self._round_key

    def round_label(self) -> str:
        for round_data in self._current_rounds():
            if round_data.get("key") == self._round_key:
                return round_data.get("label", "")
        return ""

    def visible_tie_ids(self):
        return [node["tie_id"] for node in self._visible]

    def visible_nodes(self):
        return list(self._visible)

    def visible_round_keys(self):
        return list(self._rendered_round_keys)

    def visible_links(self):
        visible = set(self.visible_tie_ids())
        return [link for link in self.model.get("links") or [] if link.get("from") in visible and link.get("to") in visible]

    def select_group(self, key: str) -> None:
        """Switch section; the round falls back inside that section."""
        if self.group_tabs is None or not key:
            return
        self._apply_selection(key, "", notify=True)

    def select_round(self, key: str) -> None:
        """Select a round by its stable key (also used by tests and paths)."""
        if not key or key == self._round_key:
            return
        self._apply_selection(self._group_key, key, notify=True)

    def focus_tie(self, group: str, tie_id: str) -> bool:
        """Validate the target, apply its full selection once, then focus it.

        Returns False without touching any state when the group/tie is unknown,
        so a bad path can never leave the board half-switched.
        """
        target_group = group
        node = None
        for group_data in self.model.get("groups") or []:
            if target_group and group_data.get("key") != target_group:
                continue
            for round_data in group_data.get("rounds") or []:
                for item in round_data.get("nodes") or []:
                    if item.get("tie_id") == tie_id:
                        node = item
                        target_group = group_data.get("key")
                        break
                if node is not None:
                    break
            if node is not None:
                break
        if node is None or not target_group:
            return False
        applied, changed = self._apply_selection(target_group, node.get("stage", ""), notify=True)
        if not applied:
            return False
        widget = self._nodes.get(tie_id)
        if widget is not None:
            widget.setFocus(Qt.OtherFocusReason)
            self._scroll_parent_to(widget)
        return True

    # -- one place that resolves, applies and announces a selection -------
    def _group_keys(self):
        return [group.get("key") for group in self.model.get("groups") or []]

    def _round_keys_for(self, group_key: str):
        for group_data in self.model.get("groups") or []:
            if group_data.get("key") == group_key:
                return [round_data.get("key") for round_data in group_data.get("rounds") or []]
        return []

    def _resolve_selection(self, group_key: str, round_key: str):
        """(group, round) that actually exists in this model."""
        if self.model.get("mode") == "groups":
            groups = self._group_keys()
            if not groups:
                return "", ""
            group = group_key if group_key in groups else ""
            if not group:
                current = self.model.get("current_group")
                group = current if current in groups else groups[0]
            rounds = self._round_keys_for(group)
            if not rounds:
                return group, ""
            return group, (round_key if round_key in rounds else rounds[0])
        columns = [column.get("key") for column in self.model.get("columns") or []]
        if not columns:
            return "", ""
        return "", (round_key if round_key in columns else columns[0])

    def _apply_selection(self, group_key: str, round_key: str, *, notify: bool):
        """Resolve, apply to state/controls/content, then notify once.

        Every entry point (constructor, tab bar, round box, programmatic calls,
        path links) goes through here, so the tab bar, the selector, the
        rendered round and the page can never disagree.
        """
        group, round_value = self._resolve_selection(group_key, round_key)
        previous = (self._group_key, self._round_key)
        changed = previous != (group, round_value)
        self._group_key = group
        self._round_key = round_value

        if self.group_tabs is not None:
            index = self._tab_index(group)
            if index >= 0:
                blocked = self.group_tabs.blockSignals(True)
                self.group_tabs.setCurrentIndex(index)
                self.group_tabs.blockSignals(blocked)
        # Re-render only when the visible content can differ; an idempotent
        # click must not rebuild the very node whose link was clicked.
        if changed or not self._visible:
            self._render()
        else:
            self._sync_round_box(self._current_rounds(), self._round_key, visible=self.round_box.isVisible())
        if notify:
            if previous[0] != group:
                self.group_changed.emit(group)
            if previous[1] != round_value:
                self.round_changed.emit(round_value)
        return bool(group or round_value), changed

    def _scroll_parent_to(self, widget) -> None:
        parent = self.parent()
        while parent is not None and not hasattr(parent, "verticalScrollBar"):
            parent = parent.parent()
        if parent is not None and hasattr(parent, "ensureWidgetVisible"):
            parent.ensureWidgetVisible(widget)

    # -- rendering ------------------------------------------------------
    def _current_rounds(self):
        if self.model.get("mode") == "groups":
            for group in self.model.get("groups") or []:
                if group.get("key") == self._group_key:
                    return group.get("rounds") or []
            return []
        return [
            {"key": column.get("key"), "label": column.get("label", ""), "nodes": column.get("nodes") or []}
            for column in self.model.get("columns") or []
        ]

    def _all_nodes(self):
        return [node for round_data in self._current_rounds() for node in round_data.get("nodes") or []]

    def min_node_width(self, nodes) -> int:
        return bracket_node_width(nodes, self.name_metrics)

    def _viewport_width(self) -> int:
        """Width the reader actually has, not the width a wide layout forced."""
        parent = self.parent()
        while parent is not None:
            viewport = getattr(parent, "viewport", None)
            if callable(viewport):
                return viewport().width()
            parent = parent.parent()
        return self.width()

    def minimumSizeHint(self):  # noqa: N802 - Qt API
        # One readable column, not the sum of currently visible ones: otherwise a
        # wide layout keeps the whole page horizontally scrollable and the board
        # can never shrink back to the narrow layout.
        height = super().minimumSizeHint().height()
        return QSize(240, height)

    def _fit_count(self, count: int, node_width: int) -> int:
        available = min(self.width() or 640, self._viewport_width() or 640)
        return max(1, int((available + self.GAP) // (node_width + self.GAP)))

    def _clear(self) -> None:
        while self.columns_layout.count():
            item = self.columns_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._nodes = {}

    def _column(self, round_data: dict, width: int, *, show_paths: bool) -> QWidget:
        column = QWidget()
        column.setMinimumWidth(width)
        # A single visible round should not stretch a reading card across the page.
        column.setMaximumWidth(width + 56)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S8)
        layout.addWidget(label(round_data.get("label", ""), "Eyebrow"))
        for node in round_data.get("nodes") or []:
            widget = BracketNode(self.world, node, show_paths=show_paths)
            widget.activated.connect(self.match_requested.emit)
            widget.path_activated.connect(self.focus_tie)
            if node.get("tie_id"):
                self._nodes[node["tie_id"]] = widget
            layout.addWidget(widget)
        layout.addStretch(1)
        return column

    def _render(self) -> None:
        rounds = self._current_rounds()
        if not rounds:
            self._clear()
            self._visible = []
            self._rendered_round_keys = []
            self.selector_row.setVisible(False)
            self.update()
            return
        nodes = self._all_nodes()
        node_width = self.min_node_width(nodes)
        for node in nodes:
            node["min_width"] = node_width
        fit = self._fit_count(len(rounds), node_width)
        keys = [round_data.get("key") for round_data in rounds]
        if self._round_key not in keys:
            self._round_key = keys[0]
        show_all = fit >= len(rounds)
        if show_all:
            visible = rounds
            self.selector_row.setVisible(False)
            self._sync_round_box(rounds, self._round_key, visible=False)
        else:
            visible = [round_data for round_data in rounds if round_data.get("key") == self._round_key] or [rounds[0]]
            self._round_key = visible[0].get("key")
            self.selector_row.setVisible(True)
            self._sync_round_box(rounds, self._round_key, visible=True)
        self._clear()
        for round_data in visible:
            self.columns_layout.addWidget(self._column(round_data, node_width, show_paths=self.model.get("mode") == "groups"), 1)
        # Keep the block left-aligned when a single round cannot fill the width.
        self.columns_layout.addStretch(1)
        self._rendered_round_keys = [round_data.get("key") for round_data in visible]
        self._visible = [node for round_data in visible for node in round_data.get("nodes") or []]
        if show_all:
            self.hint.setText("")
        else:
            total = len(rounds)
            index = keys.index(self._round_key) + 1
            self.hint.setText(f"{self.round_label()} · 第 {index}/{total} 轮 · {len(self._visible)} 组对阵")
        self.update()

    def _sync_round_box(self, rounds, key: str, *, visible: bool) -> None:
        self.round_box.blockSignals(True)
        self.round_box.clear()
        for round_data in rounds:
            self.round_box.addItem(round_data.get("label", ""), round_data.get("key"))
        index = self.round_box.findData(key)
        self.round_box.setCurrentIndex(max(0, index))
        self.round_box.blockSignals(False)
        self.round_box.setVisible(visible)

    def _on_round_box(self, _index: int) -> None:
        key = self.round_box.currentData()
        if not key:
            return
        self._apply_selection(self._group_key, key, notify=True)

    def _on_group_tab(self, index: int) -> None:
        key = self.group_tabs.tabData(index) if self.group_tabs is not None else ""
        if not key:
            return
        self._apply_selection(key, "", notify=True)

    def _tab_index(self, key: str) -> int:
        if self.group_tabs is None:
            return -1
        for index in range(self.group_tabs.count()):
            if self.group_tabs.tabData(index) == key:
                return index
        return -1

    # -- geometry -------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        rounds = self._current_rounds()
        if not rounds:
            return
        node_width = self.min_node_width(self._all_nodes())
        signature = (self.model.get("mode"), self._group_key, self._round_key, self._fit_count(len(rounds), node_width), int(self.width() // 40))
        if signature != self._signature:
            self._signature = signature
            self._render()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(t.BORDER_STRONG), 1.5))
        for link in self.visible_links():
            source = self._nodes.get(link.get("from"))
            target = self._nodes.get(link.get("to"))
            if source is None or target is None or not source.isVisible() or not target.isVisible():
                continue
            start = source.mapTo(self, source.rect().topRight())
            end = target.mapTo(self, target.rect().topLeft())
            mid_x = (start.x() + end.x()) // 2
            painter.drawLine(start.x(), start.y(), mid_x, start.y())
            painter.drawLine(mid_x, start.y(), mid_x, end.y())
            painter.drawLine(mid_x, end.y(), end.x(), end.y())
        painter.end()
