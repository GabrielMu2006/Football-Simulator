"""历史与荣誉页（阶段 5 重写，实施方案 §8.8）。

Route：``Route("history", season=<n>?)``——season 可选；无参默认最新一个
已归档（已完成）赛季。

数据源（只读查询，UI 不读 raw 归档字典）：
- ``history_queries.list_season_summaries`` / 直接查询 ``season_archives``
  得到"有归档的赛季"列表（赛季选择器只列这些赛季）；
- ``history_queries.get_season_archive_detail``：premier_order/second_order
  （带 rank 的 SeasonTeamOrder）、cup_champions、top20（Top20Line，player 已
  收敛为稳定 ID）、competition_awards、team_honor_table、
  player_settlement_points；
- ``competition_queries.league_standings_rows``：为"最终排名"页签补充
  赛/胜/平/负/进失/积分（归档赛季的比赛数据仍保留在 matches 表）；名次
  一律以归档 premier_order/second_order 的 rank 为准，统计按队名联接，
  联接不到的单元格显示"—"（不虚构数据）；
- ``SELECT team_id FROM teams WHERE name = ?``：冠军/球队名 → 稳定 team_id。

页签结构（QTabWidget；页签选择经 ``save_state``/``restore_state`` 记忆）：

- 赛季总览：五项冠军（球队可点）+ 年度 Top20 前三名摘要 + 赛季入口
  （``Route("season_overview", season=<n>)``）——内容型页签，单外层滚动；
- 最终排名：一级/次级联赛完整名次（全高 EntityTable，级别下拉切换，
  两个联赛各自以全高表呈现全部名次），球队可点；
- 个人奖项：年度 Top20 全 20 行（球员可点；评分/身价取该球员"赛季末"
  结算点，缺失显示"—"）+ 各赛事个人奖（射手王/助攻王/MVP，球员与赛事
  均可点）；
- 球队荣誉：team_honor_table 全表（球队可点 + 各项荣誉结果 + 荣誉积分）；
- 结算轨迹：player_settlement_points 汇总表（球员可点），提供筛选输入
  而非截断主表（§8.2 规则 5：完整呈现 + 筛选/排序）。

链接合同（§7.2）：冠军球队 → ``Route("team", team=<id>, season=<该赛季>)``；
Top20/奖项/结算球员 → ``Route("player", player=<稳定ID>, season=<该赛季>)``
（归档 player_key 经 ``canonical_player_id_for_name`` 收敛，查询层已保证
``real::<slug>`` 形态）；赛事名 → ``Route("competition", competition=<赛事>,
season=<该赛季>)``；赛季入口 → ``Route("season_overview", season=<n>)``。

滚动面归属（§8.2）：页签式页面——每个页签恰有一个纵向滚动面。表格页签
为 EntityTable 内部的 QTableView（外层不再套 QScrollArea）；"赛季总览"为
单外层 QScrollArea 且内容完整展开；无任何小框内滚动。

delegate 生命周期约定（重要，经崩溃报告与压力矩阵实证，勿改）：
``setItemDelegateForColumn`` 不取得所有权 → delegate 必须挂 view 为 Qt
parent（与视图同生命周期），同时页面持有 Python 引用，且引用列表**只增
不清**——shiboken 在最后一个 Python 引用消失时就会删除 C++ 对象（即使挂
了 parent），而旧视图的列映射仍指向该对象，此后任意 GC/绘制时刻都会
SIGSEGV。本页所有表格在 ``_build_ui`` 一次构建、随刷新只换行数据，delegate
全部存入 ``self._delegates``，从不重建、从不清空。

数据口径：历史页的奖项/结算数据仅覆盖真实球员（默认球员不参与评分/身价结算），因此无需“只显示真实球员”过滤。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base, competition_queries, history_queries
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    ColumnSpec,
    EmptyState,
    EntityLink,
    EntityTable,
    FilterBar,
    LINK_COLOR,
    PageHeader,
    TEXT_COLOR_BRIGHT,
    TEXT_COLOR_MUTED,
)
from football_simulator.ui_v2.components.team_crest import draw_team_crest
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext
from football_simulator.ui_v2.widgets import section_header

_TAB_OVERVIEW = 0
_TAB_STANDINGS = 1
_TAB_AWARDS = 2
_TAB_HONORS = 3
_TAB_ALL_HONORS = 4
_TAB_SETTLEMENT = 5
_TAB_TITLES: Tuple[str, ...] = (
    "赛季总览", "最终排名", "个人奖项", "球队荣誉", "总荣誉榜", "结算轨迹",
)

_DIVISION_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    (base.COMPETITION_PREMIER, "premier"),
    (base.COMPETITION_SECOND, "second"),
)

_AWARD_TYPE_LABELS: Dict[str, str] = {
    "top_scorer": "射手王",
    "assist_leader": "助攻王",
    "mvp": "MVP",
}
_AWARD_TYPE_ORDER: Tuple[str, ...] = ("top_scorer", "assist_leader", "mvp")

_STAGE_FINAL = "赛季末"

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = f"color: {TEXT_COLOR_BRIGHT}; background: transparent; font-weight: 700;"

_SEASON_BUTTON_STYLE = (
    "QPushButton#historySeasonButton {"
    "  padding: 10px 18px; border-radius: 10px; background: #122238;"
    "  color: #cbd7e6; border: 1px solid #263b5b; font-weight: 700; }"
    "QPushButton#historySeasonButton:hover { background: #1b304d; color: #f8fbff; }"
    "QPushButton#historySeasonButton:checked { background: #1167d8; color: #ffffff;"
    "  border: 1px solid #1167d8; }"
)

_STANDING_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("rank", "名次", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("team_name", "球队", width=200, stretch=True),
    ColumnSpec("played", "赛", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("wins", "胜", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("draws", "平", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("losses", "负", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goals_for", "进", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goals_against", "失", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goal_diff", "净", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("points", "积分", width=72, alignment=Qt.AlignmentFlag.AlignRight),
)

_TOP20_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("rank", "名次", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("player_name", "球员", width=180, stretch=True),
    ColumnSpec("position", "位置", width=64, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("team_name", "球队", width=180),
    ColumnSpec("rating", "赛季末评分", width=104, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("market_value", "赛季末身价", width=104, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("score", "Top20 分数", width=96, alignment=Qt.AlignmentFlag.AlignRight),
)

_HONOR_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("team_name", "球队", width=190, stretch=True),
    ColumnSpec("division", "级别", width=96, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("league_result", "联赛", width=96),
    ColumnSpec("winners_cup_result", "优胜者杯", width=104),
    ColumnSpec("challenge_cup_result", "挑战杯", width=96),
    ColumnSpec("super_cup_result", "超级杯", width=96),
    ColumnSpec("honor_points", "荣誉积分", width=88, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("total_titles", "总冠军", width=76, alignment=Qt.AlignmentFlag.AlignRight),
)

_ALL_HONORS_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("team_name", "球队", width=200, stretch=True),
    ColumnSpec("seasons", "赛季数", width=76, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("league_titles", "联赛冠军", width=84, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("winners_cup_titles", "优胜者杯", width=84, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("challenge_cup_titles", "挑战杯", width=84, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("super_cup_titles", "超级杯", width=84, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("honor_points", "荣誉积分", width=88, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("total_titles", "总冠军", width=76, alignment=Qt.AlignmentFlag.AlignRight),
)

_SETTLEMENT_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("player_name", "球员", width=180, stretch=True),
    ColumnSpec("team_name", "球队", width=170),
    ColumnSpec("season_number", "赛季", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("stage", "阶段", width=84, alignment=Qt.AlignmentFlag.AlignHCenter),
    ColumnSpec("week_number", "结算周", width=76, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("season_rating", "评分", width=96, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("market_value", "身价", width=100, alignment=Qt.AlignmentFlag.AlignRight),
)

_GRID_HEADER_STYLE = (
    "background: #172942; color: #dfe9f7; font-weight: 800;"
    "padding: 8px 10px; border-bottom: 1px solid #23344d;"
)
_GRID_CELL_STYLE = "color: #e8eef7; background: transparent; padding: 6px 10px; border-bottom: 1px solid #223653;"


class _Millions(float):
    """身价数值：``float`` 子类（EntityTable 仍按数值排序），显示带 M 后缀。"""

    def __new__(cls, value: float) -> "_Millions":
        return super().__new__(cls, value)

    def __str__(self) -> str:
        return f"{float(self):.2f}M"


def _market_value(value: Optional[float]) -> Optional[float]:
    """无身价（None）→ EntityTable 显示"—"，不伪造数值。"""

    return _Millions(value) if value is not None else None


# -- 页签行 DTO -----------------------------------------------------------


@dataclass(frozen=True)
class _StandingRow:
    """最终排名行：名次来自归档 order，统计按队名从联赛积分榜联接。"""

    rank: int
    team: base.TeamRef
    team_id: int
    team_name: str
    played: Optional[int]
    wins: Optional[int]
    draws: Optional[int]
    losses: Optional[int]
    goals_for: Optional[int]
    goals_against: Optional[int]
    goal_diff: Optional[int]
    points: Optional[int]


@dataclass(frozen=True)
class _Top20Row:
    line: history_queries.Top20Line
    rank: int
    player_id: str
    player_name: str
    position: str
    team_name: str
    rating: Optional[float]
    market_value: Optional[float]
    score: float


@dataclass(frozen=True)
class _HonorRow:
    line: history_queries.TeamHonorLine
    team_id: Optional[int]
    team_name: str
    division: str
    league_result: str
    winners_cup_result: str
    challenge_cup_result: str
    super_cup_result: str
    honor_points: int
    total_titles: int


@dataclass(frozen=True)
class _AllHonorRow:
    team_name: str
    seasons: int
    league_titles: int
    winners_cup_titles: int
    challenge_cup_titles: int
    super_cup_titles: int
    honor_points: int
    total_titles: int


@dataclass(frozen=True)
class _SettlementRow:
    point: history_queries.SettlementPointLine
    player_id: str
    player_name: str
    team_name: str
    season_number: int
    stage: str
    week_number: int
    season_rating: Optional[float]
    market_value: Optional[float]


# -- 列级 delegate ---------------------------------------------------------


class _LinkColumnDelegate(QStyledItemDelegate):
    """把一列文本渲染为实体链接（§7.2）：青色、hover 下划线、单击导航。

    生命周期约定见模块 docstring：parent=view、页面持有引用且只增不清。
    行的其余区域仍走 EntityTable 默认行为（双击/Enter 打开行主路由）。
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
        self._show_crest = crest

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text or str(text) == "—":
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = opt.font
        font.setUnderline(bool(opt.state & QStyle.State.State_MouseOver))
        painter.setFont(font)
        painter.setPen(QColor(LINK_COLOR))
        rect = opt.rect.adjusted(8, 0, -8, 0)
        if self._show_crest:
            crest_size = min(rect.height(), 38)
            crest_rect = QRect(rect.left(), rect.top() + (rect.height() - crest_size) // 2, crest_size, crest_size)
            draw_team_crest(painter, crest_rect, str(text), size=crest_size)
            rect = QRect(
                rect.left() + crest_size + 8,
                rect.top(),
                max(0, rect.width() - crest_size - 8),
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


def _column_index(columns: Sequence[ColumnSpec], key: str) -> int:
    for index, column in enumerate(columns):
        if column.key == key:
            return index
    raise ValueError(f"未知列：{key}")


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # 避免 setParent(None) 产生临时顶层窗口（macOS 全屏退场触发点之一）。
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


# -- 页面 -------------------------------------------------------------------


class HistoryPage(EntityPageBase):
    """历史与荣誉：按归档赛季查看冠军、最终排名、奖项、球队荣誉与结算轨迹。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._season: Optional[int] = None
        self._archived_seasons: Tuple[int, ...] = ()
        self._current_detail: Optional[history_queries.SeasonArchiveDetail] = None
        self._team_ids: Dict[str, int] = {}
        self._standings_rows: Dict[str, Tuple[_StandingRow, ...]] = {}
        self._top20_rows: Tuple[_Top20Row, ...] = ()
        self._settlement_rows: Tuple[_SettlementRow, ...] = ()
        self._page_header: Optional[PageHeader] = None
        self._season_buttons: Dict[int, QPushButton] = {}
        self._division_combo: Optional[QComboBox] = None
        self._overview_layout: Optional[QVBoxLayout] = None
        self._champion_links: List[EntityLink] = []
        self._top3_links: List[EntityLink] = []
        self._award_links: List[EntityLink] = []
        self._season_entry_link: Optional[EntityLink] = None
        self._settlement_filter: Optional[FilterBar] = None
        # delegate 生命周期约定（勿改）：引用列表只增不清。
        self._delegates: List[_LinkColumnDelegate] = []
        # 悬停可点列 → 手型光标（§7.2 指针变化）：view id -> 可点列集合。
        self._pointer_tables: Dict[int, set] = {}
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；内容在 refresh 中重建/换行） ---------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        self._stack = QStackedWidget(self)

        # 页头与赛季选择条放在 stack 之外：即使没有已归档赛季（空状态）也可见。
        self._page_header = PageHeader(
            "历史与荣誉",
            breadcrumbs=[],
            navigator=self._context.navigate,
            parent=self,
        )
        root.addWidget(self._page_header, 0)

        season_strip = QScrollArea(self)
        season_strip.setObjectName("historySeasonStrip")
        season_strip.setWidgetResizable(True)
        season_strip.setFrameShape(QFrame.Shape.NoFrame)
        season_strip.setFixedHeight(62)
        season_strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        season_strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        strip_content = QWidget(season_strip)
        self._season_strip_layout = QHBoxLayout(strip_content)
        self._season_strip_layout.setContentsMargins(0, 4, 0, 4)
        self._season_strip_layout.setSpacing(8)
        self._season_strip_placeholder = QLabel("暂无已归档赛季（赛季结束后归档到此页）")
        self._season_strip_placeholder.setStyleSheet(_MUTED_STYLE)
        self._season_strip_layout.addWidget(self._season_strip_placeholder)
        season_strip.setWidget(strip_content)
        root.addWidget(season_strip, 0)
        root.addWidget(self._stack, 1)

        self._content = QWidget(self)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        self._tabs = QTabWidget(self._content)

        # 页签 0：赛季总览（内容型，单外层滚动面，内容完整展开）。
        self._overview_scroll = QScrollArea(self)
        self._overview_scroll.setObjectName("historyOverviewScroll")
        self._overview_scroll.setWidgetResizable(True)
        self._overview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        overview_body = QWidget(self._overview_scroll)
        self._overview_layout = QVBoxLayout(overview_body)
        self._overview_layout.setContentsMargins(0, 4, 0, 4)
        self._overview_layout.setSpacing(10)
        self._overview_scroll.setWidget(overview_body)
        self._tabs.addTab(self._overview_scroll, _TAB_TITLES[_TAB_OVERVIEW])

        # 页签 1：最终排名（级别切换 + 全高 EntityTable，唯一滚动面为表格）。
        standings_page = QWidget(self)
        standings_layout = QVBoxLayout(standings_page)
        standings_layout.setContentsMargins(0, 4, 0, 0)
        standings_layout.setSpacing(8)
        division_row = QWidget(standings_page)
        division_layout = QHBoxLayout(division_row)
        division_layout.setContentsMargins(0, 0, 0, 0)
        division_layout.setSpacing(6)
        division_caption = QLabel("级别", division_row)
        division_caption.setStyleSheet(_MUTED_STYLE)
        self._division_combo = QComboBox(division_row)
        self._division_combo.setObjectName("historyDivisionSelector")
        for division, _category in _DIVISION_CATEGORIES:
            self._division_combo.addItem(division)
        self._division_combo.currentIndexChanged.connect(self._on_division_changed)
        division_layout.addWidget(division_caption)
        division_layout.addWidget(self._division_combo)
        division_layout.addStretch(1)
        standings_layout.addWidget(division_row)
        self._standings_table = EntityTable(_STANDING_COLUMNS, navigator=self._context.navigate, parent=self)
        self._standings_empty_slot = self._make_empty_slot()
        self._standings_stack = self._make_tab_stack(self._standings_table, self._standings_empty_slot)
        standings_layout.addWidget(self._standings_stack, 1)
        self._tabs.addTab(standings_page, _TAB_TITLES[_TAB_STANDINGS])

        # 页签 2：个人奖项（Top20 完整展开 + 三个奖项分区；整个页签一个滚动面）。
        awards_scroll = QScrollArea(self)
        awards_scroll.setObjectName("historyAwardsScroll")
        awards_scroll.setWidgetResizable(True)
        awards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        awards_body = QWidget(awards_scroll)
        awards_layout = QVBoxLayout(awards_body)
        awards_layout.setContentsMargins(0, 4, 0, 4)
        awards_layout.setSpacing(10)
        self._top20_table = EntityTable(_TOP20_COLUMNS, navigator=self._context.navigate, parent=self)
        self._top20_empty_slot = self._make_empty_slot()
        self._top20_stack = self._make_tab_stack(self._top20_table, self._top20_empty_slot)
        awards_layout.addWidget(self._top20_stack)
        self._awards_grid_slot = QWidget(awards_body)
        self._awards_grid_layout = QVBoxLayout(self._awards_grid_slot)
        self._awards_grid_layout.setContentsMargins(0, 0, 0, 0)
        self._awards_grid_layout.setSpacing(6)
        awards_layout.addWidget(self._awards_grid_slot)
        awards_layout.addStretch(1)
        awards_scroll.setWidget(awards_body)
        self._tabs.addTab(awards_scroll, _TAB_TITLES[_TAB_AWARDS])

        # 页签 3：球队荣誉（全高 EntityTable）。
        self._honor_table = EntityTable(_HONOR_COLUMNS, navigator=self._context.navigate, parent=self)
        self._honor_empty_slot = self._make_empty_slot()
        self._honor_stack = self._make_tab_stack(self._honor_table, self._honor_empty_slot)
        self._tabs.addTab(self._honor_stack, _TAB_TITLES[_TAB_HONORS])

        # 页签 4：总荣誉榜（跨赛季汇总，全高 EntityTable）。
        self._all_honors_table = EntityTable(_ALL_HONORS_COLUMNS, navigator=self._context.navigate, parent=self)
        self._all_honors_empty_slot = self._make_empty_slot()
        self._all_honors_stack = self._make_tab_stack(self._all_honors_table, self._all_honors_empty_slot)
        self._tabs.addTab(self._all_honors_stack, _TAB_TITLES[_TAB_ALL_HONORS])

        # 页签 5：结算轨迹（筛选输入 + 全高 EntityTable，完整呈现不截断）。
        settlement_page = QWidget(self)
        settlement_layout = QVBoxLayout(settlement_page)
        settlement_layout.setContentsMargins(0, 4, 0, 0)
        settlement_layout.setSpacing(8)
        self._settlement_filter = FilterBar(parent=settlement_page)
        self._settlement_filter.add_search("筛选球员或球队（留空显示全部）")
        self._settlement_filter.search_changed.connect(self._on_settlement_search)
        self._settlement_filter.add_reset()
        settlement_layout.addWidget(self._settlement_filter)
        self._settlement_table = EntityTable(_SETTLEMENT_COLUMNS, navigator=self._context.navigate, parent=self)
        self._settlement_empty_slot = self._make_empty_slot()
        self._settlement_stack = self._make_tab_stack(self._settlement_table, self._settlement_empty_slot)
        settlement_layout.addWidget(self._settlement_stack, 1)
        self._tabs.addTab(settlement_page, _TAB_TITLES[_TAB_SETTLEMENT])

        self._tabs.currentChanged.connect(self._on_tab_changed)
        content_layout.addWidget(self._tabs, 1)
        self._stack.addWidget(self._content)

        self._empty = EmptyState("还没有已归档的赛季", "")
        self._stack.addWidget(self._empty)

        self._install_delegates()

    def _make_empty_slot(self) -> QWidget:
        """空状态容器：本身不带滚动面，内容在刷新时填充。"""

        slot = QWidget(self)
        layout = QVBoxLayout(slot)
        layout.setContentsMargins(0, 0, 0, 0)
        return slot

    def _make_tab_stack(self, table: EntityTable, empty_slot: QWidget) -> QStackedWidget:
        stack = QStackedWidget(self)
        stack.addWidget(table)
        stack.addWidget(empty_slot)
        return stack

    def _show_empty_slot(self, stack: QStackedWidget, table: EntityTable, slot: QWidget, message: str, rows: int) -> None:
        """表格有行 → 显示表格；无行 → 在空槽位中显示明确文案。"""

        if rows > 0:
            stack.setCurrentWidget(table)
            return
        _clear_layout(slot.layout())
        stack.setCurrentWidget(slot)
        slot.layout().addWidget(EmptyState(message, "该赛季的归档中没有这一项数据。"))

    def _install_delegates(self) -> None:
        """列级链接 delegate：parent=view，页面持有引用且只增不清（勿改）。"""

        specs = (
            (self._standings_table, _STANDING_COLUMNS, "team_name", self._standing_team_route, True),
            (self._top20_table, _TOP20_COLUMNS, "player_name", self._top20_player_route, False),
            (self._honor_table, _HONOR_COLUMNS, "team_name", self._honor_team_route, True),
            (self._settlement_table, _SETTLEMENT_COLUMNS, "player_name", self._settlement_player_route, False),
        )
        for table, columns, key, resolver, show_crest in specs:
            index = _column_index(columns, key)
            delegate = _LinkColumnDelegate(
                table,
                resolver,
                columns[index].alignment,
                parent=table.view,
                crest=show_crest,
            )
            table.view.setItemDelegateForColumn(index, delegate)
            self._delegates.append(delegate)
            self._pointer_tables.setdefault(id(table.view), set()).add(index)
            table.view.viewport().installEventFilter(self)

    # -- 数据刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "history":
            return
        season_param = route.int_param("season")
        try:
            data = self._load_data(self.save_name(), season_param)
        except base.MissingSaveError as exc:
            self._show_empty("还没有可用的存档数据", str(exc), "请先在“存档”页新建或选择一个存档。")
            return
        except _NoArchiveError:
            self._show_empty(
                "还没有已归档的赛季",
                "当前存档还没有完成过任何完整赛季。",
                "完成一个完整赛季后，这里会显示该赛季的冠军、最终排名、奖项与球队荣誉。",
            )
            all_seasons, archived, _ = self._season_strip_data()
            self._render_season_strip(all_seasons, archived, None)
            return
        except _SeasonNotArchivedError as exc:
            self._show_empty(
                f"第 {exc.season_number} 赛季还没有赛季归档",
                "该赛季尚未完成归档，历史与荣誉只展示已归档（已完成）赛季。",
                "请使用赛季选择器切换到已归档的赛季。",
            )
            all_seasons, archived, _ = self._season_strip_data()
            self._render_season_strip(all_seasons, archived, exc.season_number)
            return
        except Exception as exc:  # 查询层异常统一进空状态
            self._show_empty("暂时无法加载历史数据", str(exc), None)
            return

        self._season = data.season_number
        self._archived_seasons = data.archived_seasons
        self._current_detail = data.detail
        self._team_ids = data.team_ids
        self._standings_rows = data.standings_rows
        self._top20_rows = data.top20_rows
        self._settlement_rows = data.settlement_rows
        self._render(data)

        state = self.stored_state()
        tab = state.get("tab")
        tab_index = int(tab) if isinstance(tab, (int, str)) and str(tab).isdigit() else _TAB_OVERVIEW
        if not 0 <= tab_index < self._tabs.count():
            tab_index = _TAB_OVERVIEW
        division = str(state.get("standingsDivision") or "")
        if division:
            index = self._division_combo.findText(division)
            if index >= 0:
                self._division_combo.blockSignals(True)
                self._division_combo.setCurrentIndex(index)
                self._division_combo.blockSignals(False)
        self._tabs.blockSignals(True)
        self._tabs.setCurrentIndex(tab_index)
        self._tabs.blockSignals(False)
        self._apply_settlement_filter(str(state.get("settlementFilter") or ""))
        self._stack.setCurrentWidget(self._content)

    def route_context(self) -> dict:
        if self._season is not None:
            return {"season": self._season}
        return {}

    # -- 只读取数 -------------------------------------------------------------

    def _load_data(self, save_name: str, season_param: Optional[int]) -> "_HistoryData":
        with base.open_read_connection(save_name) as conn:
            archived = tuple(
                int(row[0])
                for row in conn.execute(
                    """
                    SELECT s.season_number FROM season_archives AS sa
                    JOIN seasons AS s ON s.season_id = sa.season_id
                    ORDER BY s.season_number
                    """
                )
            )
            all_seasons = tuple(
                int(row[0])
                for row in conn.execute(
                    "SELECT season_number FROM seasons ORDER BY season_number"
                )
            )
            if not archived:
                raise _NoArchiveError()
            if season_param is None:
                season = archived[-1]  # 无参默认最新已归档赛季
            elif int(season_param) in archived:
                season = int(season_param)
            else:
                raise _SeasonNotArchivedError(int(season_param))

            detail = history_queries.get_season_archive_detail(conn, season)
            team_ids = {str(row[0]): int(row[1]) for row in conn.execute("SELECT name, team_id FROM teams")}
            season_id = base.season_id_for(conn, season)

            stats_by_name: Dict[str, competition_queries.StandingRow] = {}
            standings_rows: Dict[str, Tuple[_StandingRow, ...]] = {}
            for division, category in _DIVISION_CATEGORIES:
                order = detail.premier_order if category == "premier" else detail.second_order
                try:
                    computed = competition_queries.league_standings_rows(conn, season_id, season, category)
                except Exception:
                    computed = ()
                for row in computed:
                    stats_by_name[row.team_name] = row
                rows: List[_StandingRow] = []
                for entry in order:
                    stats = stats_by_name.get(entry.team.display_name)
                    rows.append(
                        _StandingRow(
                            rank=entry.rank,
                            team=entry.team,
                            team_id=entry.team.team_id,
                            team_name=entry.team.display_name,
                            played=stats.played if stats else None,
                            wins=stats.wins if stats else None,
                            draws=stats.draws if stats else None,
                            losses=stats.losses if stats else None,
                            goals_for=stats.goals_for if stats else None,
                            goals_against=stats.goals_against if stats else None,
                            goal_diff=(
                                (stats.goals_for - stats.goals_against)
                                if stats is not None and stats.goals_for is not None and stats.goals_against is not None
                                else None
                            ),
                            points=stats.points if stats else None,
                        )
                    )
                standings_rows[division] = tuple(rows)

            # Top20 行：评分/身价取该球员“赛季末”结算点（缺失显示“—”）。
            final_points: Dict[str, history_queries.SettlementPointLine] = {}
            for point in detail.player_settlement_points:
                if point.stage == _STAGE_FINAL:
                    final_points.setdefault(point.player.player_id, point)
            top20_rows = tuple(
                _Top20Row(
                    line=line,
                    rank=line.rank,
                    player_id=line.player.player_id,
                    player_name=line.player.display_name or line.label,
                    position=line.player.position,
                    team_name=line.team_name,
                    rating=(
                        final_points[line.player.player_id].season_rating
                        if line.player.player_id in final_points
                        else None
                    ),
                    market_value=(
                        final_points[line.player.player_id].market_value
                        if line.player.player_id in final_points
                        else None
                    ),
                    score=line.score,
                )
                for line in detail.top20
            )

            settlement_rows = tuple(
                _SettlementRow(
                    point=point,
                    player_id=point.player.player_id,
                    player_name=point.player.display_name,
                    team_name=point.team_name,
                    season_number=season,
                    stage=point.stage,
                    week_number=point.week_number,
                    season_rating=point.season_rating,
                    market_value=point.market_value,
                )
                for point in detail.player_settlement_points
            )

            all_honor_totals = history_queries.list_all_team_honor_totals(conn)
            return _HistoryData(
                season_number=season,
                archived_seasons=archived,
                all_seasons=all_seasons,
                detail=detail,
                team_ids=team_ids,
                standings_rows=standings_rows,
                top20_rows=top20_rows,
                settlement_rows=settlement_rows,
                all_honor_totals=all_honor_totals,
            )

    # -- 渲染 -----------------------------------------------------------------

    def _render(self, data: "_HistoryData") -> None:
        self._render_season_selector(data)
        self._render_overview(data)
        self._render_standings()
        self._render_top20()
        self._render_awards_grid(data)
        self._render_honors()
        self._render_all_honors(data)
        self._render_settlement(self._filter_settlement_rows(self._current_filter_text()))

    def _season_strip_data(self) -> Tuple[Tuple[int, ...], set, Optional[int]]:
        all_seasons: Tuple[int, ...] = ()
        archived: set = set()
        try:
            with base.open_read_connection(self.save_name()) as conn:
                all_seasons = tuple(
                    int(row[0])
                    for row in conn.execute(
                        "SELECT season_number FROM seasons ORDER BY season_number"
                    )
                )
                archived = {
                    int(row[0])
                    for row in conn.execute(
                        """
                        SELECT s.season_number FROM season_archives AS sa
                        JOIN seasons AS s ON s.season_id = sa.season_id
                        """
                    )
                }
        except Exception:
            pass
        return all_seasons, archived, self._season

    def _render_season_strip(
        self,
        all_seasons: Tuple[int, ...],
        archived_set: set,
        selected: Optional[int],
    ) -> None:
        layout = getattr(self, "_season_strip_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._season_buttons = {}
        if not all_seasons:
            empty_hint = QLabel("还没有任何赛季")
            empty_hint.setStyleSheet(_MUTED_STYLE)
            layout.addWidget(empty_hint)
        for season_number in all_seasons:
            suffix = "（已归档）" if season_number in archived_set else ""
            button = QPushButton(f"第 {season_number} 赛季{suffix}")
            button.setObjectName("historySeasonButton")
            button.setCheckable(True)
            button.setChecked(season_number == selected)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_SEASON_BUTTON_STYLE)
            button.clicked.connect(lambda _=False, s=season_number: self._navigate_season(s))
            layout.addWidget(button)
            self._season_buttons[season_number] = button
        layout.addStretch(1)

    def _render_season_selector(self, data: "_HistoryData") -> None:
        self._render_season_strip(data.all_seasons, set(data.archived_seasons), data.season_number)

    # -- 页签 0：赛季总览 ------------------------------------------------------

    def _render_overview(self, data: "_HistoryData") -> None:
        assert self._overview_layout is not None
        layout = self._overview_layout
        _clear_layout(layout)
        self._champion_links = []
        self._top3_links = []
        self._season_entry_link = None
        navigate = self._context.navigate
        detail = data.detail

        entry_row = QWidget(self._overview_scroll.widget())
        entry_layout = QHBoxLayout(entry_row)
        entry_layout.setContentsMargins(0, 0, 0, 0)
        entry_layout.setSpacing(8)
        entry_caption = QLabel("赛季入口", entry_row)
        entry_caption.setStyleSheet(_MUTED_STYLE)
        entry_link = EntityLink(
            f"进入第 {data.season_number} 赛季总览",
            Route("season_overview", season=data.season_number),
            navigate,
        )
        entry_link.setObjectName("historySeasonEntryLink")
        self._season_entry_link = entry_link
        entry_layout.addWidget(entry_caption)
        entry_layout.addWidget(entry_link)
        entry_layout.addStretch(1)
        layout.addWidget(entry_row)

        champions_frame = QFrame(entry_row.parentWidget())
        champions_frame.setObjectName("cardFrame")
        champions_layout = QVBoxLayout(champions_frame)
        champions_layout.setContentsMargins(12, 10, 12, 10)
        champions_layout.setSpacing(8)
        champions_layout.addWidget(section_header("赛季冠军", "该赛季五项冠军；单击球队名打开该赛季的球队页。"))
        champion_rows: Tuple[Tuple[str, Optional[str]], ...] = (
            ("一级联赛冠军", detail.premier_order[0].team.display_name if detail.premier_order else None),
            ("次级联赛冠军", detail.second_order[0].team.display_name if detail.second_order else None),
            ("优胜者杯冠军", detail.cup_champions.winners_cup),
            ("挑战杯冠军", detail.cup_champions.challenge_cup),
            ("超级杯冠军", detail.cup_champions.super_cup),
        )
        for title, champion in champion_rows:
            caption = QLabel(title)
            caption.setStyleSheet(_MUTED_STYLE)
            caption.setFixedWidth(120)
            row = QWidget(champions_frame)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            row_layout.addWidget(caption)
            if champion:
                team_id = data.team_ids.get(champion)
                if team_id is not None:
                    link = EntityLink(champion, Route("team", team=team_id, season=data.season_number), navigate)
                    self._champion_links.append(link)
                    row_layout.addWidget(link)
                else:
                    # 该队不在当前注册表（理论上不发生）；显示纯文本，不虚构链接。
                    label = QLabel(champion)
                    label.setStyleSheet(_BRIGHT_STYLE)
                    row_layout.addWidget(label)
            else:
                empty = QLabel("—（该赛季没有这项冠军记录）")
                empty.setStyleSheet(_MUTED_STYLE)
                row_layout.addWidget(empty)
            row_layout.addStretch(1)
            champions_layout.addWidget(row)
        top3_frame = QFrame(entry_row.parentWidget())
        top3_frame.setObjectName("cardFrame")
        top3_layout = QVBoxLayout(top3_frame)
        top3_layout.setContentsMargins(12, 10, 12, 10)
        top3_layout.setSpacing(8)
        top3_layout.addWidget(section_header("年度 Top20 前三名", "单击球员名打开该赛季的球员页；完整 20 行见“个人奖项”页签。"))
        top3 = self._top20_rows[:3]
        if top3:
            for row_data in top3:
                row = QWidget(top3_frame)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(10)
                rank_label = QLabel(f"第 {row_data.rank} 名")
                rank_label.setStyleSheet(_BRIGHT_STYLE)
                rank_label.setFixedWidth(64)
                row_layout.addWidget(rank_label)
                link = EntityLink(
                    row_data.player_name,
                    Route("player", player=row_data.player_id, season=data.season_number),
                    navigate,
                )
                self._top3_links.append(link)
                row_layout.addWidget(link)
                team_label = QLabel(row_data.team_name)
                team_label.setStyleSheet(_MUTED_STYLE)
                row_layout.addWidget(team_label)
                score_label = QLabel(f"{row_data.score:.2f} 分")
                score_label.setStyleSheet(_MUTED_STYLE)
                row_layout.addWidget(score_label)
                row_layout.addStretch(1)
                top3_layout.addWidget(row)
        else:
            empty = QLabel("该赛季还没有年度 Top20 数据")
            empty.setStyleSheet(_MUTED_STYLE)
            top3_layout.addWidget(empty)

        # 双栏：左侧冠军、右侧 Top20 前三，降低单列纵向空白。
        overview_row = QHBoxLayout()
        overview_row.setContentsMargins(0, 0, 0, 0)
        overview_row.setSpacing(12)
        overview_row.addWidget(champions_frame, 1)
        overview_row.addWidget(top3_frame, 1)
        layout.addLayout(overview_row)
        layout.addStretch(1)

    # -- 页签 1：最终排名 ------------------------------------------------------

    def _current_division(self) -> str:
        combo = self._division_combo
        if combo is None:
            return base.COMPETITION_PREMIER
        return combo.currentText() or base.COMPETITION_PREMIER

    def _render_standings(self) -> None:
        division = self._current_division()
        rows = self._standings_rows.get(division, ())
        self._standings_table.set_rows(
            rows,
            route_for_row=lambda row: self._team_route(row.team_id),
        )
        self._show_empty_slot(
            self._standings_stack,
            self._standings_table,
            self._standings_empty_slot,
            f"{division}没有最终排名数据",
            len(rows),
        )

    def _on_division_changed(self, _index: int) -> None:
        if self._season is None:
            return
        self._render_standings()
        self._save_state()

    # -- 页签 2：个人奖项 ------------------------------------------------------

    def _render_top20(self) -> None:
        rows = tuple(
            _Top20Row(
                line=row.line,
                rank=row.rank,
                player_id=row.player_id,
                player_name=row.player_name,
                position=row.position,
                team_name=row.team_name,
                rating=row.rating,
                market_value=_market_value(row.market_value),
                score=row.score,
            )
            for row in self._top20_rows
        )
        self._top20_table.set_rows(
            rows,
            route_for_row=lambda row: self._player_route(row.player_id),
        )
        # 完整展开：固定高度 = 表头 + 20 行 × 行高 + 边框，表格自身不滚动。
        header_h = self._top20_table.view.horizontalHeader().sizeHint().height()
        row_h = self._top20_table.view.verticalHeader().defaultSectionSize()
        self._top20_table.setFixedHeight(header_h + len(rows) * row_h + 2)
        self._show_empty_slot(
            self._top20_stack,
            self._top20_table,
            self._top20_empty_slot,
            "该赛季还没有年度 Top20 数据",
            len(rows),
        )

    def _render_awards_grid(self, data: "_HistoryData") -> None:
        """赛事个人奖：射手王 / 助攻王 / MVP 三个独立分区，不再合并成一个网格。"""

        assert self._awards_grid_layout is not None
        _clear_layout(self._awards_grid_layout)
        self._award_links = []
        navigate = self._context.navigate

        lines_by_type: Dict[str, List[history_queries.CompetitionAwardLine]] = {}
        for line in data.detail.competition_awards:
            lines_by_type.setdefault(line.award_type, []).append(line)

        rendered_any = False
        for award_type in _AWARD_TYPE_ORDER:
            label = _AWARD_TYPE_LABELS.get(award_type, award_type)
            self._awards_grid_layout.addWidget(
                section_header(label, f"该赛季各赛事的{label}得主（单击球员/赛事可跳转）")
            )
            lines = lines_by_type.get(award_type, [])
            if not lines:
                empty = QLabel("该赛季还没有该奖项数据")
                empty.setStyleSheet(_MUTED_STYLE)
                self._awards_grid_layout.addWidget(empty)
                continue
            rendered_any = True
            holder = QWidget(self._awards_grid_slot)
            grid = QGridLayout(holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(6)
            headers = ("赛事", "球员", "得分")
            for column, header_text in enumerate(headers):
                header_label = QLabel(header_text)
                header_label.setStyleSheet(_GRID_HEADER_STYLE)
                grid.addWidget(header_label, 0, column)
            for row_index, line in enumerate(lines, start=1):
                comp_link = EntityLink(
                    line.competition,
                    Route("competition", competition=line.competition, season=data.season_number),
                    navigate,
                )
                comp_link.setStyleSheet(comp_link.styleSheet() + f" {_GRID_CELL_STYLE}")
                self._award_links.append(comp_link)
                grid.addWidget(comp_link, row_index, 0)

                player_link = EntityLink(
                    line.player.display_name,
                    Route("player", player=line.player.player_id, season=data.season_number),
                    navigate,
                )
                player_link.setStyleSheet(player_link.styleSheet() + f" {_GRID_CELL_STYLE}")
                self._award_links.append(player_link)
                grid.addWidget(player_link, row_index, 1)

                score_label = QLabel("—" if line.score is None else f"{line.score:g}")
                score_label.setStyleSheet(_GRID_CELL_STYLE)
                grid.addWidget(score_label, row_index, 2)
            grid.setColumnStretch(len(headers), 1)
            self._awards_grid_layout.addWidget(holder)

        if not rendered_any:
            empty = QLabel("该赛季还没有赛事个人奖数据")
            empty.setStyleSheet(_MUTED_STYLE)
            self._awards_grid_layout.addWidget(empty)

    # -- 页签 3：球队荣誉 ------------------------------------------------------

    def _render_honors(self) -> None:
        rows = tuple(
            _HonorRow(
                line=line,
                team_id=self._team_ids.get(line.team_name),
                team_name=line.team_name,
                division=line.division,
                league_result=line.league_result,
                winners_cup_result=line.winners_cup_result,
                challenge_cup_result=line.challenge_cup_result,
                super_cup_result=line.super_cup_result,
                honor_points=line.honor_points,
                total_titles=line.total_titles,
            )
            for line in self._current_honor_lines()
        )
        self._honor_table.set_rows(
            rows,
            route_for_row=lambda row: self._team_route(row.team_id),
        )
        self._show_empty_slot(
            self._honor_stack,
            self._honor_table,
            self._honor_empty_slot,
            "该赛季还没有球队荣誉数据",
            len(rows),
        )

    def _current_honor_lines(self) -> Tuple[history_queries.TeamHonorLine, ...]:
        detail = self._current_detail
        return detail.team_honor_table if detail is not None else ()

    # -- 页签 4：总荣誉榜 ------------------------------------------------------

    def _render_all_honors(self, data: "_HistoryData") -> None:
        rows = tuple(
            _AllHonorRow(
                team_name=item.team_name,
                seasons=item.seasons,
                league_titles=item.league_titles,
                winners_cup_titles=item.winners_cup_titles,
                challenge_cup_titles=item.challenge_cup_titles,
                super_cup_titles=item.super_cup_titles,
                honor_points=item.honor_points,
                total_titles=item.total_titles,
            )
            for item in data.all_honor_totals
        )
        self._all_honors_table.set_rows(rows)
        self._show_empty_slot(
            self._all_honors_stack,
            self._all_honors_table,
            self._all_honors_empty_slot,
            "还没有球队荣誉数据（完成赛季归档后生成）",
            len(rows),
        )

    # -- 页签 5：结算轨迹 ------------------------------------------------------

    def _current_filter_text(self) -> str:
        if self._settlement_filter is None:
            return ""
        return self._settlement_filter.state().get("search", "")

    def _filter_settlement_rows(self, text: str) -> Tuple[_SettlementRow, ...]:
        needle = (text or "").strip().lower()
        if not needle:
            return self._settlement_rows
        return tuple(
            row
            for row in self._settlement_rows
            if needle in row.player_name.lower() or needle in row.team_name.lower()
        )

    def _render_settlement(self, rows: Tuple[_SettlementRow, ...]) -> None:
        display_rows = tuple(
            _SettlementRow(
                point=row.point,
                player_id=row.player_id,
                player_name=row.player_name,
                team_name=row.team_name,
                season_number=row.season_number,
                stage=row.stage,
                week_number=row.week_number,
                season_rating=row.season_rating,
                market_value=_market_value(row.market_value),
            )
            for row in rows
        )
        self._settlement_table.set_rows(
            display_rows,
            route_for_row=lambda row: self._player_route(row.player_id),
        )
        self._show_empty_slot(
            self._settlement_stack,
            self._settlement_table,
            self._settlement_empty_slot,
            "该赛季还没有球员结算数据",
            len(display_rows),
        )

    def _on_settlement_search(self, text: str) -> None:
        self._render_settlement(self._filter_settlement_rows(text))
        self._save_state()

    def _apply_settlement_filter(self, text: str) -> None:
        if self._settlement_filter is None:
            return
        self._settlement_filter.restore({"search": text} if text else {})
        self._render_settlement(self._filter_settlement_rows(text))

    # -- 状态记忆 -------------------------------------------------------------

    def _save_state(self) -> None:
        self.save_state(
            {
                "tab": int(self._tabs.currentIndex()),
                "standingsDivision": self._current_division(),
                "settlementFilter": self._current_filter_text(),
            }
        )

    def _on_tab_changed(self, _index: int) -> None:
        self._save_state()

    def _navigate_season(self, season_number: int) -> None:
        if season_number == self._season:
            return
        self.navigate(Route("history", season=season_number))

    # -- 路由解析（链接合同） ---------------------------------------------------

    def _team_route(self, team_id: Optional[int]) -> Optional[Route]:
        if team_id is None or team_id < 0 or self._season is None:
            return None
        return Route("team", team=int(team_id), season=int(self._season))

    def _player_route(self, player_id: str) -> Optional[Route]:
        if not player_id or self._season is None:
            return None
        return Route("player", player=player_id, season=int(self._season))

    def _standing_team_route(self, row: _StandingRow) -> Optional[Route]:
        return self._team_route(row.team_id)

    def _top20_player_route(self, row: _Top20Row) -> Optional[Route]:
        return self._player_route(row.player_id)

    def _honor_team_route(self, row: _HonorRow) -> Optional[Route]:
        return self._team_route(row.team_id)

    def _settlement_player_route(self, row: _SettlementRow) -> Optional[Route]:
        return self._player_route(row.player_id)

    # -- 指针反馈与空状态 -------------------------------------------------------

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

    def _show_empty(self, title: str, description: str, hint: Optional[str]) -> None:
        old = self._empty
        replacement = EmptyState(title, description, hint)
        self._stack.addWidget(replacement)
        self._stack.setCurrentWidget(replacement)
        self._empty = replacement
        if old is not None and old is not replacement:
            self._stack.removeWidget(old)
            old.deleteLater()


# -- 只读数据聚合与内部异常 ---------------------------------------------------


@dataclass(frozen=True)
class _HistoryData:
    season_number: int
    archived_seasons: Tuple[int, ...]
    all_seasons: Tuple[int, ...]
    detail: history_queries.SeasonArchiveDetail
    team_ids: Dict[str, int]
    standings_rows: Dict[str, Tuple[_StandingRow, ...]]
    top20_rows: Tuple[_Top20Row, ...]
    settlement_rows: Tuple[_SettlementRow, ...]
    all_honor_totals: Tuple[history_queries.AllTeamHonorTotal, ...]


class _NoArchiveError(Exception):
    """存档中还没有任何赛季归档。"""


class _SeasonNotArchivedError(Exception):
    def __init__(self, season_number: int) -> None:
        super().__init__(f"第 {season_number} 赛季还没有赛季归档。")
        self.season_number = season_number
