"""球员目录页（阶段 4，实施方案 §8.5）。

布局与滚动（§8.2 硬规则）：
- 全高 ``EntityTable`` 主表是本页唯一纵向滚动面，占满剩余高度；外层不套
  ``QScrollArea``。页头与筛选条固定在表格上方，行激活（双击 / Enter）进入
  球员个人页 ``Route("player", player=<id>, season=<所选赛季>)``。

位置相关主数据列的实现选择（任务规格二选一中的前者）：
- 表格同时提供 进球/助攻 与 成功扑救/零封 两组列；门将行在 进球/助攻 列
  显示占位符 “—”，非门将行在 成功扑救/零封 列显示 “—”。相比合并成一列
  “主要数据”，该方案保留可排序的数值列，也不隐藏任何引擎真实字段。

赛季选择：``players`` 路由没有 season 参数，目录把所选赛季保存在页面状态
（``save_state``）里，刷新时恢复；切换赛季只刷新数据，不产生历史栈条目。

身价列：引擎只对真实球员做冬窗/赛季末身价结算，默认球员没有身价数据，
单元格显示占位符 “—”（列头 tooltip 说明口径）；个人页头部与轨迹页签会
给出明确的解释性空状态文案，不伪造数值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base
from football_simulator.queries import player_queries
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.components import (
    TEXT_COLOR_MUTED,
    ColumnSpec,
    EmptyState,
    EntityTable,
    FilterBar,
    PageHeader,
)
from football_simulator.ui_v2.components.crest_delegate import TeamCrestTextDelegate
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext

_POSITION_OPTIONS = ("全部位置", "GK", "DF", "MF", "FW")

_DIRECTORY_COLUMNS = (
    ColumnSpec("display_name", "球员", width=190),
    ColumnSpec("position", "位置", width=60, alignment=Qt.AlignCenter),
    ColumnSpec("team_name", "球队", width=170, stretch=True),
    ColumnSpec("ability", "能力", alignment=Qt.AlignRight),
    ColumnSpec("appeared", "本季出场", alignment=Qt.AlignRight),
    ColumnSpec("goals", "进球", alignment=Qt.AlignRight),
    ColumnSpec("assists", "助攻", alignment=Qt.AlignRight),
    ColumnSpec("successful_saves", "成功扑救", alignment=Qt.AlignRight),
    ColumnSpec("clean_sheets", "零封", alignment=Qt.AlignRight),
    ColumnSpec("rating", "评分", alignment=Qt.AlignRight),
    ColumnSpec("market_value", "身价", alignment=Qt.AlignRight),
)


class _FormattedNumber(float):
    """带显示格式的数值：参与数值排序，显示时使用格式化文本。"""

    def __new__(cls, value: float, template: str = "{:.2f}"):
        instance = super().__new__(cls, value)
        instance._template = template  # type: ignore[attr-defined]
        return instance

    def __str__(self) -> str:  # noqa: D105 - Qt DisplayRole 走 str()
        return self._template.format(float(self))  # type: ignore[attr-defined]


@dataclass(frozen=True)
class _DirectoryRow:
    """目录行视图模型：按位置折叠与位置无关的统计列（None → “—”）。"""

    player_id: str
    display_name: str
    position: str
    team_name: str
    ability: int
    appeared: int
    goals: Optional[int]
    assists: Optional[int]
    successful_saves: Optional[int]
    clean_sheets: Optional[int]
    rating: Optional[float]
    market_value: Optional[float]


def _to_directory_row(source: player_queries.PlayerDirectoryRow) -> _DirectoryRow:
    is_goalkeeper = source.position == "GK"
    return _DirectoryRow(
        player_id=source.player_id,
        display_name=source.display_name,
        position=source.position,
        team_name=source.team.display_name,
        ability=source.ability,
        appeared=source.appeared,
        goals=None if is_goalkeeper else source.goals,
        assists=None if is_goalkeeper else source.assists,
        successful_saves=None if not is_goalkeeper else source.successful_saves,
        clean_sheets=None if not is_goalkeeper else source.clean_sheets,
        rating=None if source.rating is None else _FormattedNumber(source.rating),
        market_value=(
            None
            if source.market_value is None
            else _FormattedNumber(source.market_value, "{:.2f}M")
        ),
    )


class PlayersPage(EntityPageBase):
    """球员目录：全高主表 + 搜索 / 位置 / 球队 / 赛季筛选。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._season: Optional[int] = None
        self._team_id_by_name: Dict[str, int] = {}
        self._rows: List[_DirectoryRow] = []
        super().__init__(context, parent)

    # -- UI 构建 ------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        self._header = PageHeader("球员", navigator=self._context.navigate)
        header_row.addWidget(self._header, 1)
        self._season_combo = QComboBox()
        self._season_combo.setObjectName("playersSeasonCombo")
        season_caption = QLabel("赛季")
        season_caption.setStyleSheet(f"color: {TEXT_COLOR_MUTED}; background: transparent;")
        season_box = QWidget()
        season_layout = QHBoxLayout(season_box)
        season_layout.setContentsMargins(0, 0, 0, 0)
        season_layout.setSpacing(6)
        season_layout.addWidget(season_caption)
        season_layout.addWidget(self._season_combo)
        header_row.addWidget(season_box)
        layout.addLayout(header_row)

        self._filter_bar = FilterBar(on_search_changed=self._on_filters_changed)
        self._search_edit = self._filter_bar.add_search("搜索球员名")
        self._position_combo = self._filter_bar.add_combo(
            "位置", list(_POSITION_OPTIONS), "playersPositionCombo"
        )
        self._position_combo.currentIndexChanged.connect(self._on_filters_changed)
        self._team_combo = self._filter_bar.add_combo("球队", ["全部球队"], "playersTeamCombo")
        self._real_only_check = self._filter_bar.add_check("只显示真实球员", "playersRealOnlyCheck")
        self._real_only_check.toggled.connect(self._on_filters_changed)
        self._team_combo.currentIndexChanged.connect(self._on_filters_changed)
        self._filter_bar.add_reset()
        # 引擎只对真实球员结算身价；列头说明口径，避免逐行重复长文案。
        self._table = EntityTable(_DIRECTORY_COLUMNS, navigator=self._context.navigate)
        # 球队列队徽（UI#5 统一）。
        self._team_crest_delegate = TeamCrestTextDelegate(parent=self._table.view, crest_size=38)
        self._table.view.setItemDelegateForColumn(2, self._team_crest_delegate)
        self._table.view.horizontalHeader().setToolTip(
            "身价仅真实球员在冬窗/赛季末结算产生；默认球员不参与身价结算。"
        )
        layout.addWidget(self._filter_bar)

        self._stack = QStackedWidget()
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self._table)
        self._stack.addWidget(table_page)
        self._empty_state: Optional[EmptyState] = None
        self._show_empty("暂无球员数据", "当前存档还没有球员数据。")
        layout.addWidget(self._stack, 1)

        self._season_combo.currentIndexChanged.connect(self._on_season_changed)

    # -- 数据刷新 -----------------------------------------------------------

    def refresh(self) -> None:
        """按页面状态恢复筛选/赛季，并重建目录数据（幂等）。"""
        state = self.stored_state()
        try:
            with base.open_read_connection(self.save_name()) as conn:
                seasons = base.load_seasons(conn)
                if not seasons:
                    self._show_empty("存档还没有任何赛季数据", "请先初始化存档并开启一个赛季。")
                    return
                current_season = base.resolve_current_season(conn).season_number
                season_numbers = {season.season_number for season in seasons}
                stored_season = state.get("season")
                try:
                    season = int(stored_season)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    season = None
                if season is None or season not in season_numbers:
                    season = current_season
                self._season = season

                self._team_id_by_name = {
                    team.display_name: team.team_id for team in base.load_team_refs(conn)
                }
                self._rebuild_season_combo(seasons, season)
                self._rebuild_team_combo()
                # 组合框就绪后再恢复筛选文本/选项（restore 会抑制信号）。
                filters = state.get("filters")
                self._filter_bar.restore(filters if isinstance(filters, dict) else None)
                self._load_rows(conn, season)
        except base.MissingSaveError:
            self._season = None
            self._show_empty(
                "未初始化存档",
                "当前还没有可用的存档数据库。请先在“存档”页新建或选择一个存档。",
            )

    def _rebuild_season_combo(self, seasons, selected: int) -> None:
        combo = self._season_combo
        combo.blockSignals(True)
        combo.clear()
        for season in seasons:
            label = f"第 {season.season_number} 赛季"
            if not season.is_completed:
                label += "（进行中）"
            combo.addItem(label, season.season_number)
        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _rebuild_team_combo(self) -> None:
        combo = self._team_combo
        previous = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("全部球队")
        combo.addItems(sorted(self._team_id_by_name))
        if previous in self._team_id_by_name:
            combo.setCurrentText(previous)
        combo.blockSignals(False)

    def _current_filters(self):
        search = self._search_edit.text().strip() or None
        position = self._position_combo.currentText()
        position = position if position in ("GK", "DF", "MF", "FW") else None
        team_name = self._team_combo.currentText()
        team_id = self._team_id_by_name.get(team_name) if team_name in self._team_id_by_name else None
        return search, position, team_id

    def _load_rows(self, conn, season: int) -> None:
        search, position, team_id = self._current_filters()
        is_real = True if self._real_only_check.isChecked() else None
        try:
            source_rows = player_queries.list_players(
                conn,
                season,
                search=search,
                position=position,
                team_id=team_id,
                is_real=is_real,
            )
        except KeyError:
            self._season = None
            self._show_empty("该赛季不存在", f"存档中不存在第 {season} 赛季。")
            return
        self._rows = [_to_directory_row(source) for source in source_rows]
        if not self._rows:
            self._show_empty("没有匹配的球员", "当前筛选条件下没有球员，请调整搜索或筛选后重试。")
        else:
            self._table.set_rows(self._rows, route_for_row=self._route_for_row)
            self._stack.setCurrentWidget(self._table.parentWidget())
        self._save_state()

    # -- 交互 ---------------------------------------------------------------

    def _route_for_row(self, row: _DirectoryRow):
        return Route("player", player=row.player_id, season=int(self._season or 0))

    def _on_filters_changed(self, *_args) -> None:
        self._reload_rows()

    def _on_season_changed(self, index: int) -> None:
        data = self._season_combo.itemData(index)
        if data is None:
            return
        season = int(data)
        if season == self._season:
            return
        self._season = season
        self._reload_rows()

    def _reload_rows(self) -> None:
        if self._season is None:
            return
        try:
            with base.open_read_connection(self.save_name()) as conn:
                self._load_rows(conn, self._season)
        except base.MissingSaveError:
            self._show_empty(
                "未初始化存档",
                "当前还没有可用的存档数据库。请先在“存档”页新建或选择一个存档。",
            )

    def _save_state(self) -> None:
        self.save_state({"season": self._season, "filters": self._filter_bar.state()})

    def _show_empty(self, title: str, description: Optional[str] = None) -> None:
        if self._empty_state is not None:
            self._stack.removeWidget(self._empty_state)
            self._empty_state.deleteLater()
        self._empty_state = EmptyState(title, description)
        self._stack.addWidget(self._empty_state)
        self._stack.setCurrentWidget(self._empty_state)
