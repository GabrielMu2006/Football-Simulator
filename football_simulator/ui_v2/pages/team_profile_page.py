"""球队详情页（阶段 4 · 实施方案 §8.6）：``Route("team", team=<id>, season=<n>)``。

数据源：``team_queries.get_team_season_profile``（积分榜行/阵容/赛程/转会/
球队荣誉/球员个人奖项）与 ``history_queries.get_season_archive_detail``
（赛季历史页签，逐赛季归档）。

页签结构（QTabWidget；页签选择经 ``save_state``/``restore_state`` 记忆）：

- 概览：积分榜行完整卡片 + 该赛季球队荣誉 + 联赛走势（最近 5 场胜/平/负徽标）；
- 阵容：全高 EntityTable（11 人），行激活 → ``Route("player", ...)``；
- 赛程与结果：全高 EntityTable（全部 fixtures），行激活 → ``Route("match", ...)``，
  对手列单元格单击 → ``Route("team", ...)``；
- 赛季历史：一行一赛季的归档表（单外层 QScrollArea，内容完整展开）；
- 转会：全高 EntityTable（转入/转出方向列），球员与对方球队列单击可点；
- 奖项关联：球员个人奖项与球队荣誉分区展示（内容型页签）。

滚动硬规则（§8.2）：每个页签恰有一个纵向滚动面——表页签为 EntityTable 内部
的 QTableView（外层不再套 QScrollArea），内容型页签（概览/赛季历史/奖项关联）
为单外层 QScrollArea 且内容完整展开，不出现嵌套小滚动区。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base, history_queries, team_queries
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    ALT_ROW_COLOR,
    BG_COLOR_INPUT,
    ColumnSpec,
    EmptyState,
    EntityLink,
    EntityTable,
    GRID_COLOR,
    HEADER_BG_COLOR,
    LINK_COLOR,
    PageHeader,
    ROW_HOVER_COLOR,
    SELECTION_COLOR,
    TEXT_COLOR,
    TEXT_COLOR_BRIGHT,
    TEXT_COLOR_MUTED,
)
from football_simulator.ui_v2.design_tokens import (
    DANGER_DEEP_BG,
    DANGER_SOFT,
    HEADER_TEXT_COLOR,
    LINK_DARK_BG,
    NEUTRAL_BADGE_BG,
    NEUTRAL_BADGE_FG,
    NEUTRAL_DARK_BG,
    NEUTRAL_LIGHT,
    SUCCESS_BG,
    SUCCESS_BRIGHT,
    SUCCESS_COLOR,
    SUCCESS_DEEP_BG,
)
from football_simulator.ui_v2.components.team_crest import TeamCrest, draw_team_crest
from football_simulator.ui_v2.pages.entity_page_base import (
    EntityPageBase,
    PageContext,
)
from football_simulator.ui_v2.widgets import CardFrame, section_header

_TAB_TITLES: Tuple[str, ...] = ("概览", "阵容", "赛程与结果", "赛季历史", "转会", "奖项关联")

_AWARD_TYPE_LABELS: Dict[str, str] = {
    "top20": "年度 Top20",
    "top_scorer": "射手王",
    "assist_leader": "助攻王",
    "mvp": "MVP",
}

# 胜/平/负/未赛 徽标配色（前景色铺满圆角底、深色文字保证可读）。
_RESULT_BADGE_COLORS: Dict[str, Tuple[str, str]] = {
    "胜": (SUCCESS_BRIGHT, SUCCESS_DEEP_BG),
    "平": (NEUTRAL_LIGHT, NEUTRAL_DARK_BG),
    "负": (DANGER_SOFT, DANGER_DEEP_BG),
    "未赛": (NEUTRAL_BADGE_FG, NEUTRAL_BADGE_BG),
}

_DIVISION_BADGE_COLORS: Dict[str, Tuple[str, str]] = {
    base.COMPETITION_PREMIER: (LINK_COLOR, LINK_DARK_BG),
    base.COMPETITION_SECOND: (SUCCESS_COLOR, SUCCESS_BG),
}

_GRID_HEADER_STYLE = (
    f"background: {HEADER_BG_COLOR}; color: {HEADER_TEXT_COLOR}; font-weight: 800;"
    f"padding: 8px 10px; border-bottom: 1px solid {GRID_COLOR};"
)
_GRID_CELL_STYLE = (
    f"background: {{background}}; color: {TEXT_COLOR}; padding: 7px 10px;"
    f"border-bottom: 1px solid {GRID_COLOR};"
)


class _Millions(float):
    """身价数值：``float`` 子类（EntityTable 仍按数值排序），显示带 M 后缀。"""

    def __new__(cls, value: float) -> "_Millions":
        return super().__new__(cls, value)

    def __str__(self) -> str:
        return f"{float(self):.2f}M"


def _market_value(value: Optional[float]) -> Optional[float]:
    """默认球员身价为 ``None`` → EntityTable 显示"—"（不伪造数值）。"""

    return _Millions(value) if value is not None else None


# -- 页签行 DTO -----------------------------------------------------------


@dataclass(frozen=True)
class _SquadRow:
    player: base.PlayerRef
    player_name: str
    position: str
    ability: int
    kind: str
    market_value: Optional[float]


@dataclass(frozen=True)
class _FixtureRow:
    match: base.MatchRef
    week_number: int
    competition: str
    round_number: int
    side: str
    opponent: str
    opponent_team_id: int
    score: str
    result: str


@dataclass(frozen=True)
class _TransferRow:
    direction: str
    player: base.PlayerRef
    player_name: str
    position: str
    window: str
    counterpart: base.TeamRef
    counterpart_name: str
    status: str
    market_value: Optional[float]


@dataclass(frozen=True)
class _SeasonHistoryRow:
    season_number: int
    has_archive: bool
    level: str
    rank: str
    league_result: str
    winners_cup: str
    challenge_cup: str
    super_cup: str
    honor_points: str


_SQUAD_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("player_name", "球员", width=210),
    ColumnSpec("position", "位置", width=64, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("ability", "能力", width=72, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("kind", "类型", width=100, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("market_value", "身价", width=110, alignment=Qt.AlignmentFlag.AlignRight),
)

_FIXTURE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("week_number", "周", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("competition", "赛事", width=110),
    ColumnSpec("round_number", "轮次", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("side", "主/客", width=68, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("opponent", "对手", width=190, stretch=True),
    ColumnSpec("score", "比分", width=84, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("result", "结果", width=72, alignment=Qt.AlignmentFlag.AlignHCenter),
)

_TRANSFER_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("direction", "方向", width=64, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("player_name", "球员", width=180),
    ColumnSpec("position", "位置", width=64, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("window", "窗口", width=132),
    ColumnSpec("counterpart_name", "对方球队", width=180),
    ColumnSpec("status", "状态", width=118),
    ColumnSpec("market_value", "价值", width=110, alignment=Qt.AlignmentFlag.AlignRight),
)


def _column_index(columns: Sequence[ColumnSpec], key: str) -> int:
    for index, column in enumerate(columns):
        if column.key == key:
            return index
    raise ValueError(f"未知列：{key}")


# -- 列级 delegate ---------------------------------------------------------


class _LinkColumnDelegate(QStyledItemDelegate):
    """把一列文本渲染为实体链接（§7.2）：青色、hover 下划线、单击导航。

    行的其余区域仍走 EntityTable 默认行为（双击/Enter 打开行主路由），
    因此单击该列单元格时既有链接导航、又不影响整行选择。
    """

    def __init__(
        self,
        table: EntityTable,
        resolver: Callable[[Any], Optional[navigation.Route]],
        alignment: Qt.AlignmentFlag,
        parent: Optional[QWidget] = None,
        crest: bool = False,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._resolver = resolver
        self._alignment = Qt.AlignmentFlag(alignment)
        self._crest = crest

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = opt.font
        font.setUnderline(bool(opt.state & QStyle.State.State_MouseOver))
        painter.setFont(font)
        painter.setPen(QColor(LINK_COLOR))
        rect = opt.rect.adjusted(8, 0, -8, 0)
        if self._crest:
            crest_size = min(rect.height(), 38)
            crest_rect = QRect(
                rect.left(),
                rect.top() + (rect.height() - crest_size) // 2,
                crest_size,
                crest_size,
            )
            draw_team_crest(painter, crest_rect, str(text), size=crest_size)
            rect = QRect(
                rect.left() + crest_size + 6,
                rect.top(),
                max(0, rect.width() - crest_size - 6),
                rect.height(),
            )
        painter.drawText(rect, int(self._alignment | Qt.AlignmentFlag.AlignVCenter), str(text))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - Qt API
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
        ):
            self._activate(index)
        return super().editorEvent(event, model, option, index)

    def _activate(self, index) -> None:
        proxy = self._table.view.model()
        row = self._table.model.row_at(proxy.mapToSource(index).row())
        if row is None:
            return
        route = self._resolver(row)
        if route is not None and self._table.navigator is not None:
            self._table.navigator(route)


class _ResultDelegate(QStyledItemDelegate):
    """结果列徽标：胜/平/负/未赛 用圆角色块渲染，其余按普通文本。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full_rect = opt.rect.adjusted(8, 5, -8, -5)
        colors = _RESULT_BADGE_COLORS.get(text)
        if colors is not None:
            background, foreground = colors
            # 缩小徽标宽度并居中，避免全列宽的高饱和药丸造成视觉噪音。
            badge_width = min(full_rect.width(), 64)
            badge_x = full_rect.x() + (full_rect.width() - badge_width) // 2
            rect = QRect(badge_x, full_rect.y(), badge_width, full_rect.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(rect, 8, 8)
        else:
            foreground = TEXT_COLOR
            rect = full_rect
        painter.setPen(QColor(foreground))
        painter.setFont(opt.font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()


# -- 内容型页签组件 ---------------------------------------------------------


class _GridTable(QWidget):
    """内容型页签里的完整展开表格：无自身滚动区（外层 QScrollArea 负责滚动）。"""

    def __init__(self, headers: Sequence[str], stretch_column: int = -1, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("gridTable")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)
        self.data_row_count = 0
        self._cells: Dict[Tuple[int, int], QWidget] = {}
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet(_GRID_HEADER_STYLE)
            label.setAccessibleName(header)
            self._grid.addWidget(label, 0, column)
        self._grid.setColumnStretch(len(headers) - 1 if stretch_column < 0 else stretch_column, 1)

    def add_row(self, cells: Sequence[object]) -> None:
        """追加一行；``str`` 渲染为标签，``QWidget``（如 EntityLink）包进单元格容器。"""

        self.data_row_count += 1
        row_index = self.data_row_count
        background = ALT_ROW_COLOR if row_index % 2 == 0 else "transparent"
        for column, cell in enumerate(cells):
            if isinstance(cell, QWidget):
                wrapper = QWidget(self)
                wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                wrapper.setStyleSheet(
                    f"background: {background}; border-bottom: 1px solid {GRID_COLOR};"
                )
                wrapper_layout = QHBoxLayout(wrapper)
                wrapper_layout.setContentsMargins(10, 6, 10, 6)
                wrapper_layout.setSpacing(4)
                wrapper_layout.addWidget(cell)
                wrapper_layout.addStretch(1)
                self._grid.addWidget(wrapper, row_index, column)
                self._cells[(row_index, column)] = cell
            else:
                label = QLabel(str(cell))
                label.setWordWrap(True)
                label.setStyleSheet(_GRID_CELL_STYLE.replace("{background}", background))
                self._grid.addWidget(label, row_index, column)
                self._cells[(row_index, column)] = label

    def widget_at(self, row: int, column: int) -> Optional[QWidget]:
        """取第 ``row``（1 起，含表头偏移）行、``column`` 列的单元格内容。"""

        return self._cells.get((row, column))


def _badge(text: str, background: str, foreground: str = "#0b1220", large: bool = False) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    size = "font-size: 14px; padding: 4px 14px;" if large else "padding: 3px 12px;"
    label.setStyleSheet(
        f"background: {background}; color: {foreground}; border-radius: 9px;"
        f"{size} font-weight: 800;"
    )
    return label


# -- 页面 -------------------------------------------------------------------


class TeamProfilePage(EntityPageBase):
    """球队详情：``Route("team", team=<id>, season=<n>)``。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._team_name = ""
        self._overview_metrics: Dict[str, str] = {}
        self._history_rows: List[_SeasonHistoryRow] = []
        self._history_table: Optional[_GridTable] = None
        self._awards_table: Optional[_GridTable] = None
        self._page_header: Optional[PageHeader] = None
        self._season_caption: Optional[QLabel] = None
        self._pointer_tables: Dict[int, set] = {}
        super().__init__(context, parent)

    # -- UI 骨架 -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, 1)

        self._content = QWidget(self)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        # 页头（队名大标题 + 面包屑；右侧动作区放赛季选择器）。
        self._season_caption = QLabel("赛季")
        self._season_caption.setObjectName("teamSeasonCaption")
        self._season_caption.setStyleSheet(f"color: {TEXT_COLOR_MUTED}; background: transparent;")
        self._season_combo = QComboBox(self)
        self._season_combo.setObjectName("teamSeasonSelector")
        self._season_combo.currentIndexChanged.connect(self._on_season_selected)
        self._rebuild_header("球队详情", None)

        # 积分榜行摘要：分区徽标 + 名次/赛/胜/平/负/进失/积分。
        summary_row = QWidget(self._content)
        summary_layout = QHBoxLayout(summary_row)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(10)
        self._division_badge = QLabel("", summary_row)
        self._division_badge.setObjectName("teamDivisionBadge")
        self._summary_label = QLabel("", summary_row)
        self._summary_label.setObjectName("teamSummaryLabel")
        self._summary_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 14px; background: transparent;")
        summary_layout.addWidget(self._division_badge)
        summary_layout.addWidget(self._summary_label, 1)
        content_layout.addWidget(summary_row)

        self._tabs = QTabWidget(self._content)
        self._overview_scroll = self._make_content_scroll("teamOverviewScroll")
        self._tabs.addTab(self._overview_scroll, _TAB_TITLES[0])

        self._squad_panel = QWidget(self)
        squad_layout = QVBoxLayout(self._squad_panel)
        squad_layout.setContentsMargins(0, 0, 0, 0)
        squad_layout.setSpacing(6)
        self._squad_table = EntityTable(_SQUAD_COLUMNS, navigator=self._context.navigate, parent=self._squad_panel)
        self._squad_real_only = QCheckBox("只显示真实球员", self._squad_panel)
        self._squad_real_only.setObjectName("squadRealOnlyCheck")
        self._squad_real_only.setChecked(True)  # 默认只显示真实球员
        self._squad_real_only.toggled.connect(self._on_squad_real_only_toggled)
        squad_layout.addWidget(self._squad_real_only, 0)
        squad_layout.addWidget(self._squad_table, 1)
        self._tabs.addTab(self._squad_panel, _TAB_TITLES[1])

        self._fixtures_table = EntityTable(_FIXTURE_COLUMNS, navigator=self._context.navigate, parent=self)
        self._tabs.addTab(self._fixtures_table, _TAB_TITLES[2])

        self._history_scroll = self._make_content_scroll("teamHistoryScroll")
        self._tabs.addTab(self._history_scroll, _TAB_TITLES[3])

        self._transfer_stack = QStackedWidget(self)
        self._transfers_table = EntityTable(_TRANSFER_COLUMNS, navigator=self._context.navigate, parent=self)
        self._transfers_empty = EmptyState("暂无转会记录", "该球队在这个赛季没有转会记录。")
        self._transfer_stack.addWidget(self._transfers_table)
        self._transfer_stack.addWidget(self._transfers_empty)
        self._tabs.addTab(self._transfer_stack, _TAB_TITLES[4])

        self._awards_scroll = self._make_content_scroll("teamAwardsScroll")
        self._tabs.addTab(self._awards_scroll, _TAB_TITLES[5])

        content_layout.addWidget(self._tabs, 1)
        self._stack.addWidget(self._content)

        self._empty = EmptyState("未找到球队", "")
        self._stack.addWidget(self._empty)

        # 列级链接与结果徽标 delegate。
        # 生命周期约定（重要，经崩溃报告与压力矩阵实证）：setItemDelegateForColumn
        # 不取得所有权，且 shiboken 会在最后一个 Python 引用消失时删除 C++
        # 对象（即使挂了 Qt parent）——此时旧视图的列映射仍指向已释放对象，
        # 之后任意 GC/绘制时刻 SIGSEGV。因此统一约定：parent=view（与视图同
        # 生命周期）+ 页面持有 Python 引用**并保持到页面销毁，绝不提前清空**。
        opponent_index = _column_index(_FIXTURE_COLUMNS, "opponent")
        self._fixture_link_delegate = _LinkColumnDelegate(
            self._fixtures_table,
            self._fixture_opponent_route,
            _FIXTURE_COLUMNS[opponent_index].alignment,
            parent=self._fixtures_table.view,
            crest=True,
        )
        self._fixtures_table.view.setItemDelegateForColumn(
            opponent_index, self._fixture_link_delegate
        )
        self._result_delegate = _ResultDelegate(parent=self._fixtures_table.view)
        self._fixtures_table.view.setItemDelegateForColumn(
            _column_index(_FIXTURE_COLUMNS, "result"), self._result_delegate
        )
        transfer_player_index = _column_index(_TRANSFER_COLUMNS, "player_name")
        self._transfer_link_delegate = _LinkColumnDelegate(
            self._transfers_table,
            self._transfer_player_route,
            _TRANSFER_COLUMNS[transfer_player_index].alignment,
            parent=self._transfers_table.view,
        )
        self._transfers_table.view.setItemDelegateForColumn(
            transfer_player_index, self._transfer_link_delegate
        )
        transfer_counterpart_index = _column_index(_TRANSFER_COLUMNS, "counterpart_name")
        self._transfer_counterpart_delegate = _LinkColumnDelegate(
            self._transfers_table,
            self._transfer_counterpart_route,
            _TRANSFER_COLUMNS[transfer_counterpart_index].alignment,
            parent=self._transfers_table.view,
        )
        self._transfers_table.view.setItemDelegateForColumn(
            transfer_counterpart_index, self._transfer_counterpart_delegate
        )

        # 指针反馈：悬停可点列时切换为手型光标（§7.2 指针变化）。
        self._pointer_tables = {
            id(self._fixtures_table.view): {_column_index(_FIXTURE_COLUMNS, "opponent")},
            id(self._transfers_table.view): {
                _column_index(_TRANSFER_COLUMNS, "player_name"),
                _column_index(_TRANSFER_COLUMNS, "counterpart_name"),
            },
        }
        for table in (self._fixtures_table, self._transfers_table):
            table.view.viewport().installEventFilter(self)

        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _make_content_scroll(self, object_name: str) -> QScrollArea:
        """内容型页签的单外层滚动区；内容完整展开，不嵌套小滚动面。"""

        scroll = QScrollArea(self)
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _rebuild_header(self, title: str, route: Optional[navigation.Route]) -> None:
        """重建页头（PageHeader 标题在构造时固定）；赛季选择器跨刷新复用。"""

        old_header = self._page_header
        breadcrumbs: list = []
        # 复用控件直接交给新页头；旧页头随后删除，全程不 setParent(None)。
        self._team_crest = TeamCrest(title, size=64)
        self._page_header = PageHeader(
            title,
            breadcrumbs,
            self._context.navigate,
            avatar=self._team_crest,
        )
        assert self._season_caption is not None
        self._page_header.add_action(self._season_caption)
        self._page_header.add_action(self._season_combo)
        assert self._content is not None
        self._content.layout().insertWidget(0, self._page_header)
        if old_header is not None:
            old_header.deleteLater()

    # -- 契约入口 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        team_id = route.int_param("team") if route is not None else None
        season = route.int_param("season") if route is not None else None
        if team_id is None or season is None:
            self._set_empty_state("尚未选择球队", "路由缺少球队或赛季参数。")
            return
        try:
            with base.open_read_connection(self.save_name()) as conn:
                profile = team_queries.get_team_season_profile(conn, team_id, season)
                seasons = base.load_seasons(conn)
                history_rows = self._build_history_rows(conn, seasons, profile)
        except base.MissingSaveError as exc:
            self._set_empty_state("存档不可用", str(exc))
            return
        except KeyError as exc:
            self._set_empty_state("未找到球队", str(exc))
            return
        except sqlite3.Error as exc:
            self._set_empty_state("存档读取失败", str(exc))
            return
        self._populate(profile, seasons, history_rows, int(season))

    def route_context(self) -> dict:
        return {"team_name": self._team_name}

    # -- 数据装载 -------------------------------------------------------------

    def _populate(
        self,
        profile: team_queries.TeamSeasonProfile,
        seasons: Sequence[base.SeasonRef],
        history_rows: Sequence[_SeasonHistoryRow],
        season: int,
    ) -> None:
        route = self.current_route()
        self._team_name = profile.identity.display_name
        self._rebuild_header(profile.identity.display_name, route)
        self._rebuild_season_combo(seasons, season)

        line = profile.standings_row
        rank_text = f"第 {line.rank} 名" if line.rank is not None else "未排名"
        colors = _DIVISION_BADGE_COLORS.get(
            profile.season_division, ("#94a3b8", "#0f172a")
        )
        self._division_badge.setText(profile.season_division)
        self._division_badge.setStyleSheet(
            f"background: {colors[0]}; color: {colors[1]}; border-radius: 9px;"
            "padding: 4px 14px; font-weight: 800;"
        )
        self._summary_label.setText(
            f"{rank_text} · {line.played} 赛 {line.wins} 胜 {line.draws} 平 {line.losses} 负"
            f" · 进 {line.goals_for} / 失 {line.goals_against} · 积分 {line.points}"
        )

        squad_rows = [
            _SquadRow(
                player=line_.player,
                player_name=line_.player.display_name,
                position=line_.player.position,
                ability=line_.ability,
                kind="真实球员" if line_.player.is_real else "默认球员",
                market_value=_market_value(line_.market_value),
            )
            for line_ in profile.roster
        ]
        self._profile = profile
        self._history_rows = history_rows
        self._season = season
        self._squad_rows_all = squad_rows
        self._refresh_squad()
        # 页签记忆：恢复上次勾选（放在数据就绪后，避免恢复时行集为空）。
        state = self.stored_state()
        self._squad_real_only.blockSignals(True)
        self._squad_real_only.setChecked(str(state.get("squadRealOnly", "0")) == "1")
        self._squad_real_only.blockSignals(False)
        self._refresh_squad()

    def _refresh_squad(self) -> None:
        profile = self._profile
        season = self._season
        history_rows = self._history_rows
        rows = getattr(self, "_squad_rows_all", [])
        if self._squad_real_only.isChecked():
            rows = [row for row in rows if row.player.is_real]
        self._squad_table.set_rows(rows, route_for_row=self._squad_route_for_row)

        fixture_rows = [self._fixture_row(fixture, profile.identity.team_id) for fixture in profile.fixtures]
        self._fixtures_table.set_rows(fixture_rows, route_for_row=self._fixture_route_for_row)

        transfer_rows = [
            self._transfer_row("转入", line_) for line_ in profile.transfers_in
        ] + [self._transfer_row("转出", line_) for line_ in profile.transfers_out]
        self._transfers_table.set_rows(transfer_rows, route_for_row=self._transfer_route_for_row)
        self._transfer_stack.setCurrentWidget(
            self._transfers_table if transfer_rows else self._transfers_empty
        )

        self._replace_scroll_content(self._overview_scroll, self._build_overview(profile, season))
        self._replace_scroll_content(self._history_scroll, self._build_history_content(history_rows))
        self._replace_scroll_content(self._awards_scroll, self._build_awards_content(profile, season))

        # 页签记忆（route 无 tab 参数，状态经 save_state/restore_state 往返）。
        state = self.stored_state()
        try:
            tab = int(state.get("tab", 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            tab = 0
        tab = max(0, min(tab, self._tabs.count() - 1))
        self._tabs.blockSignals(True)
        self._tabs.setCurrentIndex(tab)
        self._tabs.blockSignals(False)

        self._stack.setCurrentWidget(self._content)

    def _fixture_row(self, fixture: base.MatchRef, team_id: int) -> _FixtureRow:
        is_home = fixture.home.team_id == team_id
        if fixture.is_completed and fixture.home_goals is not None:
            own_goals = fixture.home_goals if is_home else fixture.away_goals
            opponent_goals = fixture.away_goals if is_home else fixture.home_goals
            score = f"{own_goals}-{opponent_goals}"
            result = "胜" if own_goals > opponent_goals else ("平" if own_goals == opponent_goals else "负")
        else:
            score = "未赛"
            result = "未赛"
        opponent = fixture.away if is_home else fixture.home
        return _FixtureRow(
            match=fixture,
            week_number=fixture.week_number,
            competition=fixture.competition,
            round_number=fixture.round_number,
            side="主场" if is_home else "客场",
            opponent=opponent.display_name,
            opponent_team_id=opponent.team_id,
            score=score,
            result=result,
        )

    def _transfer_row(self, direction: str, line: team_queries.TeamTransferRow) -> _TransferRow:
        return _TransferRow(
            direction=direction,
            player=line.player,
            player_name=line.player.display_name,
            position=line.player.position,
            window=f"{line.window} 第 {line.week_number} 周",
            counterpart=line.counterpart,
            counterpart_name=line.counterpart.display_name,
            status=line.status,
            market_value=_market_value(line.market_value),
        )

    # -- 路由解析（行激活 + 列级链接） -------------------------------------------

    def _route_season(self) -> Optional[int]:
        route = self.current_route()
        return route.int_param("season") if route is not None else None

    def _player_route(self, player: base.PlayerRef) -> Optional[navigation.Route]:
        season = self._route_season()
        if season is None:
            return None
        return navigation.Route("player", player=player.player_id, season=season)

    def _squad_route_for_row(self, row: _SquadRow) -> Optional[navigation.Route]:
        return self._player_route(row.player)

    def _fixture_route_for_row(self, row: _FixtureRow) -> Optional[navigation.Route]:
        return navigation.Route("match", match=row.match.match_id)

    def _fixture_opponent_route(self, row: _FixtureRow) -> Optional[navigation.Route]:
        season = self._route_season()
        if season is None:
            return None
        return navigation.Route("team", team=row.opponent_team_id, season=season)

    def _transfer_route_for_row(self, row: _TransferRow) -> Optional[navigation.Route]:
        return self._transfer_player_route(row)

    def _transfer_player_route(self, row: _TransferRow) -> Optional[navigation.Route]:
        return self._player_route(row.player)

    def _transfer_counterpart_route(self, row: _TransferRow) -> Optional[navigation.Route]:
        season = self._route_season()
        if season is None:
            return None
        return navigation.Route("team", team=row.counterpart.team_id, season=season)

    # -- 内容型页签构建 ---------------------------------------------------------

    def _replace_scroll_content(self, scroll: QScrollArea, widget: QWidget) -> None:
        old = scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        scroll.setWidget(widget)

    def _build_overview(self, profile: team_queries.TeamSeasonProfile, season: int) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 20)
        layout.setSpacing(14)

        # 积分榜行完整卡片。
        line = profile.standings_row
        standings_card = CardFrame("积分榜", f"第 {season} 赛季 · {profile.season_division}")
        metrics_widget = QWidget(standings_card)
        metrics_layout = QGridLayout(metrics_widget)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(16)
        rank_text = f"第 {line.rank} 名" if line.rank is not None else "未排名"
        metric_values: List[Tuple[str, str]] = [
            ("名次", rank_text),
            ("已赛", str(line.played)),
            ("胜", str(line.wins)),
            ("平", str(line.draws)),
            ("负", str(line.losses)),
            ("进球", str(line.goals_for)),
            ("失球", str(line.goals_against)),
            ("净胜球", f"{line.goals_for - line.goals_against:+d}"),
            ("积分", str(line.points)),
        ]
        self._overview_metrics = dict(metric_values)
        for index, (label, value) in enumerate(metric_values):
            metrics_layout.addWidget(self._metric_cell(label, value), index // 5, index % 5)
        standings_card.body_layout.addWidget(metrics_widget)

        # 该赛季球队荣誉。
        honors_card = CardFrame("本赛季球队荣誉", None)
        honors_row = QHBoxLayout()
        honors_row.setContentsMargins(0, 0, 0, 0)
        honors_row.setSpacing(8)
        if profile.team_honors:
            for label in profile.team_honors:
                honors_row.addWidget(_badge(label, "#f9c74f", "#33270a"))
        else:
            honors_row.addWidget(self._muted_label("本赛季暂无荣誉"))
        honors_row.addStretch(1)
        honors_holder = QWidget(honors_card)
        honors_holder.setLayout(honors_row)
        honors_card.body_layout.addWidget(honors_holder)

        # 双栏：左侧积分榜、右侧本赛季荣誉，降低单列纵向空白。
        overview_row = QHBoxLayout()
        overview_row.setContentsMargins(0, 0, 0, 0)
        overview_row.setSpacing(12)
        overview_row.addWidget(standings_card, 3)
        overview_row.addWidget(honors_card, 2)
        layout.addLayout(overview_row)

        # 联赛走势摘要：最近 5 场联赛结果，胜/平/负徽标序列（按周倒序）。
        form_card = CardFrame("联赛走势", "最近 5 场联赛结果（按周倒序）")
        form_row = QHBoxLayout()
        form_row.setContentsMargins(0, 0, 0, 0)
        form_row.setSpacing(16)
        form = self._league_form(profile)
        if form:
            for outcome, caption in form:
                form_row.addWidget(self._form_cell(outcome, caption))
        else:
            form_row.addWidget(self._muted_label("本赛季联赛尚未开赛。"))
        form_row.addStretch(1)
        form_holder = QWidget(form_card)
        form_holder.setLayout(form_row)
        form_card.body_layout.addWidget(form_holder)
        layout.addWidget(form_card)

        layout.addStretch(1)
        return container

    def _league_form(self, profile: team_queries.TeamSeasonProfile) -> List[Tuple[str, str]]:
        team_id = profile.identity.team_id
        played = [
            fixture
            for fixture in profile.fixtures
            if fixture.competition == profile.season_division
            and fixture.is_completed
            and fixture.home_goals is not None
        ]
        played.sort(key=lambda fixture: (fixture.week_number, fixture.round_number), reverse=True)
        form: List[Tuple[str, str]] = []
        for fixture in played[:5]:
            is_home = team_id is not None and fixture.home.team_id == team_id
            own_goals = fixture.home_goals if is_home else fixture.away_goals
            opponent_goals = fixture.away_goals if is_home else fixture.home_goals
            outcome = "胜" if own_goals > opponent_goals else ("平" if own_goals == opponent_goals else "负")
            opponent = fixture.away if is_home else fixture.home
            caption = (
                f"第 {fixture.week_number} 周 {'主' if is_home else '客'}"
                f" {own_goals}-{opponent_goals} {opponent.display_name}"
            )
            form.append((outcome, caption))
        return form

    def _build_history_content(self, history_rows: Sequence[_SeasonHistoryRow]) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 20)
        layout.setSpacing(10)
        layout.addWidget(section_header("赛季历史", "每赛季级别、名次、联赛与杯赛结果、荣誉积分"))
        self._history_table = None
        self._history_rows = list(history_rows)
        if not any(row.has_archive for row in history_rows):
            layout.addWidget(
                self._muted_label("该球队还没有已归档的历史赛季（当前赛季进行中或归档尚未生成）。")
            )
            layout.addStretch(1)
            return container
        table = _GridTable(
            ("赛季", "级别", "名次", "联赛结果", "优胜者杯", "挑战杯", "超级杯", "荣誉积分"),
            stretch_column=0,
        )
        for row in history_rows:
            table.add_row(
                [
                    f"第 {row.season_number} 赛季",
                    row.level,
                    row.rank,
                    row.league_result,
                    row.winners_cup,
                    row.challenge_cup,
                    row.super_cup,
                    row.honor_points,
                ]
            )
        self._history_table = table
        layout.addWidget(table)
        layout.addStretch(1)
        return container

    def _build_awards_content(self, profile: team_queries.TeamSeasonProfile, season: int) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 20)
        layout.setSpacing(10)

        # 球队荣誉（单独区块，避免与球员个人奖项混淆）。
        layout.addWidget(section_header("球队荣誉", f"第 {season} 赛季球队成绩标签"))
        honors_row = QHBoxLayout()
        honors_row.setContentsMargins(0, 0, 0, 0)
        honors_row.setSpacing(8)
        if profile.team_honors:
            for label in profile.team_honors:
                honors_row.addWidget(_badge(label, "#f9c74f", "#33270a"))
        else:
            honors_row.addWidget(self._muted_label("本赛季暂无荣誉"))
        honors_row.addStretch(1)
        honors_holder = QWidget(container)
        honors_holder.setLayout(honors_row)
        layout.addWidget(honors_holder)

        # 球员个人奖项。
        layout.addWidget(section_header("球员个人奖项", f"第 {season} 赛季该队球员获得的个人奖项"))
        self._awards_table = None
        if profile.player_awards:
            table = _GridTable(("球员", "奖项类型", "赛事", "名次", "分数"), stretch_column=0)
            for award in profile.player_awards:
                rank_text = str(award.rank) if award.rank is not None else "—"
                score_text = f"{award.score:.2f}" if award.score is not None else "—"
                competition_cell: object = "—"
                if award.competition:
                    competition_cell = EntityLink(
                        award.competition,
                        navigation.Route(
                            "competition", competition=award.competition, season=season
                        ),
                        self._context.navigate,
                    )
                table.add_row(
                    [
                        EntityLink(
                            award.player.display_name,
                            self._player_route(award.player),
                            self._context.navigate,
                        ),
                        _AWARD_TYPE_LABELS.get(award.award_type, award.award_type),
                        competition_cell,
                        rank_text,
                        score_text,
                    ]
                )
            self._awards_table = table
            layout.addWidget(table)
        else:
            layout.addWidget(self._muted_label("本赛季暂无个人奖项。"))
        layout.addStretch(1)
        return container

    # -- 赛季历史数据 -----------------------------------------------------------

    def _build_history_rows(
        self,
        conn: sqlite3.Connection,
        seasons: Sequence[base.SeasonRef],
        profile: team_queries.TeamSeasonProfile,
    ) -> List[_SeasonHistoryRow]:
        """一行一赛季：名次取 premier/second order，杯赛结果取归档荣誉表。"""

        rows: List[_SeasonHistoryRow] = []
        team_id = profile.identity.team_id
        team_name = profile.identity.display_name
        for ref in seasons:
            try:
                detail = history_queries.get_season_archive_detail(conn, ref.season_number)
            except KeyError:
                status_label = "进行中" if not ref.is_completed else "无归档"
                rows.append(
                    _SeasonHistoryRow(
                        season_number=ref.season_number,
                        has_archive=False,
                        level="—",
                        rank="—",
                        league_result=status_label,
                        winners_cup="—",
                        challenge_cup="—",
                        super_cup="—",
                        honor_points="—",
                    )
                )
                continue
            level = "—"
            rank = "—"
            for order, division in (
                (detail.premier_order, base.COMPETITION_PREMIER),
                (detail.second_order, base.COMPETITION_SECOND),
            ):
                for order_line in order:
                    if order_line.team.team_id == team_id:
                        level = division
                        rank = f"第 {order_line.rank} 名"
                        break
                if rank != "—":
                    break
            honor = next(
                (item for item in detail.team_honor_table if item.team_name == team_name),
                None,
            )
            rows.append(
                _SeasonHistoryRow(
                    season_number=ref.season_number,
                    has_archive=True,
                    level=level if level != "—" else (honor.division if honor else "—"),
                    rank=rank,
                    league_result=honor.league_result if honor and honor.league_result else "—",
                    winners_cup=honor.winners_cup_result if honor and honor.winners_cup_result else "—",
                    challenge_cup=honor.challenge_cup_result if honor and honor.challenge_cup_result else "—",
                    super_cup=honor.super_cup_result if honor and honor.super_cup_result else "—",
                    honor_points=str(honor.honor_points) if honor else "—",
                )
            )
        rows.sort(key=lambda row: row.season_number, reverse=True)
        return rows

    # -- 小组件 -----------------------------------------------------------------

    def _metric_cell(self, label: str, value: str) -> QWidget:
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(2)
        caption = QLabel(label)
        caption.setStyleSheet(f"color: {TEXT_COLOR_MUTED}; font-size: 12px; background: transparent;")
        value_label = QLabel(value)
        value_label.setStyleSheet(
            f"color: {TEXT_COLOR_BRIGHT}; font-size: 20px; font-weight: 800; background: transparent;"
        )
        cell_layout.addWidget(caption)
        cell_layout.addWidget(value_label)
        return cell

    def _form_cell(self, outcome: str, caption: str) -> QWidget:
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(4)
        colors = _RESULT_BADGE_COLORS.get(outcome, ("#94a3b8", "#0f172a"))
        badge = _badge(outcome, colors[0], colors[1])
        detail = QLabel(caption)
        detail.setStyleSheet(f"color: {TEXT_COLOR_MUTED}; font-size: 12px; background: transparent;")
        cell_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
        cell_layout.addWidget(detail, 0, Qt.AlignmentFlag.AlignHCenter)
        return cell

    def _muted_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {TEXT_COLOR_MUTED}; background: transparent;")
        return label

    def _set_empty_state(self, title: str, description: str) -> None:
        previous = self._empty
        self._empty = EmptyState(title, description)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentWidget(self._empty)
        if previous is not None:
            self._stack.removeWidget(previous)
            previous.deleteLater()

    # -- 事件处理 ----------------------------------------------------------------

    def _rebuild_season_combo(self, seasons: Sequence[base.SeasonRef], season: int) -> None:
        """重建赛季选择器并定位到路由赛季；切换由 ``_on_season_selected`` 导航。"""

        self._season_combo.blockSignals(True)
        try:
            self._season_combo.clear()
            for ref in seasons:
                self._season_combo.addItem(f"第 {ref.season_number} 赛季", ref.season_number)
            numbers = [ref.season_number for ref in seasons]
            if season in numbers:
                self._season_combo.setCurrentIndex(numbers.index(season))
        finally:
            self._season_combo.blockSignals(False)

    def _on_season_selected(self, index: int) -> None:
        route = self.current_route()
        data = self._season_combo.itemData(index)
        if route is None or data is None:
            return
        season = int(data)
        if season == route.int_param("season"):
            return
        self.navigate(navigation.Route("team", team=route.int_param("team"), season=season))

    def _on_tab_changed(self, index: int) -> None:
        self._save_profile_state()

    def _on_squad_real_only_toggled(self, _checked: bool) -> None:
        self._refresh_squad()
        self._save_profile_state()

    def _save_profile_state(self) -> None:
        self.save_state(
            {
                "tab": int(self._tabs.currentIndex()),
                "squadRealOnly": "1" if self._squad_real_only.isChecked() else "0",
            }
        )

    def eventFilter(self, obj: object, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Leave):
            view = obj.parent() if obj is not None else None
            columns = self._pointer_tables.get(id(view)) if view is not None else None
            if columns is not None and hasattr(view, "indexAt"):
                if event.type() == QEvent.Type.Leave:
                    view.unsetCursor()
                else:
                    position = event.position().toPoint()
                    index = view.indexAt(position)
                    if index.isValid() and index.column() in columns:
                        view.setCursor(Qt.CursorShape.PointingHandCursor)
                    else:
                        view.unsetCursor()
        return super().eventFilter(obj, event)
