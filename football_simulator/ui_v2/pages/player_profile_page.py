"""球员个人页（阶段 4，实施方案 §8.5 A-F——本次大改的核心交付）。

Route：``Route("player", player=<稳定ID>, season=<赛季>, tab=<overview/splits/
history/awards/matches/trend>)``。

页签与滚动面归属（§8.2 硬规则：每个页签内部只有一个纵向滚动面）：
- 概览 / 奖项 / 评分身价轨迹：内容型页签，单个外层 ``QScrollArea``，内部
  元素（指标卡、摘要行、结算点明细）完整展开，不再嵌套任何小滚动区；
- 各赛事数据 / 比赛记录 / 赛季历史：表格型页签，全高 ``EntityTable`` 是
  唯一纵向滚动面，外层无 ``QScrollArea``；
- ``QTabWidget`` 本身不是滚动面。

实现说明（任务规格中要求明示的选择）：
1. 页签切换只 ``save_state`` 记录、不 ``navigate``——避免每次切页签都污染
   后退/前进历史；route 显式携带 ``tab`` 参数时仍按 route 选中页签。
2. “各赛事数据”页签的总计行实现为表格下方的固定合计条（而非表尾行）：
   ``EntityTable`` 支持表头排序，表尾行会被排序打乱；固定合计条稳定可见。
3. 轨迹页签的“数据表”用完全展开的标签网格渲染（不用 ``QTableWidget``），
   与图表共享外层唯一滚动面，避免嵌套滚动。
4. 头部“本季评分”口径：优先使用查询层 ``list_players`` 按赛季总计推导的
   评分（注明“按现有公式推导”）；无目录行（该赛季无出场）时回退该赛季
   最近一次结算评分；再无则显示“待结算”。不伪造数值。
5. 赛季选择器切换 → ``navigate`` 同球员、同页签、新赛季的 player 路由，
   形成可后退的历史。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base
from football_simulator.queries import player_queries
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    TEXT_COLOR_MUTED,
    ColumnSpec,
    EmptyState,
    EntityLink,
    EntityTable,
    FilterBar,
    PageHeader,
)
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext
from football_simulator.ui_v2.widgets import POSITION_COLORS, TrendSparkline, section_header

_TAB_ORDER = ("overview", "splits", "history", "awards", "matches", "trend")
_TAB_LABELS = {
    "overview": "概览",
    "splits": "各赛事数据",
    "history": "赛季历史",
    "awards": "奖项",
    "matches": "比赛记录",
    "trend": "评分/身价轨迹",
}

_STAGE_LABELS = {
    base.SETTLEMENT_STAGE_WINTER: "冬窗",
    base.SETTLEMENT_STAGE_FINAL: "赛季末",
}

_STAT_TITLES = (
    ("appeared", "出场"),
    ("goals", "进球"),
    ("assists", "助攻"),
    ("chances_created", "创造机会"),
    ("successful_defenses", "成功防守"),
    ("successful_saves", "成功扑救"),
    ("clean_sheets", "零封"),
)

# 按位置突出相关项（前 3 项高亮显示），但不隐藏其他真实字段。
_STAT_PRIORITY = {
    "GK": (
        "appeared",
        "successful_saves",
        "clean_sheets",
        "successful_defenses",
        "chances_created",
        "goals",
        "assists",
    ),
    "DF": (
        "appeared",
        "successful_defenses",
        "clean_sheets",
        "goals",
        "assists",
        "chances_created",
        "successful_saves",
    ),
    "MF": (
        "appeared",
        "assists",
        "chances_created",
        "goals",
        "successful_defenses",
        "successful_saves",
        "clean_sheets",
    ),
}
_STAT_PRIORITY["FW"] = (
    "appeared",
    "goals",
    "assists",
    "chances_created",
    "successful_defenses",
    "successful_saves",
    "clean_sheets",
)

_AWARD_TYPE_LABELS = {
    "top_scorer": "射手王",
    "assist_leader": "助攻王",
    "mvp": "MVP",
}

_SPLIT_COLUMNS = (
    ColumnSpec("competition", "赛事", width=130),
    ColumnSpec("team_name", "球队", width=170),
    ColumnSpec("appeared", "出场", alignment=Qt.AlignRight),
    ColumnSpec("goals", "进球", alignment=Qt.AlignRight),
    ColumnSpec("assists", "助攻", alignment=Qt.AlignRight),
    ColumnSpec("chances_created", "创造机会", alignment=Qt.AlignRight),
    ColumnSpec("successful_defenses", "成功防守", alignment=Qt.AlignRight),
    ColumnSpec("successful_saves", "成功扑救", alignment=Qt.AlignRight),
    ColumnSpec("clean_sheets", "零封", alignment=Qt.AlignRight),
    ColumnSpec("rating", "评分（推导）", alignment=Qt.AlignRight),
)

_HISTORY_COLUMNS = (
    ColumnSpec("season_text", "赛季", width=104),
    ColumnSpec("teams_text", "球队", width=180),
    ColumnSpec("appeared", "出场", alignment=Qt.AlignRight),
    ColumnSpec("goals", "进球", alignment=Qt.AlignRight),
    ColumnSpec("assists", "助攻", alignment=Qt.AlignRight),
    ColumnSpec("chances_created", "创造机会", alignment=Qt.AlignRight),
    ColumnSpec("successful_defenses", "成功防守", alignment=Qt.AlignRight),
    ColumnSpec("successful_saves", "成功扑救", alignment=Qt.AlignRight),
    ColumnSpec("clean_sheets", "零封", alignment=Qt.AlignRight),
    ColumnSpec("rating", "赛季末评分", alignment=Qt.AlignRight),
    ColumnSpec("market_value", "赛季末身价", alignment=Qt.AlignRight),
    ColumnSpec("honors_text", "团队荣誉", width=150),
    ColumnSpec("awards_text", "个人奖项", width=190),
)

_MATCH_COLUMNS = (
    ColumnSpec("week_text", "周", width=88),
    ColumnSpec("competition", "赛事", width=110),
    ColumnSpec("round_text", "轮次", width=70, alignment=Qt.AlignRight),
    ColumnSpec("team_name", "球队", width=155),
    ColumnSpec("opponent_name", "对手", width=155),
    ColumnSpec("venue_text", "主/客", width=56, alignment=Qt.AlignCenter),
    ColumnSpec("score_text", "比分", width=72, alignment=Qt.AlignCenter),
    ColumnSpec("goals", "进球", alignment=Qt.AlignRight),
    ColumnSpec("assists", "助攻", alignment=Qt.AlignRight),
    ColumnSpec("chances_created", "创造机会", alignment=Qt.AlignRight),
    ColumnSpec("successful_defenses", "成功防守", alignment=Qt.AlignRight),
    ColumnSpec("successful_saves", "成功扑救", alignment=Qt.AlignRight),
    ColumnSpec("clean_sheets", "零封", alignment=Qt.AlignRight),
)

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = "color: #f8fbff; background: transparent;"


class _FormattedNumber(float):
    """带显示格式的数值：参与数值排序，显示时使用格式化文本。"""

    def __new__(cls, value: float, template: str = "{:.2f}"):
        instance = super().__new__(cls, value)
        instance._template = template  # type: ignore[attr-defined]
        return instance

    def __str__(self) -> str:  # noqa: D105 - Qt DisplayRole 走 str()
        return self._template.format(float(self))  # type: ignore[attr-defined]


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _money_text(value: Optional[float]) -> str:
    return "—" if value is None else f"{float(value):.2f}M"


def _stat_text(stats: player_queries.PlayerStatLine) -> str:
    return (
        f"进 {stats.goals} · 助 {stats.assists} · 创 {stats.chances_created} · "
        f"防 {stats.successful_defenses} · 扑 {stats.successful_saves} · 零 {stats.clean_sheets}"
    )


def _competition_sort_index(competition: str) -> int:
    try:
        return base.ALL_COMPETITIONS.index(competition)
    except ValueError:
        return len(base.ALL_COMPETITIONS)


def _clear_layout(layout) -> None:
    """清空布局中的全部子项（含嵌套布局），控件标记延迟删除。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _stat_card(title: str, value: str, note: str, accent: bool) -> QWidget:
    """赛季总计指标卡；``accent=True`` 表示按位置突出的相关项。"""
    frame = QFrame()
    frame.setObjectName("cardFrame")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(2)
    title_label = QLabel(title)
    title_label.setStyleSheet(
        f"font-size: 12px; font-weight: 700; background: transparent; "
        f"color: {'#7dd3fc' if accent else TEXT_COLOR_MUTED};"
    )
    value_label = QLabel(value)
    value_label.setStyleSheet("font-size: 22px; font-weight: 800; background: transparent; color: #f8fbff;")
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    if note:
        note_label = QLabel(note)
        note_label.setStyleSheet(f"font-size: 11px; background: transparent; {_MUTED_STYLE}")
        note_label.setWordWrap(True)
        layout.addWidget(note_label)
    frame.value_label = value_label  # type: ignore[attr-defined]
    return frame


@dataclass(frozen=True)
class _SplitRow:
    competition: str
    team_name: str
    appeared: int
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int
    rating: Optional[float]


def _split_row(split: player_queries.PlayerCompetitionSplit) -> _SplitRow:
    return _SplitRow(
        competition=split.competition,
        team_name=split.team.display_name,
        appeared=split.stats.appeared,
        goals=split.stats.goals,
        assists=split.stats.assists,
        chances_created=split.stats.chances_created,
        successful_defenses=split.stats.successful_defenses,
        successful_saves=split.stats.successful_saves,
        clean_sheets=split.stats.clean_sheets,
        rating=None if split.rating is None else _FormattedNumber(split.rating),
    )


@dataclass(frozen=True)
class _HistoryRow:
    season_number: int
    season_text: str
    teams_text: str
    appeared: int
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int
    rating: Optional[float]
    market_value: Optional[float]
    honors_text: str
    awards_text: str


@dataclass(frozen=True)
class _MatchRow:
    match_id: str
    week_text: str
    competition: str
    round_text: str
    team_name: str
    opponent_name: str
    venue_text: str
    score_text: str
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int


def _match_row(source: player_queries.PlayerMatchRow) -> _MatchRow:
    if source.home_goals is None or source.away_goals is None:
        score = "未赛"
    else:
        score = f"{source.home_goals}-{source.away_goals}"
    return _MatchRow(
        match_id=source.match_id,
        week_text=f"第 {source.week_number} 周",
        competition=source.competition,
        round_text=f"第 {source.round_number} 轮",
        team_name=source.team.display_name,
        opponent_name=source.opponent.display_name,
        venue_text="主" if source.is_home else "客",
        score_text=score,
        goals=source.stats.goals,
        assists=source.stats.assists,
        chances_created=source.stats.chances_created,
        successful_defenses=source.stats.successful_defenses,
        successful_saves=source.stats.successful_saves,
        clean_sheets=source.stats.clean_sheets,
    )


class PlayerProfilePage(EntityPageBase):
    """球员个人页：页头固定信息栏 + 六个页签（概览/各赛事/历史/奖项/比赛/轨迹）。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._player_id: Optional[str] = None
        self._season: int = 0
        self._current_tab: str = "overview"
        self._cached_seasons: List[base.SeasonRef] = []
        self._profile: Optional[player_queries.PlayerSeasonProfile] = None
        self._career: Optional[player_queries.PlayerCareer] = None
        self._season_profiles: Dict[int, player_queries.PlayerSeasonProfile] = {}
        self._season_rating: Optional[float] = None
        self._season_rating_note: str = ""
        self._header: Optional[PageHeader] = None
        self._season_combo: Optional[QComboBox] = None
        self._position_badge: Optional[QLabel] = None
        self._type_badge: Optional[QLabel] = None
        self._team_link: Optional[EntityLink] = None
        self._team_none_label: Optional[QLabel] = None
        self._ability_value: Optional[QLabel] = None
        self._rating_value: Optional[QLabel] = None
        self._rating_note: Optional[QLabel] = None
        self._value_value: Optional[QLabel] = None
        self._value_note: Optional[QLabel] = None
        self._overview_totals: Dict[str, QLabel] = {}
        self._splits_total_labels: Dict[str, QLabel] = {}
        self._matches_combo: Optional[QComboBox] = None
        self._trend_points: List[player_queries.SettlementPoint] = []
        self._trend_detail_rows: List[tuple] = []
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；内容在 refresh 中重建） ---------------------------

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(0)
        self._page_stack = QStackedWidget()
        page_layout.addWidget(self._page_stack, 1)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        self._header_slot = QVBoxLayout()
        self._header_slot.setContentsMargins(0, 0, 0, 0)
        main_layout.addLayout(self._header_slot)

        self._info_slot = QVBoxLayout()
        self._info_slot.setContentsMargins(0, 0, 0, 0)
        main_layout.addLayout(self._info_slot)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("playerProfileTabs")
        self._tab_stacks: Dict[str, QStackedWidget] = {}
        self._scrolls: Dict[str, QScrollArea] = {}
        self._tables: Dict[str, EntityTable] = {}

        for key in _TAB_ORDER:
            stack = QStackedWidget()
            if key in ("overview", "awards", "trend"):
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.NoFrame)
                scroll.setObjectName(f"profileScroll_{key}")
                stack.addWidget(scroll)
                self._scrolls[key] = scroll
            elif key == "splits":
                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(8)
                self._splits_total_bar = self._build_total_bar()
                container_layout.addWidget(self._splits_total_bar)
                table = EntityTable(_SPLIT_COLUMNS, navigator=self._context.navigate)
                self._tables[key] = table
                container_layout.addWidget(table, 1)
                stack.addWidget(container)
            elif key == "matches":
                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(8)
                self._matches_filter = FilterBar()
                self._matches_combo = self._matches_filter.add_combo(
                    "赛事", ["全部赛事"], "profileMatchCompetitionCombo"
                )
                self._matches_combo.currentIndexChanged.connect(self._on_match_filter_changed)
                container_layout.addWidget(self._matches_filter)
                table = EntityTable(_MATCH_COLUMNS, navigator=self._context.navigate)
                self._tables[key] = table
                container_layout.addWidget(table, 1)
                stack.addWidget(container)
            else:  # history
                table = EntityTable(_HISTORY_COLUMNS, navigator=self._context.navigate)
                self._tables[key] = table
                stack.addWidget(table)
            empty = self._build_tab_empty_state(key)
            stack.addWidget(empty)
            self._tab_stacks[key] = stack
            self._tabs.addTab(stack, _TAB_LABELS[key])

        self._tabs.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self._tabs, 1)
        self._page_stack.addWidget(main)

        self._error_state = EmptyState(
            "球员不存在或已离开注册表",
            "注册表中找不到该球员。可能链接指向了另一个存档，或该默认球员已被替换。",
        )
        self._page_stack.addWidget(self._error_state)

    def _build_total_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("cardFrame")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)
        title = QLabel("全部赛事总计")
        title.setStyleSheet("font-weight: 800; background: transparent; color: #f8fbff;")
        layout.addWidget(title)
        self._splits_total_labels = {}
        for field, title_text in _STAT_TITLES:
            label = QLabel("—")
            label.setStyleSheet(_MUTED_STYLE)
            layout.addWidget(label)
            self._splits_total_labels[field] = label
        rating_label = QLabel("评分：—")
        rating_label.setStyleSheet(_MUTED_STYLE)
        layout.addWidget(rating_label)
        self._splits_total_labels["rating"] = rating_label
        layout.addStretch(1)
        note = QLabel("合计口径：赛季全部比赛（按比赛当时球队归属）")
        note.setStyleSheet(f"font-size: 11px; {_MUTED_STYLE}")
        layout.addWidget(note)
        return bar

    def _build_tab_empty_state(self, key: str) -> EmptyState:
        copy_by_tab = {
            "overview": (
                "该赛季暂无出场记录",
                "出场按球队参赛当时注册阵容记录；该球员在所选赛季没有出场数据。",
            ),
            "splits": (
                "暂无各赛事分段数据",
                "该球员在所选赛季没有出场数据，因此没有任何赛事×球队分段。",
            ),
            "history": (
                "暂无赛季记录",
                "该球员还没有任何赛季出场数据。",
            ),
            "awards": (
                "暂无奖项记录",
                "该球员还没有获得个人奖项或球队荣誉。",
            ),
            "matches": (
                "暂无比赛记录",
                "该球员在所选赛季没有出场数据，因此没有比赛记录。",
            ),
            "trend": (
                "暂无评分/身价轨迹",
                "该球员还没有冬窗或赛季末结算记录。默认球员不参与身价结算。",
            ),
        }
        title, description = copy_by_tab[key]
        return EmptyState(title, description)

    # -- 数据刷新 -----------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "player":
            return
        player_id = str(route.params.get("player", ""))
        state = self.stored_state()
        season = route.int_param("season")
        tab_key = route.params.get("tab") or str(state.get("tab") or "overview")
        if tab_key not in _TAB_ORDER:
            tab_key = "overview"
        try:
            with base.open_read_connection(self.save_name()) as conn:
                self._cached_seasons = base.load_seasons(conn)
                if season is None:
                    season = base.resolve_current_season(conn).season_number
                profile = player_queries.get_player_season_profile(conn, player_id, int(season))
                career = player_queries.get_player_career(conn, player_id)
                rating, rating_note = self._resolve_season_rating(conn, profile, int(season))
                season_profiles = self._load_season_profiles(conn, player_id, career, profile, int(season))
        except base.MissingSaveError:
            self._show_error("未初始化存档", "当前还没有可用的存档数据库。请先在“存档”页新建或选择一个存档。")
            return
        except KeyError as exc:
            self._show_error("球员不存在或已离开注册表", str(exc))
            return

        self._player_id = profile.identity.player_id
        self._season = int(season)
        self._profile = profile
        self._career = career
        self._season_profiles = season_profiles
        self._season_rating = rating
        self._season_rating_note = rating_note
        self._current_tab = tab_key

        self._render_header(profile, career)
        self._render_overview(profile)
        self._render_splits(profile)
        self._render_history(career)
        self._render_awards()
        self._render_matches(profile)
        self._render_trend(profile)

        index = _TAB_ORDER.index(tab_key)
        self._tabs.blockSignals(True)
        self._tabs.setCurrentIndex(index)
        self._tabs.blockSignals(False)
        self._page_stack.setCurrentIndex(0)
        self.save_state({"tab": tab_key})

    def route_context(self) -> dict:
        if self._profile is not None:
            return {"player_name": self._profile.identity.display_name}
        return {}

    def _show_error(self, title: str, description: Optional[str]) -> None:
        # EmptyState 的文案在构造时固定；每次替换一块新的错误页。
        self._error_state = EmptyState(title, description)
        self._page_stack.addWidget(self._error_state)
        self._page_stack.setCurrentWidget(self._error_state)

    def _load_season_profiles(
        self,
        conn,
        player_id: str,
        career: player_queries.PlayerCareer,
        selected: player_queries.PlayerSeasonProfile,
        season: int,
    ) -> Dict[int, player_queries.PlayerSeasonProfile]:
        """逐赛季取 profile（赛季历史/奖项页签需要球队、荣誉与奖项明细）。"""
        result: Dict[int, player_queries.PlayerSeasonProfile] = {season: selected}
        for career_season in career.seasons:
            number = career_season.season_number
            if number in result:
                continue
            try:
                result[number] = player_queries.get_player_season_profile(conn, player_id, number)
            except KeyError:
                continue
        return result

    def _resolve_season_rating(
        self,
        conn,
        profile: player_queries.PlayerSeasonProfile,
        season: int,
    ):
        """头部“本季评分”：优先赛季总计推导口径，其次该赛季最近结算。"""
        try:
            directory_rows = player_queries.list_players(conn, season)
        except KeyError:
            directory_rows = []
        match = next((row for row in directory_rows if row.player_id == profile.identity.player_id), None)
        if match is not None and match.rating is not None:
            return float(match.rating), "按现有公式推导（赛季总计口径）"
        points = [
            point
            for point in profile.trend
            if point.season_number == season and point.rating is not None
        ]
        if points:
            last = points[-1]
            return float(last.rating), f"第 {season} 赛季{_stage_label(last.stage)}结算评分"
        return None, ""

    # -- 页头与固定信息栏 -----------------------------------------------------

    def _render_header(
        self,
        profile: player_queries.PlayerSeasonProfile,
        career: player_queries.PlayerCareer,
    ) -> None:
        identity = profile.identity
        _clear_layout(self._header_slot)
        _clear_layout(self._info_slot)

        season_combo = QComboBox()
        season_combo.setObjectName("profileSeasonCombo")
        season_caption = QLabel("赛季")
        season_caption.setStyleSheet(_MUTED_STYLE)
        selector = QWidget()
        selector_layout = QHBoxLayout(selector)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(6)
        selector_layout.addWidget(season_caption)
        selector_layout.addWidget(season_combo)

        route = Route("player", player=self._player_id or "", season=self._season)
        header = PageHeader(
            identity.display_name,
            breadcrumbs=[],
            navigator=self._context.navigate,
            actions=[selector],
        )
        seasons = self._cached_seasons
        for season in seasons:
            label = f"第 {season.season_number} 赛季"
            if not season.is_completed:
                label += "（进行中）"
            season_combo.addItem(label, season.season_number)
        index = season_combo.findData(self._season)
        season_combo.setCurrentIndex(max(0, index))
        season_combo.currentIndexChanged.connect(self._on_season_changed)
        self._header = header
        self._season_combo = season_combo
        self._header_slot.addWidget(header)

        # -- 固定信息栏 --
        info = QWidget()
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(16)

        self._position_badge = QLabel(identity.position)
        self._position_badge.setStyleSheet(self._badge_style(POSITION_COLORS.get(identity.position)))
        info_layout.addWidget(self._position_badge)

        self._type_badge = QLabel("真实球员" if identity.is_real else "默认球员")
        self._type_badge.setStyleSheet(self._badge_style("#86efac" if identity.is_real else "#94a3b8"))
        info_layout.addWidget(self._type_badge)

        info_layout.addWidget(self._info_caption("当前球队"))
        if profile.current_team is not None:
            self._team_link = EntityLink(
                profile.current_team.display_name,
                Route("team", team=profile.current_team.team_id, season=self._season),
                self._context.navigate,
            )
            self._team_none_label = None
            info_layout.addWidget(self._team_link)
        else:
            self._team_link = None
            self._team_none_label = QLabel("—")
            self._team_none_label.setStyleSheet(_MUTED_STYLE)
            info_layout.addWidget(self._team_none_label)

        info_layout.addWidget(self._info_caption("能力"))
        self._ability_value = QLabel(str(profile.ability))
        self._ability_value.setStyleSheet(_BRIGHT_STYLE)
        info_layout.addWidget(self._ability_value)

        info_layout.addWidget(self._info_caption("本季评分"))
        if self._season_rating is not None:
            self._rating_value = QLabel(f"{self._season_rating:.2f}")
            self._rating_note = QLabel(self._season_rating_note)
        else:
            self._rating_value = QLabel("待结算")
            self._rating_note = QLabel("该赛季暂无评分记录")
        self._rating_value.setStyleSheet(_BRIGHT_STYLE)
        self._rating_note.setStyleSheet(f"font-size: 11px; {_MUTED_STYLE}")
        info_layout.addWidget(self._rating_value)
        info_layout.addWidget(self._rating_note)

        info_layout.addWidget(self._info_caption("身价"))
        latest_value, latest_note = self._latest_market_value(profile)
        self._value_value = QLabel(latest_value)
        self._value_note = QLabel(latest_note)
        self._value_value.setStyleSheet(_BRIGHT_STYLE)
        self._value_note.setStyleSheet(f"font-size: 11px; {_MUTED_STYLE}")
        info_layout.addWidget(self._value_value)
        info_layout.addWidget(self._value_note)

        info_layout.addStretch(1)
        self._info_slot.addWidget(info)

    @staticmethod
    def _badge_style(color: Optional[str]) -> str:
        background = color or "#94a3b8"
        return (
            f"background: {background}; color: #0b1220; border-radius: 9px; "
            "padding: 2px 10px; font-weight: 800; font-size: 12px;"
        )

    @staticmethod
    def _info_caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"font-size: 12px; {_MUTED_STYLE}")
        return label

    @staticmethod
    def _latest_market_value(profile: player_queries.PlayerSeasonProfile):
        """(显示值, 说明文案)：trend 最近一个有身价的结算点；无则解释性文案。"""
        for point in reversed(profile.trend):
            if point.market_value is not None:
                stage_text = _stage_label(point.stage)
                return (
                    f"{float(point.market_value):.2f}M",
                    f"最近结算：第 {point.season_number} 赛季{stage_text}",
                )
        if not profile.identity.is_real:
            return "—", "默认球员不参与身价结算"
        return "待结算", "身价仅冬窗/赛季末结算产生"

    # -- A 概览 --------------------------------------------------------------

    def _render_overview(self, profile: player_queries.PlayerSeasonProfile) -> None:
        stack = self._tab_stacks["overview"]
        if profile.season_totals.appeared == 0 and not profile.competition_splits:
            stack.setCurrentIndex(1)
            return
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 8, 12)
        layout.setSpacing(14)

        layout.addWidget(
            section_header(
                f"第 {self._season} 赛季总计",
                "出场按球队参赛当时注册阵容记录；高亮项为该位置的主要数据。",
            )
        )
        totals_holder = QWidget()
        grid = QGridLayout(totals_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        priority = _STAT_PRIORITY.get(profile.identity.position, tuple(field for field, _ in _STAT_TITLES))
        totals = profile.season_totals
        self._overview_totals = {}
        for index, field in enumerate(priority):
            title = next(text for name, text in _STAT_TITLES if name == field)
            card = _stat_card(title, str(getattr(totals, field)), "", accent=index < 3)
            grid.addWidget(card, index // 4, index % 4)
            self._overview_totals[field] = card.value_label  # type: ignore[attr-defined]
        for column in range(4):
            grid.setColumnStretch(column, 1)
        layout.addWidget(totals_holder)

        layout.addWidget(
            section_header(
                "各赛事数据",
                "赛事评分（按现有公式推导）；单击赛事名打开赛事页，单击球队名打开球队页。",
            )
        )
        if profile.competition_splits:
            layout.addWidget(self._split_summary_header())
            for split in profile.competition_splits:
                layout.addWidget(self._build_split_summary_row(split))
        else:
            layout.addWidget(self._muted_label("该赛季没有各赛事分段数据。"))

        layout.addWidget(
            section_header("最近比赛", "最近 8 场；单击比赛信息打开赛后报告，单击对手打开球队页。")
        )
        recent = list(reversed(profile.match_log[-8:]))
        if recent:
            for source in recent:
                layout.addWidget(self._build_recent_match_row(source))
        else:
            layout.addWidget(self._muted_label("该赛季暂无比赛记录。"))

        layout.addWidget(section_header("个人奖项"))
        awards_block = self._build_awards_block(profile.awards, self._season, "overviewPersonalAwards")
        layout.addWidget(awards_block)

        layout.addWidget(section_header("球队荣誉"))
        honors_block = self._build_honors_block(
            profile.team_honors, "overviewTeamHonors", empty_text="本赛季暂无球队荣誉记录"
        )
        layout.addWidget(honors_block)

        layout.addStretch(1)
        self._scrolls["overview"].setWidget(inner)
        stack.setCurrentIndex(0)

    def _muted_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(_MUTED_STYLE)
        return label

    _SUMMARY_WIDTHS = (130, 150, 48, 48, 48, 48, 48, 48, 48, 72)

    def _split_summary_header(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        titles = ("赛事", "球队", "出场", "进球", "助攻", "创造", "防守", "扑救", "零封", "评分")
        for width, title in zip(self._SUMMARY_WIDTHS, titles):
            label = QLabel(title)
            label.setStyleSheet(f"font-size: 12px; font-weight: 700; {_MUTED_STYLE}")
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter if title not in ("赛事", "球队") else Qt.AlignLeft)
            layout.addWidget(label)
        layout.addStretch(1)
        return row

    def _build_split_summary_row(self, split: player_queries.PlayerCompetitionSplit) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        competition_link = EntityLink(
            split.competition,
            Route("competition", competition=split.competition, season=self._season),
            self._context.navigate,
        )
        competition_link.setFixedWidth(self._SUMMARY_WIDTHS[0])
        layout.addWidget(competition_link)
        team_link = EntityLink(
            split.team.display_name,
            Route("team", team=split.team.team_id, season=self._season),
            self._context.navigate,
        )
        team_link.setFixedWidth(self._SUMMARY_WIDTHS[1])
        layout.addWidget(team_link)
        values = (
            split.stats.appeared,
            split.stats.goals,
            split.stats.assists,
            split.stats.chances_created,
            split.stats.successful_defenses,
            split.stats.successful_saves,
            split.stats.clean_sheets,
        )
        for width, value in zip(self._SUMMARY_WIDTHS[2:-1], values):
            label = QLabel(str(value))
            label.setStyleSheet(_BRIGHT_STYLE)
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addWidget(label)
        rating_label = QLabel("—" if split.rating is None else f"{split.rating:.2f}")
        rating_label.setStyleSheet(_BRIGHT_STYLE)
        rating_label.setFixedWidth(self._SUMMARY_WIDTHS[-1])
        rating_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(rating_label)
        layout.addStretch(1)
        return row

    def _build_recent_match_row(self, source: player_queries.PlayerMatchRow) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        match_link = EntityLink(
            f"第 {source.week_number} 周 · {source.competition}",
            Route("match", match=source.match_id),
            self._context.navigate,
        )
        match_link.setFixedWidth(190)
        layout.addWidget(match_link)
        opponent_link = EntityLink(
            source.opponent.display_name,
            Route("team", team=source.opponent.team_id, season=self._season),
            self._context.navigate,
        )
        opponent_link.setFixedWidth(150)
        layout.addWidget(opponent_link)
        score = "—" if source.home_goals is None or source.away_goals is None else (
            f"{source.home_goals}-{source.away_goals}"
        )
        score_label = QLabel(("主 " if source.is_home else "客 ") + score)
        score_label.setStyleSheet(_BRIGHT_STYLE)
        score_label.setFixedWidth(72)
        layout.addWidget(score_label)
        stats_label = QLabel(_stat_text(source.stats))
        stats_label.setStyleSheet(_MUTED_STYLE)
        layout.addWidget(stats_label, 1)
        return row

    def _build_awards_block(
        self,
        awards: player_queries.PlayerSeasonAwards,
        season: int,
        object_name: str,
        empty_text: str = "该赛季暂无个人奖项",
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        frame.setProperty("block_role", object_name)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        caption = QLabel("个人奖项")
        caption.setStyleSheet("font-weight: 800; background: transparent; color: #f8fbff;")
        layout.addWidget(caption)
        rows: List[QWidget] = []
        if awards.top20 is not None:
            score_text = "—" if awards.top20.score is None else f"{awards.top20.score:.2f}"
            rows.append(self._muted_label(f"年度 Top20 第 {awards.top20.rank} 名（分数 {score_text}）"))
        for award in awards.competitions:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            type_label = _AWARD_TYPE_LABELS.get(award.award_type, award.award_type)
            score_text = "" if award.score is None else f"（分数 {award.score:.2f}）"
            competition_link = EntityLink(
                award.competition or "—",
                Route("competition", competition=award.competition or "", season=season),
                self._context.navigate,
            )
            row_layout.addWidget(competition_link)
            detail = QLabel(f"{type_label}{score_text}")
            detail.setStyleSheet(_BRIGHT_STYLE)
            row_layout.addWidget(detail)
            row_layout.addStretch(1)
            rows.append(row)
        if not rows:
            rows.append(self._muted_label(empty_text))
        for row in rows:
            layout.addWidget(row)
        return frame

    def _build_honors_block(
        self,
        honors: Sequence[str],
        object_name: str,
        empty_text: str = "该赛季暂无球队荣誉",
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        frame.setProperty("block_role", object_name)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        caption = QLabel("球队荣誉")
        caption.setStyleSheet("font-weight: 800; background: transparent; color: #f8fbff;")
        layout.addWidget(caption)
        if honors:
            for honor in honors:
                label = QLabel(f"· {honor}")
                label.setStyleSheet(_BRIGHT_STYLE)
                layout.addWidget(label)
        else:
            layout.addWidget(self._muted_label(empty_text))
        return frame

    # -- B 各赛事数据 ---------------------------------------------------------

    def _render_splits(self, profile: player_queries.PlayerSeasonProfile) -> None:
        stack = self._tab_stacks["splits"]
        rows = [_split_row(split) for split in profile.competition_splits]
        if not rows:
            stack.setCurrentIndex(1)
            return
        self._tables["splits"].set_rows(rows, route_for_row=self._split_route_for_row)
        totals = profile.season_totals
        for field, title in _STAT_TITLES:
            self._splits_total_labels[field].setText(f"{title} {getattr(totals, field)}")
        if self._season_rating is not None:
            self._splits_total_labels["rating"].setText(f"评分：{self._season_rating:.2f}（按现有公式推导）")
        else:
            self._splits_total_labels["rating"].setText("评分：—")
        stack.setCurrentIndex(0)

    def _split_route_for_row(self, row: _SplitRow):
        return Route("competition", competition=row.competition, season=self._season)

    # -- C 赛季历史 -----------------------------------------------------------

    def _render_history(self, career: player_queries.PlayerCareer) -> None:
        stack = self._tab_stacks["history"]
        rows: List[_HistoryRow] = []
        for career_season in career.seasons:
            season_profile = self._season_profiles.get(career_season.season_number)
            teams_text = (
                "、".join(team.display_name for team in season_profile.season_teams)
                if season_profile and season_profile.season_teams
                else "—"
            )
            honors_text = (
                "；".join(season_profile.team_honors) if season_profile and season_profile.team_honors else "—"
            )
            awards_text = "；".join(career_season.award_labels) if career_season.award_labels else "—"
            totals = career_season.totals
            rows.append(
                _HistoryRow(
                    season_number=career_season.season_number,
                    season_text=f"第 {career_season.season_number} 赛季",
                    teams_text=teams_text,
                    appeared=totals.appeared,
                    goals=totals.goals,
                    assists=totals.assists,
                    chances_created=totals.chances_created,
                    successful_defenses=totals.successful_defenses,
                    successful_saves=totals.successful_saves,
                    clean_sheets=totals.clean_sheets,
                    rating=(
                        None
                        if career_season.season_rating is None
                        else _FormattedNumber(career_season.season_rating)
                    ),
                    market_value=(
                        None
                        if career_season.market_value is None
                        else _FormattedNumber(career_season.market_value, "{:.2f}M")
                    ),
                    honors_text=honors_text,
                    awards_text=awards_text,
                )
            )
        if not rows:
            stack.setCurrentIndex(1)
            return
        self._tables["history"].set_rows(rows, route_for_row=self._history_route_for_row)
        stack.setCurrentIndex(0)

    def _history_route_for_row(self, row: _HistoryRow):
        # 单击赛季行切换 profile 上下文：保持在当前页签查看该赛季信息。
        return Route("player", player=self._player_id or "", season=row.season_number, tab=self._current_tab)

    # -- D 奖项 ---------------------------------------------------------------

    def _render_awards(self) -> None:
        scroll = self._scrolls["awards"]
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 8, 12)
        layout.setSpacing(14)

        season_numbers = sorted(
            set(self._season_profiles) | {self._season},
            reverse=True,
        )
        for number in season_numbers:
            season_profile = self._season_profiles.get(number)
            awards = season_profile.awards if season_profile is not None else player_queries.PlayerSeasonAwards(
                top20=None, competitions=[]
            )
            honors = season_profile.team_honors if season_profile is not None else []
            title = QLabel(f"第 {number} 赛季")
            title.setStyleSheet("font-size: 16px; font-weight: 800; background: transparent; color: #f8fbff;")
            layout.addWidget(title)
            # 个人奖项与球队荣誉严格分区（§8.5D），绝不混排。
            layout.addWidget(self._build_awards_block(awards, number, "personalAwardsBlock"))
            layout.addWidget(self._build_honors_block(honors, "teamHonorsBlock"))

        layout.addStretch(1)
        scroll.setWidget(inner)
        self._tab_stacks["awards"].setCurrentIndex(0)

    # -- E 比赛记录 -----------------------------------------------------------

    def _render_matches(self, profile: player_queries.PlayerSeasonProfile) -> None:
        stack = self._tab_stacks["matches"]
        all_rows = [_match_row(source) for source in profile.match_log]
        if not all_rows:
            stack.setCurrentIndex(1)
            return
        competitions = []
        for row in all_rows:
            if row.competition not in competitions:
                competitions.append(row.competition)
        competitions.sort(key=lambda name: (_competition_sort_index(name), name))
        self._rebuild_matches_combo(competitions)

        selected = self._matches_combo.currentText()
        rows = [row for row in all_rows if selected == "全部赛事" or row.competition == selected]
        self._tables["matches"].set_rows(rows, route_for_row=self._match_route_for_row)
        stack.setCurrentIndex(0)

    def _rebuild_matches_combo(self, competitions: Sequence[str]) -> None:
        combo = self._matches_combo
        if combo is None:
            return
        state = self.stored_state()
        desired = str(state.get("matchCompetition") or combo.currentText() or "全部赛事")
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("全部赛事")
        combo.addItems(list(competitions))
        if desired in competitions or desired == "全部赛事":
            combo.setCurrentText(desired)
        combo.blockSignals(False)

    def _match_route_for_row(self, row: _MatchRow):
        return Route("match", match=row.match_id)

    def _on_match_filter_changed(self, *_args) -> None:
        if self._profile is None:
            return
        self.save_state({"tab": self._current_tab, "matchCompetition": self._matches_combo.currentText()})
        self._render_matches(self._profile)

    # -- F 评分/身价轨迹 -------------------------------------------------------

    def _render_trend(self, profile: player_queries.PlayerSeasonProfile) -> None:
        stack = self._tab_stacks["trend"]
        points = profile.trend
        if not points:
            stack.setCurrentIndex(1)
            return
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 8, 12)
        layout.setSpacing(14)

        layout.addWidget(
            section_header(
                "评分/身价结算轨迹",
                "结算点仅有冬窗（第 24 周）与赛季末（第 49 周）两种；默认球员不参与身价结算。",
            )
        )
        rating_points = [point for point in points if point.rating is not None]
        value_points = [point for point in points if point.market_value is not None]
        labels = [f"S{point.season_number}·{_stage_label(point.stage)}" for point in points]
        rating_values = [float(point.rating) for point in points if point.rating is not None]
        value_values = [float(point.market_value) for point in points if point.market_value is not None]
        rating_labels = [label for label, point in zip(labels, points) if point.rating is not None]
        value_labels = [label for label, point in zip(labels, points) if point.market_value is not None]

        charts_row = QHBoxLayout()
        charts_row.setContentsMargins(0, 0, 0, 0)
        charts_row.setSpacing(12)
        rating_chart = TrendSparkline("评分结算轨迹", "#7dd3fc")
        rating_chart.set_points(rating_values, rating_labels)
        value_chart = TrendSparkline("身价结算轨迹", "#facc15")
        value_chart.set_points(value_values, value_labels)
        charts_row.addWidget(rating_chart, 1)
        charts_row.addWidget(value_chart, 1)
        charts_holder = QWidget()
        charts_holder.setLayout(charts_row)
        layout.addWidget(charts_holder)
        if not value_points:
            layout.addWidget(self._muted_label("暂无身价结算点：默认球员不参与身价结算。"))

        layout.addWidget(section_header("结算点明细", "与上方图表共享同一页面滚动，完整列出全部结算点。"))
        detail_rows: List[tuple] = []
        detail_holder = QWidget()
        detail_grid = QGridLayout(detail_holder)
        detail_grid.setContentsMargins(0, 0, 0, 0)
        detail_grid.setSpacing(6)
        headers = ("赛季", "阶段", "周", "评分", "身价")
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet(f"font-size: 12px; font-weight: 700; {_MUTED_STYLE}")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            detail_grid.addWidget(label, 0, column)
        for row_index, point in enumerate(points, start=1):
            values = (
                f"第 {point.season_number} 赛季",
                _stage_label(point.stage),
                f"第 {point.week_number} 周",
                "—" if point.rating is None else f"{float(point.rating):.2f}",
                _money_text(point.market_value),
            )
            detail_rows.append(values)
            for column, value in enumerate(values):
                label = QLabel(value)
                label.setStyleSheet(_BRIGHT_STYLE if column >= 3 else _MUTED_STYLE)
                detail_grid.addWidget(label, row_index, column)
        detail_grid.setColumnStretch(5, 1)
        layout.addWidget(detail_holder)

        layout.addStretch(1)
        self._scrolls["trend"].setWidget(inner)
        self._trend_points = list(points)
        self._trend_detail_rows = detail_rows
        stack.setCurrentIndex(0)

    # -- 页签与赛季交互 ---------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(_TAB_ORDER):
            return
        key = _TAB_ORDER[index]
        self._current_tab = key
        # 只记录状态、不 navigate：避免每次切页签都污染后退/前进历史；
        # route 显式携带 tab 参数时仍以 route 为准（见 refresh）。
        state = {"tab": key}
        if self._matches_combo is not None:
            state["matchCompetition"] = self._matches_combo.currentText()
        self.save_state(state)

    def _on_season_changed(self, index: int) -> None:
        combo = self._season_combo
        if combo is None:
            return
        data = combo.itemData(index)
        if data is None or self._player_id is None:
            return
        season = int(data)
        if season == self._season:
            return
        self.navigate(
            Route("player", player=self._player_id, season=season, tab=self._current_tab)
        )
