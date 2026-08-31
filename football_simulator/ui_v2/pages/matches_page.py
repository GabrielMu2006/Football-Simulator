"""比赛中心列表页（阶段 4，实施方案 §8.7 比赛列表）。

Route：``Route("matches", season=<赛季号>, competition=<赛事名可选>, week=<周次可选>)``。

布局与滚动（§8.2 硬规则）：
- 全高 ``EntityTable`` 主表是本页唯一纵向滚动面，占满剩余高度，外层不套
  ``QScrollArea``；已赛与未赛比赛统一完整呈现，不做"当周前 12 条"截断。
- 行激活（双击 / 键盘 Enter）→ ``Route("match", match=<match_id>)``：已赛进
  赛后报告、未赛进赛前页（同一个稳定 match_id 实体页）。

筛选与状态保留（§7.3 / 任务规格明示的选择）：
- 路由参数优先：``season`` 必填；路由显式携带 ``competition``/``week`` 时以
  路由为准；无显式参数时恢复页面保存状态（``save_state``）。
- 赛季下拉变化 → ``navigate`` 新路由（并保留当前具体的赛事/周次参数）：
  ``season`` 是路由必填参数，若只改页面状态，后退/前进返回时会按"路由参数
  优先"被旧 ``season`` 覆盖，无法保证"回来时筛选与列表一致"，因此切赛季必须
  产生新路由。
- 赛事/周次变化 → 只 ``save_state`` 并重查，不 ``navigate``（避免每次改筛选
  都污染后退/前进历史；无显式参数的路由返回时恢复这些筛选）。
- 状态筛选（已赛/未赛）不进路由（路由 schema 无该参数），仅存页面状态；切换
  赛季产生新路由键时回到"全部状态"。

旧版信息能力对照（整体重写，不丢失信息）：周次/赛事选择 → 筛选条下拉；
"比赛场数/赛事类型/当前焦点"指标卡 → 表格上方摘要行；"当周比赛列表 + 查看
完整比赛列表" → 全量主表；"比赛详情面板 + 查看主队/客队 + 完整事件/完整球员
数据" → 比赛详情页（``Route("match", ...)``，球队/球员以链接跳转）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base
from football_simulator.queries import match_queries
from football_simulator.ui_v2.components import (
    TEXT_COLOR_MUTED,
    ColumnSpec,
    EmptyState,
    EntityTable,
    FilterBar,
    PageHeader,
)
from football_simulator.ui_v2.components.crest_delegate import TeamCrestTextDelegate
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext

_ALL_COMPETITION = "全部赛事"
_ALL_WEEK = "全部周次"
_ALL_STATUS = "全部状态"
_STATUS_OPTIONS = (_ALL_STATUS, "已赛", "未赛")
_STATUS_QUERY = {
    "已赛": base.MATCH_STATUS_COMPLETED,
    "未赛": base.MATCH_STATUS_SCHEDULED,
}
_MIN_WEEK, _MAX_WEEK = 1, 52

_LIST_COLUMNS = (
    ColumnSpec("week_text", "周", width=92),
    ColumnSpec("competition", "赛事", width=118),
    ColumnSpec("round_text", "轮次", width=92, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("home_name", "主队", width=190, stretch=True),
    ColumnSpec("score_text", "比分/未赛", width=104, alignment=Qt.AlignmentFlag.AlignCenter),
    ColumnSpec("away_name", "客队", width=190, stretch=True),
    ColumnSpec("result_text", "结果", width=84, alignment=Qt.AlignmentFlag.AlignCenter),
)

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"


@dataclass(frozen=True)
class _ListRow:
    """列表行视图模型；未赛比赛比分/结果列显示"未赛"，不虚构数据。"""

    match_id: str
    week_text: str
    competition: str
    round_text: str
    home_name: str
    score_text: str
    away_name: str
    result_text: str


def _to_list_row(source: match_queries.MatchRow) -> _ListRow:
    home_goals = source.home_goals
    away_goals = source.away_goals
    if source.is_completed and home_goals is not None and away_goals is not None:
        score_text = f"{home_goals}-{away_goals}"
        if home_goals > away_goals:
            result_text = "主胜"
        elif home_goals == away_goals:
            result_text = "平"
        else:
            result_text = "客胜"
    else:
        score_text = "vs"
        result_text = "未赛"
    return _ListRow(
        match_id=source.match_id,
        week_text=f"第 {source.week_number} 周",
        competition=source.competition,
        round_text=f"第 {source.round_number} 轮",
        home_name=source.home.display_name,
        score_text=score_text,
        away_name=source.away.display_name,
        result_text=result_text,
    )


class MatchCenterPage(EntityPageBase):
    """比赛中心：全高主表 + 赛季 / 赛事 / 周次 / 状态筛选。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._season: Optional[int] = None
        self._rows: List[_ListRow] = []
        self._empty_state: Optional[EmptyState] = None
        super().__init__(context, parent)

    # -- UI 构建（一次构建；内容在 refresh 中重建） ---------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        self._header = PageHeader("比赛中心", navigator=self._context.navigate)
        layout.addWidget(self._header)

        self._filter_bar = FilterBar(on_search_changed=self._on_filters_changed)
        self._season_combo = self._filter_bar.add_combo("赛季", [], "matchesSeasonCombo")
        self._competition_combo = self._filter_bar.add_combo(
            "赛事", [_ALL_COMPETITION, *base.ALL_COMPETITIONS], "matchesCompetitionCombo"
        )
        self._week_combo = self._filter_bar.add_combo("周次", [_ALL_WEEK], "matchesWeekCombo")
        for week in range(_MIN_WEEK, _MAX_WEEK + 1):
            self._week_combo.addItem(f"第 {week} 周", week)
        self._status_combo = self._filter_bar.add_combo(
            "状态", list(_STATUS_OPTIONS), "matchesStatusCombo"
        )
        self._season_combo.currentIndexChanged.connect(self._on_season_changed)
        self._competition_combo.currentIndexChanged.connect(self._on_filters_changed)
        self._week_combo.currentIndexChanged.connect(self._on_filters_changed)
        self._status_combo.currentIndexChanged.connect(self._on_filters_changed)
        self._filter_bar.add_reset()
        layout.addWidget(self._filter_bar)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(_MUTED_STYLE)
        layout.addWidget(self._summary_label)

        self._table = EntityTable(_LIST_COLUMNS, navigator=self._context.navigate)
        # 队徽：主客队名列（UI#5 统一）。
        self._home_crest_delegate = TeamCrestTextDelegate(parent=self._table.view, crest_size=22)
        self._away_crest_delegate = TeamCrestTextDelegate(parent=self._table.view, crest_size=22)
        self._table.view.setItemDelegateForColumn(3, self._home_crest_delegate)
        self._table.view.setItemDelegateForColumn(5, self._away_crest_delegate)
        # 结果列口径：主队视角（主胜/平/客胜）。
        self._table.view.horizontalHeader().setToolTip(
            "结果列为主队视角：主胜=主队胜，平=平局，客胜=客队胜；未赛=比赛尚未进行。"
        )
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self._table)
        self._stack = QStackedWidget()
        self._stack.addWidget(table_page)
        layout.addWidget(self._stack, 1)
        self._show_empty("暂无比赛数据", "当前存档还没有比赛数据。请先初始化存档并推进赛程。")

    # -- 数据刷新 -----------------------------------------------------------

    def refresh(self) -> None:
        """按路由参数与页面状态重建列表（幂等）。"""
        route = self.current_route()
        if route is None or route.name != "matches":
            return
        state = self.stored_state()
        try:
            with base.open_read_connection(self.save_name()) as conn:
                seasons = base.load_seasons(conn)
                if not seasons:
                    self._season = None
                    self._show_empty("存档还没有任何赛季数据", "请先在“存档”页初始化存档并开启一个赛季。")
                    return
                season = route.int_param("season")
                season_valid = season in {entry.season_number for entry in seasons}
                competition = self._resolve_competition(route, state)
                week = self._resolve_week(route, state)
                status_raw = state.get("status")
                status_text = status_raw if status_raw in _STATUS_OPTIONS else _ALL_STATUS
                self._season = season
                self._rebuild_season_combo(seasons, season if season_valid else None)
                self._set_combo_text(self._competition_combo, competition)
                self._set_week_combo(week)
                self._set_combo_text(self._status_combo, status_text)
                self._load_rows(conn, season, competition, week, status_text, season_valid)
        except base.MissingSaveError:
            self._season = None
            self._show_empty(
                "未初始化存档",
                "当前还没有可用的存档数据库。请先在“存档”页新建或选择一个存档。",
            )

    def route_context(self) -> dict:
        if self._season is None:
            return {}
        context: dict = {"season": int(self._season)}
        competition = self._competition_combo.currentText()
        if competition != _ALL_COMPETITION:
            context["competition_name"] = competition
        week = self._current_week()
        if week is not None:
            context["week"] = week
        return context

    # -- 路由参数 / 页面状态的筛选解析 ---------------------------------------

    @staticmethod
    def _resolve_competition(route: Route, state: dict) -> str:
        if "competition" in route.params:
            candidate = route.params.get("competition")
        else:
            candidate = state.get("competition")
        if candidate in base.ALL_COMPETITIONS:
            return str(candidate)
        return _ALL_COMPETITION

    @staticmethod
    def _resolve_week(route: Route, state: dict) -> Optional[int]:
        if "week" in route.params:
            week = route.int_param("week")
        else:
            raw = state.get("week")
            try:
                week = int(raw) if raw is not None else None  # type: ignore[arg-type]
            except (TypeError, ValueError):
                week = None
        if week is not None and not _MIN_WEEK <= week <= _MAX_WEEK:
            return None
        return week

    # -- 行加载与状态保存 ------------------------------------------------------

    def _load_rows(
        self,
        conn,
        season: int,
        competition: str,
        week: Optional[int],
        status_text: str,
        season_valid: bool = True,
    ) -> None:
        self.save_state(
            {
                "season": season,
                "competition": competition,
                "week": week,
                "status": status_text,
            }
        )
        self._summary_label.setText("")
        if not season_valid:
            self._rows = []
            self._show_empty("该赛季不存在", f"存档中不存在第 {season} 赛季。")
            return
        try:
            source_rows = match_queries.list_matches(
                conn,
                int(season),
                competition=None if competition == _ALL_COMPETITION else competition,
                week_number=week,
                status=_STATUS_QUERY.get(status_text),
            )
        except KeyError:
            self._rows = []
            self._show_empty("该赛季不存在", f"存档中不存在第 {season} 赛季。")
            return
        self._rows = [_to_list_row(row) for row in source_rows]
        completed_count = sum(1 for row in source_rows if row.is_completed)
        self._summary_label.setText(
            f"共 {len(source_rows)} 场 · 已赛 {completed_count} · 未赛 {len(source_rows) - completed_count}"
        )
        if not self._rows:
            self._show_empty(
                "没有匹配的比赛",
                "当前筛选条件下没有比赛，请调整赛季、赛事、周次或状态筛选后重试。",
            )
        else:
            self._table.set_rows(self._rows, route_for_row=self._route_for_row)
            self._stack.setCurrentWidget(self._table.parentWidget())

    def _route_for_row(self, row: _ListRow):
        return Route("match", match=row.match_id)

    # -- 筛选交互 -------------------------------------------------------------

    def _on_season_changed(self, index: int) -> None:
        """切赛季 → navigate 新路由（保留当前具体赛事/周次），见模块说明。"""
        data = self._season_combo.itemData(index)
        if data is None or self._season is None:
            return
        season = int(data)
        if season == self._season:
            return
        params = {"season": season}
        competition = self._competition_combo.currentText()
        if competition != _ALL_COMPETITION:
            params["competition"] = competition
        week = self._current_week()
        if week is not None:
            params["week"] = week
        self.navigate(Route("matches", **params))

    def _on_filters_changed(self, *_args) -> None:
        """赛事/周次/状态变化：保存页面状态并重查，不产生历史栈条目。"""
        if self._season is None:
            return
        try:
            with base.open_read_connection(self.save_name()) as conn:
                self._load_rows(
                    conn,
                    self._season,
                    self._competition_combo.currentText(),
                    self._current_week(),
                    self._status_combo.currentText(),
                )
        except base.MissingSaveError:
            self._show_empty(
                "未初始化存档",
                "当前还没有可用的存档数据库。请先在“存档”页新建或选择一个存档。",
            )

    # -- 组合框辅助 ------------------------------------------------------------

    def _rebuild_season_combo(self, seasons, selected: Optional[int]) -> None:
        combo = self._season_combo
        combo.blockSignals(True)
        combo.clear()
        for season in seasons:
            label = f"第 {season.season_number} 赛季"
            if not season.is_completed:
                label += "（进行中）"
            combo.addItem(label, season.season_number)
        index = combo.findData(selected) if selected is not None else -1
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _set_combo_text(self, combo: QComboBox, text: str) -> None:
        combo.blockSignals(True)
        combo.setCurrentIndex(max(0, combo.findText(text)))
        combo.blockSignals(False)

    def _set_week_combo(self, week: Optional[int]) -> None:
        combo = self._week_combo
        combo.blockSignals(True)
        index = 0 if week is None else combo.findData(int(week))
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _current_week(self) -> Optional[int]:
        index = self._week_combo.currentIndex()
        if index <= 0:
            return None
        data = self._week_combo.itemData(index)
        try:
            return int(data)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    # -- 空状态 ----------------------------------------------------------------

    def _show_empty(self, title: str, description: Optional[str] = None) -> None:
        if self._empty_state is not None:
            self._stack.removeWidget(self._empty_state)
            self._empty_state.deleteLater()
        self._empty_state = EmptyState(title, description)
        self._stack.addWidget(self._empty_state)
        self._stack.setCurrentWidget(self._empty_state)
