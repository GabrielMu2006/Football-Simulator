"""球队目录页（阶段 4 · 实施方案 §8.6）。

- 全高 EntityTable 主表：40 支球队各一行，附该赛季积分榜行与注册阵容摘要；
- 筛选：分区（全部/一级联赛/次级联赛）+ 队名搜索（FilterBar，300ms 防抖）
  + 页面内赛季下拉（状态经 ``save_state`` 保存，不进入路由参数）；
- 行激活（双击 / Enter）→ ``Route("team", team=<team_id>, season=<所选赛季>)``；
- 当前赛季默认取 ``base.resolve_current_season``。

滚动硬规则（§8.2）：本页唯一纵向滚动面是球队目录 EntityTable，
外层不套 QScrollArea；空结果时整页替换为 EmptyState（无滚动区）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base, team_queries
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    ColumnSpec,
    EmptyState,
    EntityTable,
    FilterBar,
    PageHeader,
    TEXT_COLOR,
    TEXT_COLOR_MUTED,
)
from football_simulator.ui_v2.components.team_crest import draw_team_crest
from football_simulator.ui_v2.pages.entity_page_base import (
    EntityPageBase,
    PageContext,
    PageState,
)

_DIVISION_ALL = "全部"
_DIVISION_OPTIONS = (_DIVISION_ALL, base.COMPETITION_PREMIER, base.COMPETITION_SECOND)

_DIRECTORY_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("team_name", "球队", width=210, stretch=True),
    ColumnSpec("season_division", "分区", width=92),
    ColumnSpec("played", "赛", width=52, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("wins", "胜", width=52, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("draws", "平", width=52, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("losses", "负", width=52, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goals_for", "进球", width=60, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goals_against", "失球", width=60, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("points", "积分", width=64, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("rank", "名次", width=60, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("real_player_count", "真实球员数", width=94, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("roster_total_ability", "阵容总能力", width=96, alignment=Qt.AlignmentFlag.AlignRight),
)


@dataclass(frozen=True)
class _DirectoryRow:
    """目录表行 DTO；属性名与 ``_DIRECTORY_COLUMNS`` 的 ``key`` 一一对应。

    ``rank`` 为 ``None``（该队本赛季尚未赛过）时 EntityTable 显示"—"。
    """

    team_id: int
    team_name: str
    season_division: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int
    rank: Optional[int]
    real_player_count: int
    roster_total_ability: int


class _TeamCrestTextDelegate(QStyledItemDelegate):
    # 球队名列：队徽 + 队名文本，保持基础选中/悬停行为，行激活由表格处理。

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
        crest_size = min(rect.height(), 46)
        crest_rect = QRect(
            rect.left(),
            rect.top() + (rect.height() - crest_size) // 2,
            crest_size,
            crest_size,
        )
        draw_team_crest(painter, crest_rect, text, size=crest_size)
        painter.setPen(QColor(TEXT_COLOR))
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


class TeamsPage(EntityPageBase):
    """球队目录：``Route("teams")``，查询层主数据源为 ``team_queries.list_teams``。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(context, parent)

    # -- UI 骨架 -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._page_header = PageHeader(
            "球队",
            [],
            self._context.navigate,
        )
        season_caption = QLabel("赛季")
        season_caption.setObjectName("seasonCaption")
        season_caption.setStyleSheet(f"color: {TEXT_COLOR_MUTED}; background: transparent;")
        self._season_combo = QComboBox(self)
        self._season_combo.setObjectName("seasonFilter")
        self._season_combo.currentIndexChanged.connect(self._on_filters_changed)
        self._page_header.add_action(season_caption)
        self._page_header.add_action(self._season_combo)
        root.addWidget(self._page_header)

        self._filter_bar = FilterBar(on_search_changed=self._on_filters_changed)
        self._division_combo = self._filter_bar.add_combo(
            "分区", list(_DIVISION_OPTIONS), "divisionFilter"
        )
        self._division_combo.currentIndexChanged.connect(self._on_filters_changed)
        self._search_edit = self._filter_bar.add_search("搜索球队名…")
        self._filter_bar.add_reset()
        root.addWidget(self._filter_bar)

        self._stack = QStackedWidget(self)
        self._table = EntityTable(_DIRECTORY_COLUMNS, navigator=self._context.navigate, parent=self)
        self._crest_delegate = _TeamCrestTextDelegate(parent=self._table.view)
        self._table.view.setItemDelegateForColumn(0, self._crest_delegate)
        self._empty = EmptyState("暂无球队数据", "当前存档还没有球队数据。")
        self._stack.addWidget(self._table)
        self._stack.addWidget(self._empty)
        root.addWidget(self._stack, 1)

    # -- 契约入口 -------------------------------------------------------------

    def refresh(self) -> None:
        """按当前路由与最新存档数据重建目录（幂等）。"""

        season_context = self._load_season_context()
        state = self.stored_state()
        self._rebuild_season_combo(season_context, state.get("season"))
        self._filter_bar.restore(state)
        self._load_rows()
        self._persist_state()

    def route_context(self) -> dict:
        return {}

    # -- 数据装载 -------------------------------------------------------------

    def _selected_season(self) -> Optional[int]:
        data = self._season_combo.currentData()
        return int(data) if data is not None else None

    def _load_season_context(self) -> Optional[Tuple[List[base.SeasonRef], int]]:
        """（赛季列表, 当前赛季号）；存档不可读时返回 ``None``。"""

        def _read(conn: sqlite3.Connection) -> Tuple[List[base.SeasonRef], int]:
            seasons = base.load_seasons(conn)
            current = base.resolve_current_season(conn)
            return seasons, current.season_number

        return self._with_connection(_read)

    def _rebuild_season_combo(
        self,
        season_context: Optional[Tuple[List[base.SeasonRef], int]],
        preferred: object,
    ) -> None:
        """重建赛季下拉：优先恢复保存的赛季，否则落到当前赛季。"""

        self._season_combo.blockSignals(True)
        try:
            self._season_combo.clear()
            seasons: List[base.SeasonRef] = []
            current_number: Optional[int] = None
            if season_context is not None:
                seasons, current_number = season_context
            for season in seasons:
                self._season_combo.addItem(f"第 {season.season_number} 赛季", season.season_number)
            numbers = [season.season_number for season in seasons]
            index = -1
            if isinstance(preferred, int) and preferred in numbers:
                index = numbers.index(preferred)
            elif current_number is not None and current_number in numbers:
                index = numbers.index(current_number)
            if index >= 0:
                self._season_combo.setCurrentIndex(index)
        finally:
            self._season_combo.blockSignals(False)

    def _load_rows(self) -> None:
        season = self._selected_season()
        if season is None:
            self._show_empty("暂无球队数据", "当前存档还没有赛季数据。")
            return
        division = self._division_combo.currentText()
        search = str(self._filter_bar.state().get("search") or "").strip()
        rows = self._with_connection(
            lambda conn: team_queries.list_teams(
                conn,
                season,
                division=None if division == _DIVISION_ALL else division,
                search=search or None,
            )
        )
        if rows is None:
            self._show_empty("暂无球队数据", "存档不可读或还没有球队数据。")
            return
        if not rows:
            self._show_empty(
                "没有匹配的球队",
                "当前分区与搜索条件下没有球队。",
                "调整分区筛选或清空搜索关键词后重试。",
            )
            return
        directory_rows = [
            _DirectoryRow(
                team_id=row.team.team_id,
                team_name=row.team.display_name,
                season_division=row.season_division,
                played=row.played,
                wins=row.wins,
                draws=row.draws,
                losses=row.losses,
                goals_for=row.goals_for,
                goals_against=row.goals_against,
                points=row.points,
                rank=row.rank,
                real_player_count=row.real_player_count,
                roster_total_ability=row.roster_total_ability,
            )
            for row in rows
        ]
        self._table.set_rows(directory_rows, route_for_row=self._team_route_for_row)
        self._stack.setCurrentWidget(self._table)

    def _team_route_for_row(self, row: _DirectoryRow) -> Optional[navigation.Route]:
        season = self._selected_season()
        if season is None:
            return None
        return navigation.Route("team", team=row.team_id, season=season)

    # -- 状态 ------------------------------------------------------------------

    def _persist_state(self) -> None:
        state: PageState = {"season": self._selected_season()}
        state.update(self._filter_bar.state())
        self.save_state(state)

    def _on_filters_changed(self, *_args: object) -> None:
        self._load_rows()
        self._persist_state()

    # -- 空状态 / 连接辅助 -------------------------------------------------------

    def _show_empty(self, title: str, description: str, hint: Optional[str] = None) -> None:
        # 空状态时清空表格，避免旧行残留导致 rowCount 失真。
        self._table.set_rows([], route_for_row=None)
        previous = self._empty
        self._empty = EmptyState(title, description, hint)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentWidget(self._empty)
        if previous is not None:
            self._stack.removeWidget(previous)
            previous.deleteLater()

    def _with_connection(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """只读连接中执行 ``fn``；存档缺失 / 赛季缺失 / SQL 错误时返回 ``None``。"""

        try:
            with base.open_read_connection(self.save_name()) as conn:
                return fn(conn)
        except (base.MissingSaveError, KeyError, sqlite3.Error):
            return None
