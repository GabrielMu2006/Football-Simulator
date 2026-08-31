"""首页（阶段 5 重写，实施方案 §8.3）。

Route：``Route("dashboard")``（无参数）。

目标：让用户一眼知道“现在在哪、接下来做什么、最近发生什么”。

- 顶部状态区：当前赛季、周次、当前阶段（本周 label）、待办数量（按类型）；
- 主区（唯一外层 ``QScrollArea``，区块完整展开，§8.2 滚动硬规则）：
  “接下来的比赛”“最近赛果”“联赛速览”“联赛射手/助攻”“已决出冠军”；
- 待办非零时显示显著提示块，按钮直达转会中心 / 选秀中心 / 赛季总览（能力审核）；
- 未初始化存档时显示 ``EmptyState`` 并引导初始化（外壳已有“初始化赛季”按钮）。

数据全部来自只读查询（``dashboard_queries`` / ``competition_queries``），
不消费快照、不虚构数据。链接遵循 §7.2 全局链接合同：比赛行/比分 → ``match``，
球队 → ``team``，球员 → ``player``，赛事 → ``competition``。

兼容说明：外壳（main_window）在阶段 5 集成前仍以旧签名构造本页面并调用
``set_snapshot``；新契约页面不消费快照（数据全部由 ``apply_route``/``refresh``
按路由只读查询得到），因此构造器容忍旧位置参数、``set_snapshot`` 为显式空操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base, competition_queries, dashboard_queries
from football_simulator.schedule import TOTAL_WEEKS, build_week_calendar
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    TEXT_COLOR_MUTED,
    EntityLink,
    EmptyState,
    PageHeader,
)
from football_simulator.ui_v2.components.team_crest import TeamCrest
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase
from football_simulator.ui_v2.widgets import section_header

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = "color: #f8fbff; background: transparent; font-weight: 700;"
_ACCENT_STYLE = "color: #7dd3fc; background: transparent; font-weight: 800;"

# 联赛赛历固定为 38 轮；build_week_calendar 只使用轮数与固定常量推导 52 周日历，
# 与 initialize_save_state 构建的赛历完全一致（确定性，无随机源）。
_WEEK_CALENDAR = build_week_calendar([[] for _ in range(38)])

# 各区块网格的统一列宽，保证多区块行对齐。
_COLUMN_WIDTHS = (216, 168, 84, 168)


@dataclass(frozen=True)
class _MatchLine:
    """首页比赛行的展示 DTO。"""

    match_id: str
    week_number: int
    competition: str
    round_number: int
    home_id: int
    home_name: str
    away_id: int
    away_name: str
    score_text: str  # 已赛 "2-1"；未赛 "vs"
    status_text: str  # "未赛" / "已赛"


def _match_line(ref: base.MatchRef) -> _MatchLine:
    """``base.MatchRef`` → 首页展示行（未赛比分显示 “vs”）。"""
    return _MatchLine(
        match_id=ref.match_id,
        week_number=ref.week_number,
        competition=ref.competition,
        round_number=ref.round_number,
        home_id=ref.home.team_id,
        home_name=ref.home.display_name,
        away_id=ref.away.team_id,
        away_name=ref.away.display_name,
        score_text=ref.score_display or "vs",
        status_text="已赛" if ref.is_completed else "未赛",
    )


def _phase_label(current_week: int, season_complete: bool, weeks=()) -> str:
    # 当前阶段 = 周指针所指周次（本周）的赛历 label。
    # weeks 优先取存档真实赛历（已按杯赛激活修饰），缺省退回静态日历。
    if season_complete:
        return "赛季已结束"
    if weeks:
        if current_week >= len(weeks):
            return "赛季已结束"
        entry = weeks[current_week]
        return entry["label"] if isinstance(entry, dict) else entry.label
    if current_week >= len(_WEEK_CALENDAR):
        return "赛季已结束"
    return _WEEK_CALENDAR[current_week].label


def _clear_layout(layout) -> None:
    """清空布局中的全部子项（控件标记延迟删除）。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class _MetricCard(QFrame):
    """可点击的仪表卡：鼠标点击触发导航回调。"""

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._callback = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_callback(self, callback) -> None:
        self._callback = callback

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            if self._callback is not None:
                self._callback()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _metric_card(key: str, title: str, note: str) -> _MetricCard:
    frame = _MetricCard()
    frame.setObjectName("cardFrame")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(2)
    title_label = QLabel(title)
    title_label.setStyleSheet(f"font-size: 12px; font-weight: 700; {_MUTED_STYLE}")
    value_label = QLabel("—")
    value_label.setStyleSheet("font-size: 20px; font-weight: 800; background: transparent; color: #f8fbff;")
    note_label = QLabel(note)
    note_label.setStyleSheet(f"font-size: 11px; {_MUTED_STYLE}")
    note_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    layout.addWidget(note_label)
    frame.value_label = value_label  # type: ignore[attr-defined]
    frame._metric_key = key  # type: ignore[attr-defined]
    return frame


class DashboardPage(EntityPageBase):
    """首页：状态区 + 待办提示 + 赛程速览 + 联赛摘要 + 榜单 + 冠军。"""

    def __init__(self, context, parent: Optional[QWidget] = None, *_legacy_args: object) -> None:
        # 外壳在阶段 5 集成前仍用旧签名构造本页（多余位置参数在此被丢弃）。
        if not isinstance(parent, QWidget):
            parent = None
        self._status_labels: Dict[str, QLabel] = {}
        self._pending_frame: Optional[QFrame] = None
        self._pending_body: Optional[QVBoxLayout] = None
        self._pending_buttons: Dict[str, QPushButton] = {}
        self._upcoming_links: List[EntityLink] = []
        self._latest_links: List[EntityLink] = []
        self._league_rows: List[QWidget] = []
        self._leader_links: List[EntityLink] = []
        self._champion_links: List[EntityLink] = []
        self._sections_layout: Optional[QVBoxLayout] = None
        self._empty: Optional[EmptyState] = None
        # “只显示真实球员”应用于联赛射手/助攻榜；页面实例常驻，跨刷新保持。
        self._leader_real_only = False
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；内容在 refresh 中重建） ---------------------------

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(0)

        self._status_cards: Dict[str, _MetricCard] = {}
        self._stack = QStackedWidget()
        page_layout.addWidget(self._stack, 1)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        main_layout.addWidget(PageHeader("主页", breadcrumbs=[]))

        # -- 顶部状态区（静态骨架，refresh 时改数值） ------------------------
        status_holder = QWidget()
        status_grid = QGridLayout(status_holder)
        status_grid.setContentsMargins(0, 0, 0, 0)
        status_grid.setSpacing(12)
        cards = (
            ("season", "当前赛季", "当前进行中的赛季"),
            ("week", "当前周次", "已模拟的周次 / 共 52 周"),
            ("phase", "当前阶段", "本周在赛历中的阶段"),
            ("pending", "待办数量", "能力审核、转会审核、选秀汇总"),
        )
        for index, (key, title, note) in enumerate(cards):
            card = _metric_card(key, title, note)
            status_grid.addWidget(card, 0, index)
            self._status_labels[key] = card.value_label  # type: ignore[attr-defined]
            self._status_cards[key] = card
        for column in range(len(cards)):
            status_grid.setColumnStretch(column, 1)
        main_layout.addWidget(status_holder)

        # -- 待办提示块（待办非零时显示） ------------------------------------
        self._pending_frame = QFrame()
        self._pending_frame.setObjectName("dashboardPendingBlock")
        pending_layout = QVBoxLayout(self._pending_frame)
        pending_layout.setContentsMargins(12, 10, 12, 10)
        pending_layout.setSpacing(8)
        pending_title = QLabel("待办提醒")
        pending_title.setStyleSheet(_ACCENT_STYLE)
        pending_layout.addWidget(pending_title)
        self._pending_body = QVBoxLayout()
        self._pending_body.setContentsMargins(0, 0, 0, 0)
        self._pending_body.setSpacing(6)
        pending_layout.addLayout(self._pending_body)
        self._pending_frame.setVisible(False)
        main_layout.addWidget(self._pending_frame)

        # -- 主区：唯一外层纵向滚动面（§8.2），区块完整展开 -------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("dashboardScroll")
        content = QWidget()
        self._sections_layout = QVBoxLayout(content)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(12)
        scroll.setWidget(content)
        self._scroll = scroll
        main_layout.addWidget(scroll, 1)

        self._main = main
        self._stack.addWidget(main)
        self._empty = EmptyState(
            "还没有可用的存档数据",
            "当前存档还没有赛季数据。",
            "请先在顶部选择存档，然后点击“初始化赛季”创建第 1 赛季。",
        )
        self._stack.addWidget(self._empty)

    # -- 外壳遗留兼容 ---------------------------------------------------------

    def set_snapshot(self, snapshot: object) -> None:
        """外壳 ``_refresh_views`` 的遗留兼容入口（阶段 5 集成后移除）。

        新契约页面不消费快照：数据全部由 ``apply_route``/``refresh`` 按路由
        只读查询得到，这里刻意不做任何事，避免与路由刷新双写。
        """
        del snapshot

    # -- 数据刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is not None and route.name != "dashboard":
            return
        navigate = getattr(self._context, "navigate", None)
        try:
            with base.open_read_connection(self.save_name()) as conn:
                snapshot = dashboard_queries.get_dashboard(
                    conn,
                    leaderboards_is_real=True if self._leader_real_only else None,
                )
                week_labels = base.load_week_labels(conn)
                season_id = base.season_id_for(conn, snapshot.current_season)
                standings = {
                    category: competition_queries.league_standings_rows(
                        conn, season_id, snapshot.current_season, category
                    )
                    for category in ("premier", "second")
                }
                team_ids = {str(row[0]): int(row[1]) for row in conn.execute("SELECT name, team_id FROM teams")}
        except base.MissingSaveError as exc:
            self._show_empty("还没有可用的存档数据", str(exc), "请先在顶部选择存档，然后点击“初始化赛季”。")
            return
        except Exception as exc:  # 查询层异常统一进空状态，避免整页崩溃
            self._show_empty("暂时无法加载首页数据", str(exc), None)
            return

        self._upcoming_links = []
        self._latest_links = []
        self._upcoming_data: List[_MatchLine] = []
        self._latest_data: List[_MatchLine] = []
        self._league_rows = []
        self._leader_links = []
        self._champion_links = []
        self._pending_buttons = {}

        pending = snapshot.pending_counts
        pending_total = pending.ability_review + pending.transfer_review + pending.draft
        season = snapshot.current_season

        self._status_labels["season"].setText(f"第 {season} 赛季")
        self._status_labels["week"].setText(f"第 {snapshot.current_week} / {TOTAL_WEEKS} 周")
        self._status_labels["phase"].setText(
            _phase_label(snapshot.current_week, snapshot.season_complete, week_labels)
        )
        self._status_labels["pending"].setText(
            str(pending_total) if pending_total else "0"
        )
        self._render_pending_block(snapshot, season, pending_total)
        self._bind_status_card_navigation(snapshot, season, pending_total, pending, navigate)
        self._render_sections(snapshot, standings, team_ids, navigate)
        self._stack.setCurrentWidget(self._main)

    def route_context(self) -> dict:
        return {}

    # -- 待办提示块 -----------------------------------------------------------

    def _render_pending_block(self, snapshot, season: int, pending_total: int) -> None:
        assert self._pending_body is not None and self._pending_frame is not None
        _clear_layout(self._pending_body)
        self._pending_buttons = {}
        self._pending_frame.setVisible(pending_total > 0)
        if pending_total == 0:
            return
        pending = snapshot.pending_counts
        entries = (
            (
                "ability_review",
                f"能力变动审核 {pending.ability_review} 项等待处理（赛季末能力调整）",
                "前往赛季总览审核",
                Route("season_overview", season=season),
                "pendingAbilityButton",
            ),
            (
                "transfer_review",
                f"转会审核 {pending.transfer_review} 笔等待处理",
                "前往转会中心",
                Route("transfers", season=season),
                "pendingTransferButton",
            ),
            (
                "draft",
                "选秀录入等待处理",
                "前往选秀中心",
                Route("draft", season=season),
                "pendingDraftButton",
            ),
        )
        navigate = getattr(self._context, "navigate", None)
        for key, text, button_text, route, object_name in entries:
            count = getattr(pending, key)
            if not count:
                continue
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            label = QLabel(text)
            label.setStyleSheet(_BRIGHT_STYLE)
            row_layout.addWidget(label, 1)
            button = QPushButton(button_text)
            button.setObjectName(object_name)
            button.clicked.connect(lambda _=False, target=route: navigate(target) if navigate else None)
            row_layout.addWidget(button)
            self._pending_buttons[key] = button
            self._pending_body.addWidget(row)

    # -- 主区区块 -------------------------------------------------------------

    def _bind_status_card_navigation(self, snapshot, season: int, pending_total: int, pending, navigate) -> None:
        if navigate is None:
            return

        def _goto(route: Route) -> None:
            navigate(route)

        self._status_cards["season"].set_callback(
            lambda: _goto(Route("season_overview", season=season))
        )
        week_route = Route("weekly_report", week=snapshot.current_week)
        self._status_cards["week"].set_callback(lambda: _goto(week_route))
        self._status_cards["phase"].set_callback(lambda: _goto(week_route))

        pending_route = None
        if pending_total:
            if pending.ability_review:
                pending_route = Route("season_overview", season=season)
            elif pending.transfer_review:
                pending_route = Route("transfers", season=season)
            elif pending.draft:
                pending_route = Route("draft", season=season)
        if pending_route is not None:
            self._status_cards["pending"].set_callback(lambda r=pending_route: _goto(r))
        else:
            self._status_cards["pending"].set_callback(None)

    def _render_sections(self, snapshot, standings, team_ids, navigate) -> None:
        assert self._sections_layout is not None
        _clear_layout(self._sections_layout)
        season = snapshot.current_season

        # “接下来”：未赛比赛（行 → 赛前页）
        upcoming = [_match_line(ref) for ref in snapshot.upcoming_matches]
        self._upcoming_data = upcoming
        frame, grid = self._section_frame(
            "接下来的比赛",
            "未来 8 场；单击周次/赛事或“vs”打开赛前页，单击球队名打开球队页。",
        )
        self._fill_match_grid(grid, upcoming, season, navigate, self._upcoming_links, empty_text="当前没有已排期的比赛。")
        self._sections_layout.addWidget(frame)

        # “最近赛果”：已完成比赛（行 → 赛后报告）
        latest = [_match_line(ref) for ref in snapshot.latest_results]
        self._latest_data = latest
        frame, grid = self._section_frame(
            "最近赛果",
            "最近 8 场已完成比赛；单击比分打开赛后报告。",
        )
        self._fill_match_grid(grid, latest, season, navigate, self._latest_links, empty_text="本赛季还没有已完成的比赛。")
        self._sections_layout.addWidget(frame)

        # “联赛速览”：两个联赛榜首摘要 + 进入赛事
        frame, grid = self._section_frame(
            "联赛速览",
            "两个联赛的榜首摘要；单击球队名打开球队页，单击“进入赛事”打开赛事页。",
        )
        self._fill_league_grid(grid, standings, season, navigate)
        self._sections_layout.addWidget(frame)

        # “联赛射手/助攻”：league_leaders 榜首
        frame, grid = self._section_frame(
            "联赛射手 / 助攻",
            "各联赛当前榜首球员（前 3 名中的第 1 名）；单击球员名打开球员页。",
            action=self._make_leader_real_check(),
        )
        self._fill_leader_grid(grid, snapshot.league_leaders, season, navigate)
        self._sections_layout.addWidget(frame)

        # “已决出冠军”
        frame, grid = self._section_frame(
            "已决出冠军",
            "当前赛季已经决出冠军的杯赛；单击赛事或球队名打开对应页面。",
        )
        self._fill_champion_grid(grid, snapshot.cup_champions_so_far, team_ids, season, navigate)
        self._sections_layout.addWidget(frame)

        self._sections_layout.addStretch(1)

    def _section_frame(self, title: str, note: str, action: Optional[QWidget] = None):
        frame = QFrame()
        frame.setObjectName("cardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(section_header(title, note))
        if action is not None:
            layout.addWidget(action)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        layout.addLayout(grid)
        return frame, grid

    @staticmethod
    def _grid_header(grid: QGridLayout, titles) -> None:
        for column, title in enumerate(titles):
            label = QLabel(title)
            label.setStyleSheet(f"font-size: 12px; font-weight: 700; {_MUTED_STYLE}")
            if column < len(_COLUMN_WIDTHS):
                label.setFixedWidth(_COLUMN_WIDTHS[column])
            if column == 2:
                label.setAlignment(Qt.AlignCenter)
            grid.addWidget(label, 0, column)
        grid.setColumnStretch(len(titles), 1)

    def _fill_match_grid(self, grid: QGridLayout, rows: List[_MatchLine], season: int, navigate, sink: List[EntityLink], empty_text: str) -> None:
        self._grid_header(grid, ("周次 / 赛事 / 轮次", "主队", "比分", "客队"))
        if not rows:
            placeholder = QLabel(empty_text)
            placeholder.setStyleSheet(_MUTED_STYLE)
            grid.addWidget(placeholder, 1, 0, 1, 4)
            return
        for row_index, row in enumerate(rows, start=1):
            match_link = EntityLink(
                f"第 {row.week_number} 周 · {row.competition} 第 {row.round_number} 轮",
                Route("match", match=row.match_id),
                navigate,
            )
            match_link.setFixedWidth(_COLUMN_WIDTHS[0])
            grid.addWidget(match_link, row_index, 0)
            sink.append(match_link)

            home_link = EntityLink(
                row.home_name,
                Route("team", team=row.home_id, season=season),
                navigate,
            )
            home_holder = QWidget()
            home_layout = QHBoxLayout(home_holder)
            home_layout.setContentsMargins(0, 0, 0, 0)
            home_layout.setSpacing(6)
            home_layout.addWidget(TeamCrest(row.home_name, size=24), 0)
            home_layout.addWidget(home_link, 1)
            grid.addWidget(home_holder, row_index, 1)

            score_link = EntityLink(row.score_text, Route("match", match=row.match_id), navigate)
            score_link.setFixedWidth(_COLUMN_WIDTHS[2])
            score_link.setAlignment(Qt.AlignCenter)
            score_link.setStyleSheet(score_link.styleSheet() + "font-weight: 800;")
            grid.addWidget(score_link, row_index, 2)
            sink.append(score_link)

            away_link = EntityLink(
                row.away_name,
                Route("team", team=row.away_id, season=season),
                navigate,
            )
            away_holder = QWidget()
            away_layout = QHBoxLayout(away_holder)
            away_layout.setContentsMargins(0, 0, 0, 0)
            away_layout.setSpacing(6)
            away_layout.addWidget(TeamCrest(row.away_name, size=24), 0)
            away_layout.addWidget(away_link, 1)
            grid.addWidget(away_holder, row_index, 3)

            status_label = QLabel(row.status_text)
            status_label.setStyleSheet(_MUTED_STYLE)
            grid.addWidget(status_label, row_index, 4)

    def _fill_league_grid(self, grid: QGridLayout, standings, season: int, navigate) -> None:
        self._grid_header(grid, ("赛事", "榜首球队", "积分", "赛况"))
        for row_index, (competition, rows) in enumerate(
            (
                (base.COMPETITION_PREMIER, standings["premier"]),
                (base.COMPETITION_SECOND, standings["second"]),
            ),
            start=1,
        ):
            competition_link = EntityLink(
                competition,
                Route("competition", competition=competition, season=season),
                navigate,
            )
            competition_link.setFixedWidth(_COLUMN_WIDTHS[0])
            grid.addWidget(competition_link, row_index, 0)
            self._league_rows.append(competition_link)

            top = rows[0] if rows else None
            if top is None:
                grid.addWidget(self._muted_placeholder("暂无数据"), row_index, 1, 1, 3)
                continue
            team_link = EntityLink(
                top.team_name,
                Route("team", team=top.team_id, season=season),
                navigate,
            )
            team_link.setFixedWidth(_COLUMN_WIDTHS[1])
            grid.addWidget(team_link, row_index, 1)
            self._league_rows.append(team_link)
            points_label = QLabel(f"{top.points} 分" if top.played > 0 else "暂未开赛")
            points_label.setStyleSheet(_BRIGHT_STYLE)
            points_label.setFixedWidth(_COLUMN_WIDTHS[2])
            points_label.setAlignment(Qt.AlignCenter)
            grid.addWidget(points_label, row_index, 2)
            detail = QLabel(f"已赛 {top.played} 场" if top.played else "第 1 轮尚未开始")
            detail.setStyleSheet(_MUTED_STYLE)
            grid.addWidget(detail, row_index, 3)
            enter_link = EntityLink(
                "进入赛事",
                Route("competition", competition=competition, season=season),
                navigate,
            )
            grid.addWidget(enter_link, row_index, 4)
            self._league_rows.append(enter_link)

    def _fill_leader_grid(self, grid: QGridLayout, league_leaders, season: int, navigate) -> None:
        self._grid_header(grid, ("赛事", "射手榜首", "进球", "助攻榜首", "助攻"))
        row_index = 0
        for leaders in league_leaders:
            row_index += 1
            competition_link = EntityLink(
                leaders.competition.display_name,
                Route("competition", competition=leaders.competition.competition_id, season=season),
                navigate,
            )
            competition_link.setFixedWidth(_COLUMN_WIDTHS[0])
            grid.addWidget(competition_link, row_index, 0)
            self._leader_links.append(competition_link)

            scorer = leaders.top_scorers[0] if leaders.top_scorers else None
            assister = leaders.assist_leaders[0] if leaders.assist_leaders else None
            if scorer is None and assister is None:
                grid.addWidget(self._muted_placeholder("本赛季还没有球员统计数据"), row_index, 1, 1, 4)
                continue
            for column, entry, unit in ((1, scorer, "球"), (3, assister, "次")):
                if entry is None:
                    grid.addWidget(self._muted_placeholder("暂无"), row_index, column)
                    continue
                link = EntityLink(
                    entry.player.display_name,
                    Route("player", player=entry.player.player_id, season=season),
                    navigate,
                )
                link.setFixedWidth(_COLUMN_WIDTHS[column])
                grid.addWidget(link, row_index, column)
                self._leader_links.append(link)
                value_label = QLabel(f"{entry.value} {unit}")
                value_label.setStyleSheet(_BRIGHT_STYLE)
                value_label.setFixedWidth(_COLUMN_WIDTHS[2])
                value_label.setAlignment(Qt.AlignCenter)
                grid.addWidget(value_label, row_index, column + 1)

    def _make_leader_real_check(self) -> QCheckBox:
        # 联赛射手/助攻榜"只显示真实球员"：每次刷新新建（随区块重建），
        # 勾选状态保存在页面实例 _leader_real_only，跨刷新/路由保持。
        check = QCheckBox("只显示真实球员")
        check.setObjectName("dashboardLeaderRealOnlyCheck")
        check.setToolTip("联赛射手/助攻榜只显示真实球员（隐藏默认球员）")
        check.setChecked(self._leader_real_only)
        check.toggled.connect(self._on_leader_real_only_toggled)
        return check

    def _on_leader_real_only_toggled(self, checked: bool) -> None:
        self._leader_real_only = bool(checked)
        self.refresh()

    def _fill_champion_grid(self, grid: QGridLayout, champions, team_ids, season: int, navigate) -> None:
        self._grid_header(grid, ("赛事", "冠军"))
        if not champions:
            placeholder = QLabel("本赛季还没有决出冠军。")
            placeholder.setStyleSheet(_MUTED_STYLE)
            grid.addWidget(placeholder, 1, 0, 1, 2)
            return
        for row_index, line in enumerate(champions, start=1):
            competition_link = EntityLink(
                line.competition.display_name,
                Route("competition", competition=line.competition.competition_id, season=season),
                navigate,
            )
            competition_link.setFixedWidth(_COLUMN_WIDTHS[0])
            grid.addWidget(competition_link, row_index, 0)
            team_id = team_ids.get(line.champion)
            if team_id is not None:
                champion: QWidget = EntityLink(
                    line.champion,
                    Route("team", team=team_id, season=season),
                    navigate,
                )
                self._champion_links.append(champion)
            else:
                label = QLabel(line.champion)
                label.setStyleSheet(_BRIGHT_STYLE)
                champion = label
            grid.addWidget(champion, row_index, 1)

    # -- 空状态与杂项 -----------------------------------------------------------

    def _show_empty(self, title: str, description: str, hint: Optional[str]) -> None:
        assert self._empty is not None
        old = self._empty
        replacement = EmptyState(title, description, hint)
        self._stack.addWidget(replacement)
        self._stack.setCurrentWidget(replacement)
        self._empty = replacement
        if old is not None and old is not replacement:
            self._stack.removeWidget(old)
            old.deleteLater()

    @staticmethod
    def _muted_placeholder(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(_MUTED_STYLE)
        return label
