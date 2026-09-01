"""赛事详情页（阶段 5，实施方案 §8.4）：``Route("competition", competition=<赛事>, season=<n>)``。

数据源（全部查询驱动，不虚构数据）：

- ``competition_queries.list_competitions``：状态摘要（状态/完成进度/冠军）；
- ``competition_queries.get_competition_profile``：积分榜 / 杯赛签表（stage_rows
  按 round_number + match_id 确定性排序）/ 全部比赛 / 球员榜 / 奖项 / 冠军；
- ``history_queries.get_competition_history``：历史页签（历届冠军）；
- ``player_queries.list_players``（按赛事口径）：榜单行的"球队"列。

页签结构（QTabWidget；页签选择经 ``save_state``/``restore_state`` 记忆）：

- 概览：状态摘要 + 赛制说明 + 冠军与晋级（内容型页签，单外层 QScrollArea）；
- 积分榜 / 签表：联赛 → 积分榜 EntityTable；杯赛 → 小组积分表 + 淘汰树
  （唯一 QScrollArea 内完整展开）；升级附加赛 → 两回合比赛表 + 升级成功方
  横条。该页签按赛事类型在刷新时重建（§8.2：每页签恰一个纵向滚动面）；
- 赛程与结果：该赛事全部比赛的 EntityTable，行激活 → 比赛路由；
- 球员榜：射手/助攻/评分三榜合并的单表（"类型"列区分，各前 10），球员可点；
- 奖项：该赛事奖项表，球员可点；无奖项时空状态；
- 历史：一行一赛季（赛季、冠军、冠军球员），冠军可点球队。

滚动硬规则（§8.2）：联赛/附加赛表格页签的 EntityTable 是唯一纵向滚动面，
外层不套 QScrollArea；杯赛页签为唯一 QScrollArea 且内容完整展开；概览为
单外层 QScrollArea 且内容完整展开；杯赛未举办时各页签显示"本届未举办"
空状态，不引入额外滚动面。

链接合同（§7.2）：积分榜/签表/晋级方球队 → ``Route("team", ...)``；比赛行 →
``Route("match", ...)``；榜单/奖项/历史球员 → ``Route("player", ...)``；页头
赛事切换下拉与赛季选择器变化 → ``navigate`` 新路由（形成可后退历史）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from football_simulator.queries import base, competition_queries, history_queries, player_queries
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    TEXT_COLOR_BRIGHT,
    TEXT_COLOR_MUTED,
    ColumnSpec,
    EmptyState,
    EntityLink,
    EntityTable,
    PageHeader,
)
from football_simulator.ui_v2.components.team_crest import draw_team_crest
from football_simulator.ui_v2.design_tokens import (
    LINK_COLOR,
    LINK_DARK_BG,
    NEUTRAL_BADGE_BG,
    NEUTRAL_BADGE_FG,
    STATUS_NOT_HELD_BG,
    STATUS_NOT_HELD_FG,
    SUCCESS_BG,
    SUCCESS_COLOR,
)
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext
from football_simulator.ui_v2.widgets import CardFrame, section_header

_TAB_TITLES: Tuple[str, ...] = ("概览", "积分榜 / 签表", "赛程与结果", "球员榜", "奖项", "历史")
_TAB_OVERVIEW, _TAB_STAGE, _TAB_MATCHES, _TAB_LEADERS, _TAB_AWARDS, _TAB_HISTORY = range(6)

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = f"color: {TEXT_COLOR_BRIGHT}; background: transparent;"

_GRID_HEADER_STYLE = (
    "background: #172942; color: #dfe9f7; font-weight: 800;"
    "padding: 8px 10px; border-bottom: 1px solid #23344d;"
)
_GRID_CELL_STYLE = "color: #e8eef7; background: transparent; padding: 6px 10px; border-bottom: 1px solid #223653;"

# 状态徽标配色（前景色铺满圆角底、深色文字保证可读）。
_STATUS_BADGE_COLORS: Dict[str, Tuple[str, str]] = {
    competition_queries.STATUS_NOT_STARTED: (NEUTRAL_BADGE_FG, NEUTRAL_BADGE_BG),
    competition_queries.STATUS_IN_PROGRESS: (LINK_COLOR, LINK_DARK_BG),
    competition_queries.STATUS_FINISHED: (SUCCESS_COLOR, SUCCESS_BG),
    competition_queries.STATUS_NOT_HELD: (STATUS_NOT_HELD_FG, STATUS_NOT_HELD_BG),
}

_AWARD_TYPE_LABELS: Dict[str, str] = {
    "top20": "年度 Top20",
    "top_scorer": "射手王",
    "assist_leader": "助攻王",
    "mvp": "MVP",
}

# 赛制说明（与引擎实现一一对应，不写引擎没有的规则）。
_STRUCTURE_TEXT: Dict[str, str] = {
    base.COMPETITION_PREMIER: "20 支球队主客场双循环，共 38 轮 380 场；排名链：积分 → 净胜球 → 进球 → 相互战绩。",
    base.COMPETITION_SECOND: "20 支球队主客场双循环，共 38 轮 380 场；排名链：积分 → 净胜球 → 进球 → 相互战绩。",
    base.COMPETITION_WINNERS_CUP: "16 队分 4 组进行 6 轮小组赛；四分之一决赛、半决赛、决赛均为主客场两回合淘汰。",
    base.COMPETITION_CHALLENGE_CUP: "32 队单场淘汰：三十二强、十六强、四分之一决赛、半决赛、决赛共五轮。",
    base.COMPETITION_SUPER_CUP: "4 队淘汰制，半决赛与决赛各赛一场。",
    base.COMPETITION_PLAYOFF: "次级联赛第 3–6 名参加；半决赛与决赛均为主客场两回合，决赛胜者升入一级联赛。",
}

_WINNERS_KNOCKOUT_LABELS: Dict[int, str] = {
    7: "四分之一决赛 首回合",
    8: "四分之一决赛 次回合",
    9: "半决赛 首回合",
    10: "半决赛 次回合",
    11: "决赛 首回合",
    12: "决赛 次回合",
}
_CHALLENGE_ROUND_LABELS: Dict[int, str] = {
    1: "三十二强",
    2: "十六强",
    3: "四分之一决赛",
    4: "半决赛",
    5: "决赛",
}
_SUPER_CUP_ROUND_LABELS: Dict[int, str] = {1: "半决赛", 2: "决赛"}
_PLAYOFF_ROUND_LABELS: Dict[int, str] = {
    1: "半决赛 首回合",
    2: "半决赛 次回合",
    3: "决赛 首回合",
    4: "决赛 次回合",
}


def _round_label(competition: str, round_number: int) -> str:
    """轮次显示名：与 competition_queries 的赛事/轮次事件表一一对应。"""

    if competition == base.COMPETITION_WINNERS_CUP:
        if 1 <= round_number <= 6:
            return f"小组赛 第 {round_number} 轮"
        return _WINNERS_KNOCKOUT_LABELS.get(round_number, f"第 {round_number} 轮")
    if competition == base.COMPETITION_CHALLENGE_CUP:
        return _CHALLENGE_ROUND_LABELS.get(round_number, f"第 {round_number} 轮")
    if competition == base.COMPETITION_SUPER_CUP:
        return _SUPER_CUP_ROUND_LABELS.get(round_number, f"第 {round_number} 轮")
    if competition == base.COMPETITION_PLAYOFF:
        return _PLAYOFF_ROUND_LABELS.get(round_number, f"第 {round_number} 轮")
    return f"第 {round_number} 轮"


def _champion_caption(competition: str) -> str:
    """冠军称谓：升级附加赛的"冠军"即升级成功方。"""

    return "升级成功方" if competition == base.COMPETITION_PLAYOFF else "冠军"


class _SignedInt(int):
    """净胜球：``int`` 子类（EntityTable 仍按数值排序），显示带正负号。"""

    def __new__(cls, value: int) -> "_SignedInt":
        return super().__new__(cls, value)

    def __str__(self) -> str:
        return f"{int(self):+d}"


class _FormattedNumber(float):
    """带显示格式的数值：参与数值排序，显示时使用格式化文本。"""

    def __new__(cls, value: float, template: str = "{:.2f}") -> "_FormattedNumber":
        instance = super().__new__(cls, value)
        instance._template = template  # type: ignore[attr-defined]
        return instance

    def __str__(self) -> str:  # noqa: D105 - Qt DisplayRole 走 str()
        return self._template.format(float(self))  # type: ignore[attr-defined]


# -- 页签行 DTO -----------------------------------------------------------


@dataclass(frozen=True)
class _StandingRow:
    """积分榜行视图模型（净胜球带符号显示）。"""

    rank: Optional[int]
    team_id: int
    team_name: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    goal_diff: int
    points: int


@dataclass(frozen=True)
class _StageRow:
    """签表/两回合比赛行视图模型。"""

    round_number: int
    round_text: str
    match_id: str
    home_team_id: int
    home_name: str
    score_text: str
    away_team_id: int
    away_name: str
    status_text: str
    advancing_name: Optional[str] = None
    advancing_team_id: Optional[int] = None


@dataclass(frozen=True)
class _MatchRow:
    """赛程与结果行视图模型；未赛比赛显示"未赛"，不虚构比分。"""

    match_id: str
    week_text: str
    round_text: str
    home_team_id: int
    home_name: str
    score_text: str
    away_team_id: int
    away_name: str
    status_text: str


@dataclass(frozen=True)
class _LeaderRow:
    """球员榜行视图模型（三榜合一，board 区分类型）。"""

    board: str
    rank: int
    player_id: str
    player_name: str
    team_name: Optional[str]
    appeared: int
    stat_text: str
    stat_value: int
    rating: Optional[float]  # 默认球员无评分 -> None（显示“—”）


@dataclass(frozen=True)
class _AwardRow:
    """奖项行视图模型。"""

    award_type_text: str
    rank: Optional[int]
    player_id: str
    player_name: str
    team_name: Optional[str]
    score: Optional[float]


@dataclass(frozen=True)
class _HistoryRow:
    """历史页签行视图模型（一行一赛季）。"""

    season_number: int
    season_text: str
    champion_name: Optional[str]
    champion_route: Optional[Route]
    champion_player_id: Optional[str]
    champion_player_name: Optional[str]


# -- 列定义 ---------------------------------------------------------------

_STANDINGS_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("rank", "名次", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("team_name", "球队", width=200, stretch=True),
    ColumnSpec("played", "赛", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("wins", "胜", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("draws", "平", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("losses", "负", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goals_for", "进", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goals_against", "失", width=56, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goal_diff", "净", width=68, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("points", "积分", width=72, alignment=Qt.AlignmentFlag.AlignRight),
)

_STAGE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("round_text", "轮次", width=160),
    ColumnSpec("home_name", "主队", width=190),
    ColumnSpec("score_text", "比分", width=92, alignment=Qt.AlignmentFlag.AlignCenter),
    ColumnSpec("away_name", "客队", width=190),
    ColumnSpec("advancing_name", "晋级方", width=190),
)

_PLAYOFF_STAGE_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("round_text", "轮次", width=160),
    ColumnSpec("home_name", "主队", width=210),
    ColumnSpec("score_text", "比分", width=96, alignment=Qt.AlignmentFlag.AlignCenter),
    ColumnSpec("away_name", "客队", width=210),
    ColumnSpec("status_text", "状态", width=84, alignment=Qt.AlignmentFlag.AlignCenter),
)

_MATCH_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("week_text", "周", width=96),
    ColumnSpec("round_text", "轮次", width=96, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("home_name", "主队", width=190, stretch=True),
    ColumnSpec("score_text", "比分/未赛", width=104, alignment=Qt.AlignmentFlag.AlignCenter),
    ColumnSpec("away_name", "客队", width=190, stretch=True),
    ColumnSpec("status_text", "状态", width=84, alignment=Qt.AlignmentFlag.AlignCenter),
)

_LEADER_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("board", "类型", width=88, alignment=Qt.AlignmentFlag.AlignCenter),
    ColumnSpec("rank", "名次", width=60, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("player_name", "球员", width=190, stretch=True),
    ColumnSpec("team_name", "球队", width=180),
    ColumnSpec("appeared", "出场", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("stat_text", "主要统计", width=140, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("rating", "评分（推导）", width=112, alignment=Qt.AlignmentFlag.AlignRight),
)

_AWARD_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("award_type_text", "类型", width=120),
    ColumnSpec("rank", "名次", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("player_name", "球员", width=190),
    ColumnSpec("team_name", "球队", width=190),
    ColumnSpec("score", "分数", width=100, alignment=Qt.AlignmentFlag.AlignRight),
)

_HISTORY_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("season_text", "赛季", width=110),
    ColumnSpec("champion_name", "冠军", width=210),
    ColumnSpec("champion_player_name", "冠军球员（MVP）", width=210),
)


def _column_index(columns: Sequence[ColumnSpec], key: str) -> int:
    for index, column in enumerate(columns):
        if column.key == key:
            return index
    raise ValueError(f"未知列：{key}")


# -- 列级链接 delegate（与 team_profile_page 同一套视觉与交互合同） ------------


class _LinkColumnDelegate(QStyledItemDelegate):
    """把一列文本渲染为实体链接：青色、hover 下划线、单击导航（§7.2）。

    行的其余区域仍走 EntityTable 默认行为（双击/Enter 打开行主路由）。

    生命周期约定：``setItemDelegateForColumn`` 不取得所有权，本 delegate
    必须挂 view 为 Qt parent（与视图同生命周期），并由页面持 Python 引用、
    引用列表只增不清（shiboken 在最后一个引用消失时会删除 C++ 对象，旧视图
    列映射仍指向它 → 任意 GC/绘制时刻 SIGSEGV）。
    """

    def __init__(
        self,
        table: EntityTable,
        resolver: Callable[[Any], Optional[Route]],
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
        if not text:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = opt.font
        font.setUnderline(bool(opt.state & QStyle.State.State_MouseOver))
        painter.setFont(font)
        painter.setPen(QColor("#7dd3fc"))
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


# -- 页面 -------------------------------------------------------------------


class CompetitionPage(EntityPageBase):
    """赛事详情页：``Route("competition", competition=<赛事>, season=<n>)``。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._competition_id: str = ""
        self._competition_name: str = ""
        self._season: Optional[int] = None
        self._page_header: Optional[PageHeader] = None
        self._stage_table: Optional[EntityTable] = None
        self._stage_rows: List[Any] = []
        self._champion_link: Optional[EntityLink] = None
        # 悬停可点列 → 手型光标（§7.2 指针变化）：view id -> 可点列集合。
        self._pointer_tables: Dict[int, set] = {}
        # delegate 生命周期约定（崩溃矩阵实证，勿改）：
        # setItemDelegateForColumn 不取得所有权 → delegate 必须挂 view 为 Qt
        # parent（与视图同生命周期），同时页面持有 Python 引用，且引用列表
        # **只增不清**——shiboken 在最后一个 Python 引用消失时就会删除 C++
        # 对象（即使挂了 parent），而旧视图的列映射仍指向该对象，此后任意
        # GC/绘制时刻都会 SIGSEGV。持久页签 delegate 存 _delegates；签表页签
        # 随刷新重建的 delegate 追加进 _stage_delegates，从不重置。
        self._delegates: List[_LinkColumnDelegate] = []
        self._stage_delegates: List[_LinkColumnDelegate] = []
        super().__init__(context, parent)

    # -- UI 骨架 -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, 1)

        self._content = QWidget(self)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        # 页头动作区控件（跨刷新复用；PageHeader 在刷新时重建）。
        self._competition_caption = QLabel("赛事")
        self._competition_caption.setStyleSheet(_MUTED_STYLE)
        self._competition_combo = QComboBox(self)
        self._competition_combo.setObjectName("competitionSwitchCombo")
        self._competition_combo.addItems(list(base.ALL_COMPETITIONS))
        self._competition_combo.currentIndexChanged.connect(self._on_competition_selected)
        self._season_caption = QLabel("赛季")
        self._season_caption.setStyleSheet(_MUTED_STYLE)
        self._season_combo = QComboBox(self)
        self._season_combo.setObjectName("competitionSeasonSelector")
        self._season_combo.currentIndexChanged.connect(self._on_season_selected)

        # 状态摘要行：状态徽标 + 完成进度 + 冠军（可点球队）。
        summary_row = QWidget(self._content)
        summary_layout = QHBoxLayout(summary_row)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(12)
        self._status_badge = QLabel("", summary_row)
        self._status_badge.setObjectName("competitionStatusBadge")
        self._progress_label = QLabel("", summary_row)
        self._progress_label.setStyleSheet(f"color: #e8eef7; font-size: 14px; background: transparent;")
        self._champion_holder = QWidget(summary_row)
        self._champion_layout = QHBoxLayout(self._champion_holder)
        self._champion_layout.setContentsMargins(0, 0, 0, 0)
        self._champion_layout.setSpacing(6)
        summary_layout.addWidget(self._status_badge)
        summary_layout.addWidget(self._progress_label)
        summary_layout.addWidget(self._champion_holder, 1)
        content_layout.addWidget(summary_row)

        # 页签。
        self._tabs = QTabWidget(self._content)

        self._overview_scroll = QScrollArea(self)
        self._overview_scroll.setObjectName("competitionOverviewScroll")
        self._overview_scroll.setWidgetResizable(True)
        self._overview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tabs.addTab(self._overview_scroll, _TAB_TITLES[_TAB_OVERVIEW])

        # 积分榜 / 签表：按赛事类型在刷新时重建，任何时刻只有一个表格实例。
        self._stage_container = QWidget(self)
        self._stage_container_layout = QVBoxLayout(self._stage_container)
        self._stage_container_layout.setContentsMargins(0, 0, 0, 0)
        self._stage_container_layout.setSpacing(8)
        self._stage_stack = QStackedWidget(self)
        self._stage_stack.addWidget(self._stage_container)
        self._tabs.addTab(self._stage_stack, _TAB_TITLES[_TAB_STAGE])

        self._matches_table = EntityTable(_MATCH_COLUMNS, navigator=self._context.navigate, parent=self)
        self._matches_empty_slot = self._make_empty_slot()
        self._matches_stack = self._make_tab_stack(self._matches_table, self._matches_empty_slot)
        self._tabs.addTab(self._matches_stack, _TAB_TITLES[_TAB_MATCHES])

        self._leader_tabs = QTabWidget(self)
        self._leader_scorers_table = EntityTable(_LEADER_COLUMNS, navigator=self._context.navigate, parent=self._leader_tabs)
        self._leader_assisters_table = EntityTable(_LEADER_COLUMNS, navigator=self._context.navigate, parent=self._leader_tabs)
        self._leader_rated_table = EntityTable(_LEADER_COLUMNS, navigator=self._context.navigate, parent=self._leader_tabs)
        self._leader_tabs.addTab(self._leader_scorers_table, "射手榜")
        self._leader_tabs.addTab(self._leader_assisters_table, "助攻榜")
        self._leader_tabs.addTab(self._leader_rated_table, "评分榜")
        self._leader_empty_slot = self._make_empty_slot()
        self._leader_stack = self._make_tab_stack(self._leader_tabs, self._leader_empty_slot)
        # “只显示真实球员”复选框（切换 → refresh → 重新拉取榜单，过滤发生在
        # 截取前 N 名之前，排名不失真）。复选框行不是滚动面（§8.2 不受影响）。
        leader_panel = QWidget(self)
        leader_layout = QVBoxLayout(leader_panel)
        leader_layout.setContentsMargins(0, 0, 0, 0)
        leader_layout.setSpacing(6)
        leader_layout.addWidget(self._make_leader_real_check(), 0)
        leader_layout.addWidget(self._leader_stack, 1)
        self._tabs.addTab(leader_panel, _TAB_TITLES[_TAB_LEADERS])

        self._awards_table = EntityTable(_AWARD_COLUMNS, navigator=self._context.navigate, parent=self)
        self._awards_empty_slot = self._make_empty_slot()
        self._awards_stack = self._make_tab_stack(self._awards_table, self._awards_empty_slot)
        self._tabs.addTab(self._awards_stack, _TAB_TITLES[_TAB_AWARDS])

        self._history_table = EntityTable(_HISTORY_COLUMNS, navigator=self._context.navigate, parent=self)
        self._history_empty_slot = self._make_empty_slot()
        self._history_stack = self._make_tab_stack(self._history_table, self._history_empty_slot)
        self._tabs.addTab(self._history_stack, _TAB_TITLES[_TAB_HISTORY])

        content_layout.addWidget(self._tabs, 1)
        self._stack.addWidget(self._content)

        self._empty = EmptyState("未知赛事", "")
        self._stack.addWidget(self._empty)

        self._install_table_delegates(
            self._matches_table, _MATCH_COLUMNS, self._match_team_routes()
        )
        for leader_table in (
            self._leader_scorers_table,
            self._leader_assisters_table,
            self._leader_rated_table,
        ):
            self._install_table_delegates(
                leader_table, _LEADER_COLUMNS, (("player_name", self._leader_player_route),)
            )
        self._install_table_delegates(
            self._awards_table, _AWARD_COLUMNS, (("player_name", self._award_player_route),)
        )
        self._install_table_delegates(
            self._history_table,
            _HISTORY_COLUMNS,
            (
                ("champion_name", self._history_champion_route),
                ("champion_player_name", self._history_player_route),
            ),
        )

        self._tabs.currentChanged.connect(self._on_tab_changed)

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

    def _install_table_delegates(
        self,
        table: EntityTable,
        columns: Tuple[ColumnSpec, ...],
        resolvers: Sequence[Tuple[str, Callable[[Any], Optional[Route]]]],
        crest_keys: Optional[Sequence[str]] = None,
    ) -> None:
        """给表格的可点列挂链接 delegate 并注册手型光标列。"""

        registered = set()
        crest_keys = crest_keys or ()
        for key, resolver in resolvers:
            index = _column_index(columns, key)
            # 生命周期约定：挂 view 为 parent + 页面持引用、列表只增不清（见 __init__ 注释）。
            delegate = _LinkColumnDelegate(
                table, resolver, columns[index].alignment, parent=table.view, crest=key in crest_keys
            )
            table.view.setItemDelegateForColumn(index, delegate)
            self._delegates.append(delegate)
            registered.add(index)
        self._pointer_tables[id(table.view)] = registered
        table.view.viewport().installEventFilter(self)

    # -- 契约入口 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "competition":
            return
        competition_id = str(route.params.get("competition", ""))
        season = route.int_param("season")
        self._competition_id = competition_id
        self._season = season
        if season is None:
            self._set_page_empty("路由参数不完整", "赛事路由缺少赛季参数。")
            return
        try:
            with base.open_read_connection(self.save_name()) as conn:
                seasons = base.load_seasons(conn)
                if int(season) not in {ref.season_number for ref in seasons}:
                    self._set_page_empty("赛季不存在", f"存档中不存在第 {season} 赛季。")
                    return
                overview_map = {
                    item.competition.competition_id: item
                    for item in competition_queries.list_competitions(conn, int(season))
                }
                try:
                    profile = competition_queries.get_competition_profile(
                        conn, competition_id, int(season), leaderboards_is_real=self._leader_real_only
                    )
                except KeyError:
                    self._set_page_empty("未知赛事", f"“{competition_id}”不是本存档的六项规范赛事之一。")
                    return
                overview = overview_map.get(competition_id)
                if overview is None:
                    self._set_page_empty("未知赛事", f"“{competition_id}”不是本存档的六项规范赛事之一。")
                    return
                history = history_queries.get_competition_history(conn, competition_id)
                refs_by_name = {ref.display_name: ref for ref in base.load_team_refs(conn)}
                team_by_player = {
                    row.player_id: row.team.display_name
                    for row in player_queries.list_players(conn, int(season), competition=competition_id)
                }
        except base.MissingSaveError as exc:
            self._set_page_empty("存档不可用", str(exc))
            return
        except sqlite3.Error as exc:
            self._set_page_empty("存档读取失败", str(exc))
            return
        self._populate(overview, profile, history, seasons, refs_by_name, team_by_player, int(season))

    def route_context(self) -> dict:
        if not self._competition_name:
            return {}
        context: dict = {"competition_name": self._competition_name}
        if self._season is not None:
            context["season"] = int(self._season)
        return context

    # -- 数据装配 -------------------------------------------------------------

    def _populate(
        self,
        overview: competition_queries.CompetitionOverview,
        profile: competition_queries.CompetitionProfile,
        history: Sequence[history_queries.CompetitionSeasonLine],
        seasons: Sequence[base.SeasonRef],
        refs_by_name: Dict[str, base.TeamRef],
        team_by_player: Dict[str, str],
        season: int,
    ) -> None:
        self._competition_name = profile.competition.display_name
        self._season = season
        route = self.current_route()
        self._rebuild_header(self._competition_name, route)
        self._rebuild_combos(seasons, profile.competition.competition_id, season)
        self._update_summary_row(overview, refs_by_name, season)
        self._rebuild_stage_tab(profile, overview, refs_by_name, season)
        self._rebuild_matches_tab(profile, overview)
        self._rebuild_leader_tab(profile, team_by_player, season)
        self._rebuild_awards_tab(profile, season)
        self._rebuild_history_tab(history, refs_by_name)
        self._replace_scroll_content(
            self._overview_scroll,
            self._build_overview_content(overview, profile, season, refs_by_name),
        )

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

    # -- 页头与页头动作区 -------------------------------------------------------

    def _rebuild_header(self, title: str, route: Optional[Route]) -> None:
        """重建页头（PageHeader 标题在构造时固定）；两个下拉跨刷新复用。"""

        old_header = self._page_header
        breadcrumbs: list = []
        # 复用控件直接交给新页头（加入布局时会 reparent），旧页头随后删除；
        # 全程不 setParent(None)，避免 macOS 全屏下出现临时顶层窗口。
        self._page_header = PageHeader(
            title,
            breadcrumbs,
            self._context.navigate,
            actions=[
                self._competition_caption,
                self._competition_combo,
                self._season_caption,
                self._season_combo,
            ],
        )
        self._content.layout().insertWidget(0, self._page_header)
        if old_header is not None:
            old_header.deleteLater()

    def _rebuild_combos(self, seasons: Sequence[base.SeasonRef], competition_id: str, season: int) -> None:
        """重建赛季选择器并定位当前路由；切换由对应槽 navigate。"""

        self._competition_combo.blockSignals(True)
        try:
            index = self._competition_combo.findText(competition_id)
            self._competition_combo.setCurrentIndex(max(0, index))
        finally:
            self._competition_combo.blockSignals(False)

        self._season_combo.blockSignals(True)
        try:
            self._season_combo.clear()
            for ref in seasons:
                label = f"第 {ref.season_number} 赛季"
                if not ref.is_completed:
                    label += "（进行中）"
                self._season_combo.addItem(label, ref.season_number)
            index = self._season_combo.findData(int(season))
            self._season_combo.setCurrentIndex(max(0, index))
        finally:
            self._season_combo.blockSignals(False)

    def _on_competition_selected(self, index: int) -> None:
        route = self.current_route()
        if route is None:
            return
        competition = self._competition_combo.itemText(index)
        season = route.int_param("season")
        if not competition or season is None or competition == route.params.get("competition"):
            return
        self.navigate(Route("competition", competition=competition, season=season))

    def _on_season_selected(self, index: int) -> None:
        route = self.current_route()
        data = self._season_combo.itemData(index)
        if route is None or data is None:
            return
        season = int(data)
        competition = str(route.params.get("competition", ""))
        if not competition or season == route.int_param("season"):
            return
        self.navigate(Route("competition", competition=competition, season=season))

    def _on_tab_changed(self, index: int) -> None:
        self.save_state({"tab": int(index)})

    # -- 状态摘要行 -------------------------------------------------------------

    def _update_summary_row(
        self,
        overview: competition_queries.CompetitionOverview,
        refs_by_name: Dict[str, base.TeamRef],
        season: int,
    ) -> None:
        colors = _STATUS_BADGE_COLORS.get(overview.status, ("#94a3b8", "#0f172a"))
        self._status_badge.setText(overview.status)
        self._status_badge.setStyleSheet(
            f"background: {colors[0]}; color: {colors[1]}; border-radius: 9px;"
            "padding: 4px 14px; font-weight: 800;"
        )
        if overview.total_matches is not None:
            self._progress_label.setText(
                f"已完成 {overview.completed_matches} / 共 {overview.total_matches} 场"
            )
        else:
            self._progress_label.setText(f"已完成 {overview.completed_matches} 场")

        self._champion_link = None
        self._clear_layout(self._champion_layout)
        if overview.status == competition_queries.STATUS_NOT_HELD:
            self._champion_layout.addWidget(self._muted_label("本届未举办"))
        else:
            self._champion_layout.addWidget(self._muted_label(f"{_champion_caption(self._competition_id)}："))
            if overview.champion:
                ref = refs_by_name.get(overview.champion)
                if ref is not None:
                    link = EntityLink(
                        overview.champion,
                        Route("team", team=ref.team_id, season=season),
                        self._context.navigate,
                        self._champion_holder,
                    )
                    self._champion_layout.addWidget(link)
                    self._champion_link = link
                else:
                    self._champion_layout.addWidget(self._bright_label(overview.champion))
            else:
                self._champion_layout.addWidget(self._muted_label("尚未决出"))
        self._champion_layout.addStretch(1)

    # -- 积分榜 / 签表页签（按赛事类型重建，唯一表格实例） -------------------------

    def _rebuild_stage_tab(
        self,
        profile: competition_queries.CompetitionProfile,
        overview: competition_queries.CompetitionOverview,
        refs_by_name: Dict[str, base.TeamRef],
        season: int,
    ) -> None:
        self._clear_layout(self._stage_container_layout)
        self._stage_table = None
        self._stage_rows = []
        # 注意：self._stage_delegates 只增不清（delegate 生命周期约定，见
        # __init__ 注释）——旧视图析构时其列映射仍指向旧 delegate，提前清空
        # 引用会让 shiboken 删除 C++ 对象，之后任意 GC/绘制时刻 SIGSEGV。

        competition = profile.competition.competition_id
        if profile.standings is not None:
            rows = [
                _StandingRow(
                    rank=item.rank,
                    team_id=item.team_id,
                    team_name=item.team_name,
                    played=item.played,
                    wins=item.wins,
                    draws=item.draws,
                    losses=item.losses,
                    goals_for=item.goals_for,
                    goals_against=item.goals_against,
                    goal_diff=_SignedInt(item.goals_for - item.goals_against),
                    points=item.points,
                )
                for item in profile.standings
            ]
            self._stage_rows = list(rows)
            table = EntityTable(_STANDINGS_COLUMNS, navigator=self._context.navigate, parent=self._stage_container)
            table.set_rows(rows, route_for_row=self._standing_team_route)
            self._install_stage_delegates(
                table, _STANDINGS_COLUMNS, (("team_name", self._standing_team_route),), crest_keys=("team_name",)
            )
            self._stage_container_layout.addWidget(table)
            self._stage_table = table
            return

        if competition in (base.COMPETITION_WINNERS_CUP, base.COMPETITION_CHALLENGE_CUP, base.COMPETITION_SUPER_CUP):
            rows = [self._cup_stage_row(item, competition) for item in profile.stage_rows]
            self._stage_rows = list(rows)
            if not rows:
                self._stage_container_layout.addWidget(self._stage_empty_state(overview.status))
                return
            # 杯赛页签：小组积分表 + 淘汰树放在唯一 QScrollArea 内（“赛程与结果”
            # 页签仍提供全部比赛的可点表格），避免独立表格挤占窗口高度导致内容被裁剪。
            scroll = QScrollArea(self._stage_container)
            scroll.setObjectName("cupStageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self._build_cup_groups_knockout(profile))
            self._stage_container_layout.addWidget(scroll, 1)
            self._stage_table = None
            return

        if competition == base.COMPETITION_PLAYOFF:
            rows = [self._playoff_stage_row(item) for item in profile.matches]
            self._stage_rows = list(rows)
            bar = self._build_playoff_champion_bar(profile, refs_by_name, season)
            self._stage_container_layout.addWidget(bar)
            if rows:
                table = EntityTable(
                    _PLAYOFF_STAGE_COLUMNS, navigator=self._context.navigate, parent=self._stage_container
                )
                table.set_rows(rows, route_for_row=self._stage_match_route)
                self._install_stage_delegates(
                    table,
                    _PLAYOFF_STAGE_COLUMNS,
                    (
                        ("home_name", self._stage_home_route),
                        ("away_name", self._stage_away_route),
                    ),
                )
                self._stage_container_layout.addWidget(table, 1)
                self._stage_table = table
            else:
                self._stage_container_layout.addWidget(
                    EmptyState("暂无附加赛比赛", "该赛季的升级附加赛还没有已排期的比赛。")
                )
            return

        self._stage_container_layout.addWidget(self._stage_empty_state(overview.status))

    def _build_cup_groups_knockout(
        self, profile: competition_queries.CompetitionProfile
    ) -> QWidget:
        """杯赛头部信息：小组积分表 + 淘汰树。列表页签仍是下方整表。"""
        holder = QWidget(self._stage_container)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        for group in profile.cup_groups:
            group_title = group.group_name if group.group_name.endswith("组") else f"{group.group_name} 组"
            layout.addWidget(section_header(group_title, "小组积分表（按积分、净胜球、进球排序）"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            headers = ("名次", "球队", "赛", "胜", "平", "负", "进", "失", "积分")
            for column, header in enumerate(headers):
                label = QLabel(header)
                label.setStyleSheet(_GRID_HEADER_STYLE)
                grid.addWidget(label, 0, column)
            for row_index, row in enumerate(group.rows, start=1):
                cells = (
                    str(row.rank), row.team_name, str(row.played), str(row.wins),
                    str(row.draws), str(row.losses), str(row.goals_for),
                    str(row.goals_against), str(row.points),
                )
                for column, text in enumerate(cells):
                    label = QLabel(text)
                    label.setStyleSheet(_GRID_CELL_STYLE)
                    grid.addWidget(label, row_index, column)
            grid.setColumnStretch(1, 1)
            layout.addLayout(grid)

        for round_block in profile.knockout_rounds:
            layout.addWidget(section_header(round_block.stage, "淘汰赛对局（→ 晋级方）"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            headers = ("主队", "比分", "客队", "晋级方")
            for column, header in enumerate(headers):
                label = QLabel(header)
                label.setStyleSheet(_GRID_HEADER_STYLE)
                grid.addWidget(label, 0, column)
            for row_index, pair in enumerate(round_block.pairs, start=1):
                score = "—"
                if pair.home_goals is not None and pair.away_goals is not None:
                    score = f"{pair.home_goals} - {pair.away_goals}"
                cells = (pair.home, score, pair.away, pair.advancing or "待定")
                for column, text in enumerate(cells):
                    label = QLabel(text)
                    label.setStyleSheet(_GRID_CELL_STYLE)
                    grid.addWidget(label, row_index, column)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(2, 1)
            layout.addLayout(grid)

        return holder

    def _cup_stage_row(self, item: competition_queries.CupStageRow, competition: str) -> _StageRow:
        match = item.match
        completed = match.is_completed and match.home_goals is not None and match.away_goals is not None
        advancing = item.advancing
        return _StageRow(
            round_number=item.round_number,
            round_text=_round_label(competition, item.round_number),
            match_id=match.match_id,
            home_team_id=match.home.team_id,
            home_name=match.home.display_name,
            score_text=f"{match.home_goals}-{match.away_goals}" if completed else "未赛",
            away_team_id=match.away.team_id,
            away_name=match.away.display_name,
            status_text="已赛" if completed else "未赛",
            advancing_name=advancing.display_name if advancing is not None else None,
            advancing_team_id=advancing.team_id if advancing is not None else None,
        )

    def _playoff_stage_row(self, match: base.MatchRef) -> _StageRow:
        completed = match.is_completed and match.home_goals is not None and match.away_goals is not None
        return _StageRow(
            round_number=match.round_number,
            round_text=_round_label(base.COMPETITION_PLAYOFF, match.round_number),
            match_id=match.match_id,
            home_team_id=match.home.team_id,
            home_name=match.home.display_name,
            score_text=f"{match.home_goals}-{match.away_goals}" if completed else "未赛",
            away_team_id=match.away.team_id,
            away_name=match.away.display_name,
            status_text="已赛" if completed else "未赛",
        )

    def _build_playoff_champion_bar(
        self,
        profile: competition_queries.CompetitionProfile,
        refs_by_name: Dict[str, base.TeamRef],
        season: int,
    ) -> QWidget:
        bar = QFrame()
        bar.setObjectName("cardFrame")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        caption = QLabel(f"{_champion_caption(base.COMPETITION_PLAYOFF)}：")
        caption.setStyleSheet("font-weight: 800; background: transparent; color: #f8fbff;")
        layout.addWidget(caption)
        if profile.champion:
            ref = refs_by_name.get(profile.champion)
            if ref is not None:
                layout.addWidget(
                    EntityLink(
                        profile.champion,
                        Route("team", team=ref.team_id, season=season),
                        self._context.navigate,
                        bar,
                    )
                )
            else:
                layout.addWidget(self._bright_label(profile.champion))
            note = QLabel("升级附加赛冠军即升级成功方，升入一级联赛。")
            note.setStyleSheet(f"font-size: 12px; {_MUTED_STYLE}")
            layout.addWidget(note)
        else:
            layout.addWidget(self._muted_label("尚未决出"))
        layout.addStretch(1)
        return bar

    def _stage_empty_state(self, overview_status: Optional[str]) -> EmptyState:
        if overview_status == competition_queries.STATUS_NOT_HELD:
            return EmptyState(
                "本届未举办",
                "该赛事本赛季未举办，没有签表与比赛数据。",
                "可用右上角赛季选择器查看举办过的赛季，或切换其他赛事。",
            )
        return EmptyState("暂无签表数据", "该赛事本赛季还没有已排期的比赛。")

    def _install_stage_delegates(
        self,
        table: EntityTable,
        columns: Tuple[ColumnSpec, ...],
        resolvers: Sequence[Tuple[str, Callable[[Any], Optional[Route]]]],
        crest_keys: Optional[Sequence[str]] = None,
    ) -> None:
        """给刷新时新建的签表/积分榜表格挂链接 delegate。"""

        registered: set = set()
        crest_keys = crest_keys or ()
        for key, resolver in resolvers:
            index = _column_index(columns, key)
            # 生命周期约定：挂 view 为 parent + 页面持引用、列表只增不清（见 __init__ 注释）。
            delegate = _LinkColumnDelegate(
                table, resolver, columns[index].alignment, parent=table.view, crest=key in crest_keys
            )
            table.view.setItemDelegateForColumn(index, delegate)
            self._stage_delegates.append(delegate)
            registered.add(index)
        self._pointer_tables[id(table.view)] = registered
        table.view.viewport().installEventFilter(self)

    # -- 赛程与结果页签 ---------------------------------------------------------

    def _rebuild_matches_tab(
        self,
        profile: competition_queries.CompetitionProfile,
        overview: competition_queries.CompetitionOverview,
    ) -> None:
        rows = []
        for match in profile.matches:
            completed = match.is_completed and match.home_goals is not None and match.away_goals is not None
            rows.append(
                _MatchRow(
                    match_id=match.match_id,
                    week_text=f"第 {match.week_number} 周",
                    round_text=f"第 {match.round_number} 轮",
                    home_team_id=match.home.team_id,
                    home_name=match.home.display_name,
                    score_text=f"{match.home_goals}-{match.away_goals}" if completed else "未赛",
                    away_team_id=match.away.team_id,
                    away_name=match.away.display_name,
                    status_text="已赛" if completed else "未赛",
                )
            )
        if rows:
            self._matches_table.set_rows(rows, route_for_row=self._match_route_for_row)
            self._matches_stack.setCurrentWidget(self._matches_table)
        else:
            self._show_tab_empty(
                self._matches_stack,
                self._matches_empty_slot,
                self._stage_empty_state(overview.status),
            )

    # -- 球员榜页签 -------------------------------------------------------------

    @property
    def _leader_real_only(self) -> Optional[bool]:
        # None = 不过滤（全部球员）；True = 只显示真实球员。绝不能传 False，
        # 否则查询层会把榜单过滤成“只显示默认球员”。
        check = getattr(self, "_leader_real_only_check", None)
        if check is None or not check.isChecked():
            return None
        return True

    def _make_leader_real_check(self) -> QCheckBox:
        """球员榜“只显示真实球员”复选框：跨刷新保持，切换即重新拉取。"""
        check = getattr(self, "_leader_real_only_check", None)
        if check is None:
            check = QCheckBox("只显示真实球员")
            check.setObjectName("leaderRealOnlyCheck")
            check.setChecked(True)  # 默认只显示真实球员
            check.toggled.connect(lambda _checked: self.refresh())
            self._leader_real_only_check = check
        return check

    def _rebuild_leader_tab(
        self,
        profile: competition_queries.CompetitionProfile,
        team_by_player: Dict[str, str],
        season: int,
    ) -> None:
        boards: Tuple[Tuple[str, Any, Callable[[competition_queries.LeaderboardEntry], str], Callable[[competition_queries.LeaderboardEntry], int]], ...] = (
            ("射手榜", profile.leaderboards.top_scorers, lambda e: f"进球 {e.goals}", lambda e: e.goals),
            ("助攻榜", profile.leaderboards.top_assisters, lambda e: f"助攻 {e.assists}", lambda e: e.assists),
            (
                "评分榜",
                profile.leaderboards.top_rated,
                lambda e: f"进球+助攻 {e.goals + e.assists}",
                lambda e: e.goals + e.assists,
            ),
        )
        tables = (
            self._leader_scorers_table,
            self._leader_assisters_table,
            self._leader_rated_table,
        )
        total_rows = 0
        for (board_name, entries, stat_text, stat_value), table in zip(boards, tables):
            rows: List[_LeaderRow] = []
            for index, entry in enumerate(entries, start=1):
                rows.append(
                    _LeaderRow(
                        board=board_name,
                        rank=index,
                        player_id=entry.player.player_id,
                        player_name=entry.player.display_name,
                        team_name=team_by_player.get(entry.player.player_id),
                        appeared=entry.matches_played,
                        stat_text=stat_text(entry),
                        stat_value=stat_value(entry),
                        rating=None if entry.rating is None else _FormattedNumber(entry.rating),
                    )
                )
            table.set_rows(rows, route_for_row=self._leader_player_route)
            total_rows += len(rows)
        if total_rows:
            self._leader_stack.setCurrentWidget(self._leader_tabs)
        else:
            self._show_tab_empty(
                self._leader_stack,
                self._leader_empty_slot,
                EmptyState(
                    "暂无球员榜数据",
                    "该赛事本赛季还没有球员出场统计，榜单在比赛开赛后生成。",
                ),
            )

    # -- 奖项页签 ---------------------------------------------------------------

    def _rebuild_awards_tab(self, profile: competition_queries.CompetitionProfile, season: int) -> None:
        rows = [
            _AwardRow(
                award_type_text=_AWARD_TYPE_LABELS.get(line.award_type, line.award_type),
                rank=line.rank,
                player_id=line.player.player_id,
                player_name=line.player.display_name,
                team_name=line.team_name,
                score=None if line.score is None else _FormattedNumber(line.score),
            )
            for line in profile.awards
        ]
        if rows:
            self._awards_table.set_rows(rows, route_for_row=self._award_player_route)
            self._awards_stack.setCurrentWidget(self._awards_table)
        else:
            self._show_tab_empty(
                self._awards_stack,
                self._awards_empty_slot,
                EmptyState("本届暂无奖项记录", "该赛事本赛季还没有颁发个人奖项（奖项在赛季末结算）。"),
            )

    # -- 历史页签 ---------------------------------------------------------------

    def _rebuild_history_tab(
        self,
        history: Sequence[history_queries.CompetitionSeasonLine],
        refs_by_name: Dict[str, base.TeamRef],
    ) -> None:
        rows: List[_HistoryRow] = []
        for line in history:
            champion_ref = refs_by_name.get(line.champion) if line.champion else None
            rows.append(
                _HistoryRow(
                    season_number=line.season_number,
                    season_text=f"第 {line.season_number} 赛季",
                    champion_name=line.champion,
                    champion_route=(
                        Route("team", team=champion_ref.team_id, season=line.season_number)
                        if champion_ref is not None
                        else None
                    ),
                    champion_player_id=line.champion_player.player_id if line.champion_player else None,
                    champion_player_name=line.champion_player.display_name if line.champion_player else None,
                )
            )
        if rows:
            self._history_table.set_rows(rows, route_for_row=self._history_season_route)
            self._history_stack.setCurrentWidget(self._history_table)
        else:
            self._show_tab_empty(
                self._history_stack,
                self._history_empty_slot,
                EmptyState("暂无历史归档", "该赛事还没有已归档的赛季（归档在赛季结束后生成）。"),
            )

    # -- 概览页签 ---------------------------------------------------------------

    def _build_overview_content(
        self,
        overview: competition_queries.CompetitionOverview,
        profile: competition_queries.CompetitionProfile,
        season: int,
        refs_by_name: Dict[str, base.TeamRef],
    ) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 8, 20)
        layout.setSpacing(14)

        if overview.status == competition_queries.STATUS_NOT_HELD:
            layout.addWidget(
                EmptyState(
                    "本届未举办",
                    f"{self._competition_name}在本赛季未举办，没有签表、比赛与榜单数据。",
                    "可用右上角赛季选择器查看举办过的赛季，或切换其他赛事。",
                )
            )
            layout.addStretch(1)
            return container

        # 状态摘要卡。
        status_card = CardFrame("赛事状态", f"第 {season} 赛季 · {self._competition_name}")
        metrics = QWidget(status_card)
        grid = QGridLayout(metrics)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        colors = _STATUS_BADGE_COLORS.get(overview.status, ("#94a3b8", "#0f172a"))
        status_value = QLabel(overview.status)
        status_value.setStyleSheet(
            f"background: {colors[0]}; color: {colors[1]}; border-radius: 9px;"
            "padding: 3px 12px; font-weight: 800; font-size: 15px;"
        )
        total_text = "—" if overview.total_matches is None else str(overview.total_matches)
        champion_caption = _champion_caption(self._competition_id)
        champion_value: Union[str, QWidget]
        if overview.champion:
            ref = refs_by_name.get(overview.champion)
            if ref is not None:
                champion_value = EntityLink(
                    overview.champion,
                    Route("team", team=ref.team_id, season=season),
                    self._context.navigate,
                )
            else:
                champion_value = overview.champion
        else:
            champion_value = "尚未决出"
        cells = (
            ("状态", status_value),
            ("已完成场次", str(overview.completed_matches)),
            ("总场次", total_text),
            (champion_caption, champion_value),
        )
        for index, (label, value) in enumerate(cells):
            grid.addWidget(self._metric_cell(label, value), 0, index)
            grid.setColumnStretch(index, 1)
        status_card.body_layout.addWidget(metrics)
        layout.addWidget(status_card)

        # 赛制说明卡。
        structure_card = CardFrame("赛制说明", _STRUCTURE_TEXT.get(self._competition_id))
        layout.addWidget(structure_card)

        # 冠军与晋级卡。
        champion_card = CardFrame("冠军与晋级", None)
        champion_row = QHBoxLayout()
        champion_row.setContentsMargins(0, 0, 0, 0)
        champion_row.setSpacing(8)
        champion_row.addWidget(self._muted_label(f"{champion_caption}："))
        if overview.champion:
            ref = refs_by_name.get(overview.champion)
            if ref is not None:
                champion_row.addWidget(
                    EntityLink(
                        overview.champion,
                        Route("team", team=ref.team_id, season=season),
                        self._context.navigate,
                    )
                )
            else:
                champion_row.addWidget(self._bright_label(overview.champion))
        else:
            champion_row.addWidget(self._muted_label("本届冠军尚未决出。"))
        if self._competition_id == base.COMPETITION_PLAYOFF:
            note = QLabel("升级附加赛冠军即升级成功方，升入一级联赛。")
            note.setStyleSheet(f"font-size: 12px; {_MUTED_STYLE}")
            champion_row.addWidget(note)
        champion_row.addStretch(1)
        champion_holder = QWidget(champion_card)
        champion_holder.setLayout(champion_row)
        champion_card.body_layout.addWidget(champion_holder)
        layout.addWidget(champion_card)

        layout.addStretch(1)
        return container

    def _metric_cell(self, label: str, value: Union[str, QWidget]) -> QWidget:
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(4)
        caption = QLabel(label)
        caption.setStyleSheet(f"color: {TEXT_COLOR_MUTED}; font-size: 12px; background: transparent;")
        cell_layout.addWidget(caption)
        if isinstance(value, QWidget):
            cell_layout.addWidget(value)
            cell_layout.addStretch(1)
        else:
            value_label = QLabel(value)
            value_label.setStyleSheet("color: #f8fbff; font-size: 20px; font-weight: 800; background: transparent;")
            cell_layout.addWidget(value_label)
        return cell

    # -- 路由解析（行激活 + 列级链接） -------------------------------------------

    def _team_route(self, team_id: int) -> Optional[Route]:
        season = self._season
        if season is None:
            return None
        return Route("team", team=int(team_id), season=season)

    def _player_route(self, player_id: str, season: Optional[int] = None) -> Optional[Route]:
        target_season = season if season is not None else self._season
        if target_season is None:
            return None
        return Route("player", player=player_id, season=target_season)

    def _standing_team_route(self, row: _StandingRow) -> Optional[Route]:
        return self._team_route(row.team_id)

    def _stage_match_route(self, row: _StageRow) -> Optional[Route]:
        return Route("match", match=row.match_id)

    def _stage_home_route(self, row: _StageRow) -> Optional[Route]:
        return self._team_route(row.home_team_id)

    def _stage_away_route(self, row: _StageRow) -> Optional[Route]:
        return self._team_route(row.away_team_id)

    def _stage_advancing_route(self, row: _StageRow) -> Optional[Route]:
        if row.advancing_team_id is None:
            return None
        return self._team_route(row.advancing_team_id)

    def _match_route_for_row(self, row: _MatchRow) -> Optional[Route]:
        return Route("match", match=row.match_id)

    def _match_team_routes(self) -> Tuple[Tuple[str, Callable[[Any], Optional[Route]]], ...]:
        return (
            ("home_name", lambda row: self._team_route(row.home_team_id)),
            ("away_name", lambda row: self._team_route(row.away_team_id)),
        )

    def _leader_player_route(self, row: _LeaderRow) -> Optional[Route]:
        return self._player_route(row.player_id)

    def _award_player_route(self, row: _AwardRow) -> Optional[Route]:
        return self._player_route(row.player_id)

    def _history_champion_route(self, row: _HistoryRow) -> Optional[Route]:
        return row.champion_route

    def _history_player_route(self, row: _HistoryRow) -> Optional[Route]:
        if row.champion_player_id is None:
            return None
        return self._player_route(row.champion_player_id, season=row.season_number)

    def _history_season_route(self, row: _HistoryRow) -> Optional[Route]:
        """单击历史行切换到该赛季的同一赛事（形成可后退历史）。"""

        if self._competition_id == "" or self._season is None:
            return None
        return Route("competition", competition=self._competition_id, season=row.season_number)

    # -- 小组件 -----------------------------------------------------------------

    def _replace_scroll_content(self, scroll: QScrollArea, widget: QWidget) -> None:
        old = scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        scroll.setWidget(widget)

    def _show_tab_empty(self, stack: QStackedWidget, slot: QWidget, empty: EmptyState) -> None:
        layout = slot.layout()
        self._clear_layout(layout)
        layout.addWidget(empty)
        stack.setCurrentWidget(slot)

    def _clear_layout(self, layout) -> None:
        """清空布局中的全部子控件（标记延迟删除，并立即移出控件树）。"""

        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 避免 setParent(None) 产生临时顶层窗口（macOS 全屏退场触发点之一）。
                widget.deleteLater()

    def _muted_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(_MUTED_STYLE)
        return label

    def _bright_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(_BRIGHT_STYLE)
        return label

    def _set_page_empty(self, title: str, description: str) -> None:
        self._competition_name = ""
        self._champion_link = None
        previous = self._empty
        self._empty = EmptyState(title, description)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentWidget(self._empty)
        if previous is not None:
            self._stack.removeWidget(previous)
            previous.deleteLater()

    # -- 指针反馈 ---------------------------------------------------------------

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
