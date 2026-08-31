"""本周战报（阶段 5 重写，实施方案 §8.7 周报语义）。

Route：``Route("weekly_report", week=<n>)``（week 必填，1..52）。

- 顶部：第 N 周 · 阶段 label + 上一周/下一周按钮（``navigate`` 相邻周；
  1 / 52 边界禁用）；
- 主体：该周全部比赛按赛事分组（一级联赛 / 次级联赛 / 杯赛各赛事 /
  升级附加赛），每组一张完整 ``EntityTable``（主队/比分/客队/结果），
  行激活（双击 / Enter）→ ``match`` 路由；
- 该周无比赛（冬窗/夏窗休赛周）时显示明确空状态。

滚动面归属（§8.2 硬规则）：页面唯一主滚动面是单个外层 ``QScrollArea``；
每组 ``EntityTable`` 按行数完整展开（固定高度，纵向滚动条永不激活），
不存在“小框内滚动”。这对应 §8.2 规则 3 的内容型布局：外层可滚 +
内部表格按内容展开。

数据：``match_queries.list_matches(conn, season, week_number=week)``；
season 取当前赛季（路由无 season 参数）。不虚构数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base, match_queries
from football_simulator.schedule import TOTAL_WEEKS, build_week_calendar
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import ColumnSpec, EmptyState, EntityTable, PageHeader
from football_simulator.ui_v2.components.crest_delegate import TeamCrestTextDelegate
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase

# 联赛赛历固定为 38 轮；build_week_calendar 只使用轮数与固定常量推导 52 周日历，
# 与 initialize_save_state 构建的赛历完全一致（确定性，无随机源）。
_WEEK_CALENDAR = build_week_calendar([[] for _ in range(38)])

# 分组顺序：一级联赛 → 次级联赛 → 杯赛各赛事 → 升级附加赛。
_GROUP_ORDER = (
    base.COMPETITION_PREMIER,
    base.COMPETITION_SECOND,
    base.COMPETITION_WINNERS_CUP,
    base.COMPETITION_CHALLENGE_CUP,
    base.COMPETITION_SUPER_CUP,
    base.COMPETITION_PLAYOFF,
)

_MATCH_COLUMNS = (
    ColumnSpec("home_name", "主队", width=190),
    ColumnSpec("score_text", "比分", width=90, alignment=Qt.AlignCenter),
    ColumnSpec("away_name", "客队", width=190),
    ColumnSpec("result_text", "结果", width=90, alignment=Qt.AlignCenter),
)

# 完整展开高度 = 表头 + 行数 × 行高 + 边框/缓冲（保证纵向滚动条永不激活）。
_ROW_HEIGHT = 50
_TABLE_HEIGHT_BUFFER = 12


@dataclass(frozen=True)
class _WeeklyMatchRow:
    """周报表格行 DTO（``match_id`` 供行激活导航）。"""

    match_id: str
    home_name: str
    away_name: str
    score_text: str
    result_text: str


def _result_text(row: match_queries.MatchRow) -> str:
    if not row.is_completed or row.home_goals is None or row.away_goals is None:
        return "未赛"
    if row.home_goals > row.away_goals:
        return "主胜"
    if row.home_goals < row.away_goals:
        return "客胜"
    return "平"


def _weekly_row(row: match_queries.MatchRow) -> _WeeklyMatchRow:
    return _WeeklyMatchRow(
        match_id=row.match_id,
        home_name=row.home.display_name,
        away_name=row.away.display_name,
        score_text=row.score_display or "vs",
        result_text=_result_text(row),
    )


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class WeeklyReportPage(EntityPageBase):
    """周报：第 N 周全部比赛按赛事分组的完整战报。"""

    def __init__(self, context, parent: Optional[QWidget] = None, *_legacy_args: object) -> None:
        # 外壳在阶段 5 集成前仍用旧签名构造本页（多余位置参数在此被丢弃）。
        if not isinstance(parent, QWidget):
            parent = None
        self._week: int = 1
        self._season: int = 0
        self._phase_text: str = ""
        self._tables: List[EntityTable] = []
        self._empty: Optional[EmptyState] = None
        self._prev_button: Optional[QPushButton] = None
        self._next_button: Optional[QPushButton] = None
        self._summary_label: Optional[QLabel] = None
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；内容在 refresh 中重建） ---------------------------

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(0)

        # 外层 stack 仅用于致命错误（无存档等）：此时不渲染周导航。
        self._stack = QStackedWidget()
        page_layout.addWidget(self._stack, 1)

        main = QWidget()
        self._main = main
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)

        # 页头（第 N 周 · 阶段 + 上一周/下一周）常驻：休赛周空状态下仍可切换周次。
        self._header_slot = QVBoxLayout()
        self._header_slot.setContentsMargins(0, 0, 0, 0)
        main_layout.addLayout(self._header_slot)

        self._phase_label = QLabel("—")
        self._phase_label.setObjectName("weeklyPhaseLabel")
        self._phase_label.setStyleSheet("color: #7dd3fc; background: transparent; font-weight: 800;")
        main_layout.addWidget(self._phase_label)

        # 主体 stack：分组战报（唯一主滚动面）↔ 休赛周空状态。
        self._body_stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("weeklyScroll")
        self._groups_host = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_host)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(12)
        scroll.setWidget(self._groups_host)
        self._scroll = scroll
        self._body_stack.addWidget(scroll)
        self._empty = EmptyState("第 1 周没有比赛", "", None)
        self._body_stack.addWidget(self._empty)
        main_layout.addWidget(self._body_stack, 1)

        self._stack.addWidget(main)
        self._fatal_empty = EmptyState(
            "还没有可用的存档数据",
            "当前存档还没有赛季数据。",
            "请先在顶部选择存档，然后点击“初始化赛季”。",
        )
        self._stack.addWidget(self._fatal_empty)

    def set_snapshot(self, snapshot: object) -> None:
        """外壳 ``_refresh_views`` 的遗留兼容入口（阶段 5 集成后移除）。

        新契约页面不消费快照：数据全部由 ``apply_route``/``refresh`` 按路由
        只读查询得到，这里刻意不做任何事，避免与路由刷新双写。
        """
        del snapshot

    # -- 数据刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "weekly_report":
            return
        week = route.int_param("week") or 1
        week = max(1, min(TOTAL_WEEKS, week))
        self._week = week
        navigate = getattr(self._context, "navigate", None)
        try:
            with base.open_read_connection(self.save_name()) as conn:
                season = base.resolve_current_season(conn).season_number
                matches = match_queries.list_matches(conn, season, week_number=week)
                week_labels = base.load_week_labels(conn)
        except base.MissingSaveError as exc:
            self._show_fatal("还没有可用的存档数据", str(exc), "请先在顶部选择存档，然后点击“初始化赛季”。")
            return
        except Exception as exc:  # 查询层异常统一进空状态
            self._show_fatal("暂时无法加载周报数据", str(exc), None)
            return

        self._season = season
        self._week_labels = week_labels
        if week_labels and 1 <= week <= len(week_labels):
            self._phase_text = week_labels[week - 1]["label"]
        elif 1 <= week <= len(_WEEK_CALENDAR):
            self._phase_text = _WEEK_CALENDAR[week - 1].label
        else:
            self._phase_text = "赛季已结束"
        self._render_header(navigate)
        if matches:
            self._render_groups(matches, navigate)
            self._body_stack.setCurrentWidget(self._scroll)
        else:
            self._clear_groups()
            title, description, hint = self._empty_texts(week)
            self._show_body_empty(title, description, hint)

    def route_context(self) -> dict:
        return {"week": self._week}

    # -- 顶部：页头 + 上一周/下一周 -------------------------------------------

    def _render_header(self, navigate) -> None:
        _clear_layout(self._header_slot)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)

        self._prev_button = QPushButton("← 上一周")
        self._prev_button.setObjectName("weeklyPrevButton")
        self._prev_button.setEnabled(self._week > 1)
        self._prev_button.clicked.connect(self._go_prev_week)
        self._next_button = QPushButton("下一周 →")
        self._next_button.setObjectName("weeklyNextButton")
        self._next_button.setEnabled(self._week < TOTAL_WEEKS)
        self._next_button.clicked.connect(self._go_next_week)
        actions_layout.addWidget(self._prev_button)
        actions_layout.addWidget(self._next_button)

        header = PageHeader(
            f"第 {self._week} 周战报",
            breadcrumbs=[],
            actions=[actions],
        )
        self._header_slot.addWidget(header)
        self._phase_label.setText(f"第 {self._week} 周 · {self._phase_text}")

    def _go_prev_week(self) -> None:
        if self._week > 1:
            self.navigate(Route("weekly_report", week=self._week - 1))

    def _go_next_week(self) -> None:
        if self._week < TOTAL_WEEKS:
            self.navigate(Route("weekly_report", week=self._week + 1))

    # -- 主体：赛事分组完整 EntityTable -----------------------------------------

    def _clear_groups(self) -> None:
        _clear_layout(self._groups_layout)
        self._tables = []

    def _render_groups(self, matches, navigate) -> None:
        _clear_layout(self._groups_layout)
        self._tables = []
        by_competition = {competition: [] for competition in _GROUP_ORDER}
        for row in matches:
            by_competition.setdefault(row.competition, []).append(row)

        total_count = len(matches)
        group_count = 0
        for competition in _GROUP_ORDER:
            rows = by_competition.get(competition) or []
            if not rows:
                continue
            group_count += 1
            self._groups_layout.addWidget(self._build_group(competition, rows, navigate))

        # 摘要行放在分组内容的最上方（每次刷新重建，避免引用已删除控件）。
        summary = QLabel(f"共 {total_count} 场 · {group_count} 项赛事")
        summary.setObjectName("weeklySummaryLabel")
        summary.setStyleSheet("color: #7dd3fc; background: transparent; font-weight: 800;")
        self._summary_label = summary
        self._groups_layout.insertWidget(0, summary)
        self._groups_layout.addStretch(1)

    def _build_group(self, competition: str, rows: List[match_queries.MatchRow], navigate) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        title = QLabel(f"{competition}（{len(rows)} 场）")
        title.setStyleSheet("font-size: 16px; font-weight: 800; background: transparent; color: #f8fbff;")
        layout.addWidget(title)

        table = EntityTable(_MATCH_COLUMNS, navigator=navigate)
        table.view.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        table._home_crest_delegate = TeamCrestTextDelegate(parent=table.view, crest_size=38)
        table._away_crest_delegate = TeamCrestTextDelegate(parent=table.view, crest_size=38)
        table.view.setItemDelegateForColumn(0, table._home_crest_delegate)
        table.view.setItemDelegateForColumn(2, table._away_crest_delegate)
        dtos = [_weekly_row(row) for row in rows]
        table.set_rows(dtos, route_for_row=lambda row: Route("match", match=row.match_id))
        # 完整展开：固定高度 = 表头 + 行数 × 行高 + 缓冲，纵向滚动条永不激活。
        header_height = table.view.horizontalHeader().sizeHint().height()
        table.setFixedHeight(header_height + len(rows) * _ROW_HEIGHT + _TABLE_HEIGHT_BUFFER)
        self._tables.append(table)
        layout.addWidget(table)
        return frame

    # -- 空状态 -----------------------------------------------------------------

    def _empty_texts(self, week: int):
        labels = getattr(self, "_week_labels", ())
        if labels and 1 <= week <= len(labels):
            label = labels[week - 1]["label"]
        else:
            label = _WEEK_CALENDAR[week - 1].label if 1 <= week <= len(_WEEK_CALENDAR) else "休赛期"
        if label in ("冬窗休赛期", "夏窗休赛期"):
            return (
                f"第 {week} 周为休赛周（冬窗/夏窗）",
                f"第 {week} 周（{label}）没有安排任何比赛。",
                "可以使用右上角的“上一周 / 下一周”切换到有比赛的周次。",
            )
        return (
            f"第 {week} 周没有比赛",
            f"第 {week} 周（{label}）没有安排任何比赛。",
            "可以使用右上角的“上一周 / 下一周”切换到有比赛的周次。",
        )

    def _show_body_empty(self, title: str, description: str, hint: Optional[str]) -> None:
        """休赛周/无比赛周空状态：保留页头与上一周/下一周导航。"""
        assert self._empty is not None
        old = self._empty
        replacement = EmptyState(title, description, hint)
        self._body_stack.addWidget(replacement)
        self._body_stack.setCurrentWidget(replacement)
        self._empty = replacement
        if old is not None and old is not replacement:
            self._body_stack.removeWidget(old)
            old.deleteLater()

    def _show_fatal(self, title: str, description: str, hint: Optional[str]) -> None:
        """致命错误（无存档等）：整页替换，不渲染周导航。"""
        assert self._fatal_empty is not None
        old = self._fatal_empty
        replacement = EmptyState(title, description, hint)
        self._stack.addWidget(replacement)
        self._stack.setCurrentWidget(replacement)
        self._fatal_empty = replacement
        if old is not None and old is not replacement:
            self._stack.removeWidget(old)
            old.deleteLater()
