"""首页 / 周报 / 赛季总览页测试（阶段 5，Agent F2）。

共享夹具（参照 tests/test_page_teams.py 的共享存档模式，固定随机源）：
- 存档 A（dash_a）：初始化后推进 4 周（有比赛周、无待办）；
- 存档 B（dash_b）：推进到第 25 周（冬窗休赛周 → 转会待办）；
- 存档 C（dash_c）：完整跑完第 1 赛季并开启第 2 赛季推进 3 周
  （第 1 赛季归档 → 冠军区；多赛季 → 赛季选择器）；
- 存档 D（dash_d）：推进到第 49 周（赛季末结算 → 能力审核 + 选秀待办，
  转会待办已由 support.advance_week 内的 resolve_pending 自动处理）。

覆盖内容：
- 首页（§8.3）：状态区数值、“接下来/最近赛果”行数与链接路由、
  待办提示块（转会/选秀/能力审核按钮路由）、空待办隐藏；
- 周报（§8.7）：第 3 周分组行数 == list_matches(week=3)、行激活 → match、
  第 25 周休赛周空状态文案、上一周/下一周导航与边界禁用；
- 赛季总览（§8.4 + 写流程）：52 周时间线与状态、能力审核表行数 == pending 数、
  默认“保留”、提交全部“采纳”后 pending 清空且球员池能力 == new_ability、
  转会/选秀待办链接路由、赛季选择器、归档冠军、进行中榜首摘要；
- 滚动硬规则（§8.2）：1440×860 与 1680×980 两种尺寸 × 关键状态零嵌套滚动；
- 截图输出到 Reviews/ui_audit/phase4/（dashboard_* / weekly_* / season_*）。

运行：QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_page_dashboard_weekly_season -v
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractScrollArea,
        QApplication,
        QCheckBox,
        QLabel,
        QListView,
        QScrollArea,
        QTableView,
        QTableWidget,
        QTextEdit,
        QPlainTextEdit,
        QGraphicsView,
        QTreeView,
        QTreeWidget,
        QPushButton,
        QWidget,
    )

    HAS_PYSIDE6 = True
except ImportError:  # 系统 Python 无 PySide6：整模块跳过
    raise unittest.SkipTest("需要 PySide6（用 .venv-ui-v2/bin/python 运行）")

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.queries import base, competition_queries, dashboard_queries, match_queries
from football_simulator.schedule import TOTAL_WEEKS, build_week_calendar
from football_simulator.ui_v2 import theme
from football_simulator.ui_v2.components import EntityLink, EntityTable
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.dashboard_page import DashboardPage
from football_simulator.ui_v2.pages.entity_page_base import PageContext
from football_simulator.ui_v2.pages.season_overview_page import SeasonOverviewPage
from football_simulator.ui_v2.pages.weekly_report_page import WeeklyReportPage
from football_simulator.ui_v2.services import SimulatorUIService

from tests.support import advance_week, create_save, load_state_json, run_season, run_weeks, seeded_provider

SAVE_A = "dash_a"  # 4 周，无待办
SAVE_B = "dash_b"  # 第 25 周，转会待办
SAVE_C = "dash_c"  # 第 1 赛季完整归档 + 第 2 赛季 3 周
SAVE_D = "dash_d"  # 第 49 周，能力审核 + 选秀待办

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = PROJECT_ROOT / "Reviews" / "ui_audit" / "phase4"
WINDOW_SIZES: Tuple[Tuple[int, int], ...] = ((1440, 860), (1680, 980))

# 与页面模块一致的确定性 52 周赛历（38 轮联赛 + 固定休赛/杯赛/附加赛常量）。
_WEEK_CALENDAR = build_week_calendar([[] for _ in range(38)])

# 纵向滚动面判定：QHeaderView 虽是 QAbstractScrollArea 子类但不构成纵向内容滚动面。
_SCROLL_SURFACE_CLASSES = (
    QScrollArea,
    QTableView,
    QTableWidget,
    QListView,
    QTreeView,
    QTreeWidget,
    QTextEdit,
    QPlainTextEdit,
    QGraphicsView,
)


# -- 共享存档与 QApplication ------------------------------------------------

_SHARED: Dict[str, Path] = {}
_APP: Optional[QApplication] = None


def _teardown_shared() -> None:
    root = _SHARED.get("root")
    sim_state.set_rng_provider(None)
    sim_runtime.set_save_root_override(None)
    if root is not None:
        shutil.rmtree(str(root), ignore_errors=True)


def _shared_root() -> Path:
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_page_dash_weekly_season_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())
        create_save(SAVE_A)
        run_weeks(SAVE_A, 4)
        create_save(SAVE_B)
        run_weeks(SAVE_B, 24)
        advance_week(SAVE_B)  # 第 25 周（冬窗休赛周）→ 转会待办
        create_save(SAVE_C)
        run_season(SAVE_C)
        create_save(SAVE_C)  # 开启第 2 赛季
        run_weeks(SAVE_C, 3)
        create_save(SAVE_D)
        run_weeks(SAVE_D, 49)  # 赛季末结算 → 能力审核 + 选秀待办
        _SHARED["root"] = root
        atexit.register(_teardown_shared)
    else:
        sim_runtime.set_save_root_override(_SHARED["root"])
        sim_state.set_rng_provider(seeded_provider())
    return _SHARED["root"]


def _app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        _APP.setStyleSheet(theme.APP_STYLE)
    return _APP


# -- 页面工厂 ----------------------------------------------------------------


class _Harness:
    """手工构造的 PageContext：记录路由与页面状态，可选把路由应用到已注册页面。"""

    def __init__(self, save_name: str, service: Optional[SimulatorUIService] = None, apply_to_pages: bool = False) -> None:
        self.save_name = save_name
        self.service = service
        self.routes: List[Route] = []
        self.states: Dict[str, Dict[str, object]] = {}
        self.current: Optional[Route] = None
        self.pages: List[QWidget] = []
        self.apply_to_pages = apply_to_pages

    def context(self) -> PageContext:
        return PageContext(
            save_name_provider=lambda: self.save_name,
            navigate=self._navigate,
            route_provider=lambda: self.current,
            page_state_get=self._state_get,
            page_state_set=self._state_set,
            service=self.service,
        )

    def attach(self, page: QWidget) -> None:
        self.pages.append(page)

    def _navigate(self, route: Route) -> None:
        self.routes.append(route)
        self.current = route
        if self.apply_to_pages:
            for page in self.pages:
                page.apply_route(route)

    def _state_get(self, key: str) -> Optional[Dict[str, object]]:
        state = self.states.get(key)
        return dict(state) if state else None

    def _state_set(self, key: str, value: Dict[str, object]) -> None:
        self.states[key] = dict(value)


def _make_dashboard(save_name: str) -> Tuple[_Harness, DashboardPage]:
    _shared_root()
    harness = _Harness(save_name)
    page = DashboardPage(harness.context())
    page.apply_route(Route("dashboard"))
    return harness, page


def _make_weekly(week: int, apply_to_pages: bool = False) -> Tuple[_Harness, WeeklyReportPage]:
    harness = _Harness(SAVE_A, apply_to_pages=apply_to_pages)
    page = WeeklyReportPage(harness.context())
    harness.attach(page)
    page.apply_route(Route("weekly_report", week=week))
    return harness, page


def _make_season(season: int, save_name: str = SAVE_A, service: Optional[SimulatorUIService] = None, apply_to_pages: bool = False) -> Tuple[_Harness, SeasonOverviewPage]:
    harness = _Harness(save_name, service=service, apply_to_pages=apply_to_pages)
    page = SeasonOverviewPage(harness.context())
    harness.attach(page)
    page.apply_route(Route("season_overview", season=season))
    return harness, page


def _show_page(page: QWidget, size: Tuple[int, int] = (1440, 860)) -> None:
    page.resize(*size)
    page.show()
    QApplication.processEvents()


def _click_link(link: QWidget) -> None:
    QTest.mouseClick(
        link,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(link.width() // 2, link.height() // 2),
    )


def _vertical_scroll_surfaces(widget: QWidget) -> List[QWidget]:
    """统计纵向滚动面；按方案 §8.2，下拉框（QComboBox）的弹出视图是临时
    弹层而非主内容，不计入限制。"""
    from PySide6.QtWidgets import QComboBox

    surfaces = []
    for child in widget.findChildren(QAbstractScrollArea):
        if not isinstance(child, _SCROLL_SURFACE_CLASSES):
            continue
        ancestor = child.parent()
        combo_popup = False
        while ancestor is not None:
            if isinstance(ancestor, QComboBox):
                combo_popup = True
                break
            ancestor = ancestor.parent()
        if combo_popup:
            continue
        surfaces.append(child)
    if isinstance(widget, _SCROLL_SURFACE_CLASSES):
        surfaces.append(widget)
    return surfaces


# -- 首页（存档 A：无待办） ----------------------------------------------------


class DashboardPageTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.harness, self.page = _make_dashboard(SAVE_A)

    def _dashboard_snapshot(self) -> dashboard_queries.DashboardSnapshot:
        with base.open_read_connection(SAVE_A) as conn:
            return dashboard_queries.get_dashboard(conn)

    def test_status_values_match_save_pointer(self) -> None:
        snapshot = self._dashboard_snapshot()
        labels = {key: label.text() for key, label in self.page._status_labels.items()}
        self.assertEqual(labels["season"], f"第 {snapshot.current_season} 赛季")
        self.assertEqual(labels["week"], f"第 {snapshot.current_week} / {TOTAL_WEEKS} 周")
        # 当前阶段 = 周指针所指周次（本周）的赛历 label（存档真实赛历，已按杯赛激活修饰）。
        with base.open_read_connection(SAVE_A) as conn:
            week_labels = base.load_week_labels(conn)
        expected_phase = (
            week_labels[snapshot.current_week]["label"]
            if snapshot.current_week < len(week_labels)
            else "赛季已结束"
        )
        self.assertEqual(labels["phase"], expected_phase)
        self.assertEqual(labels["pending"], "0")

    def test_upcoming_rows_match_query_and_navigate_to_match(self) -> None:
        snapshot = self._dashboard_snapshot()
        upcoming = snapshot.upcoming_matches
        self.assertTrue(upcoming)
        self.assertEqual(len(self.page._upcoming_data), len(upcoming))
        first = upcoming[0]
        self.assertEqual(
            self.page._upcoming_links[0].route,
            Route("match", match=first.match_id),
        )
        _show_page(self.page)
        _click_link(self.page._upcoming_links[0])
        self.assertEqual(self.harness.routes[-1], Route("match", match=first.match_id))
        self.page.hide()

    def test_latest_rows_match_query_and_score_navigates_to_match(self) -> None:
        snapshot = self._dashboard_snapshot()
        latest = snapshot.latest_results
        self.assertTrue(latest)
        self.assertEqual(len(self.page._latest_data), len(latest))
        # 每行两个链接（比赛描述 + 比分），都指向 match 路由。
        self.assertEqual(len(self.page._latest_links), 2 * len(latest))
        first = latest[0]
        self.assertTrue(first.is_completed)
        score_link = self.page._latest_links[1]
        self.assertEqual(score_link.route, Route("match", match=first.match_id))
        self.assertEqual(score_link.text(), f"{first.home_goals}-{first.away_goals}")
        _show_page(self.page)
        _click_link(score_link)
        self.assertEqual(self.harness.routes[-1], Route("match", match=first.match_id))
        self.page.hide()

    def test_team_links_use_stable_team_routes(self) -> None:
        first = self._dashboard_snapshot().upcoming_matches[0]
        expected = Route("team", team=first.home.team_id, season=first.season_number)
        routes = [link.route for link in self.page.findChildren(EntityLink)]
        self.assertIn(expected, routes)
        away_expected = Route("team", team=first.away.team_id, season=first.season_number)
        self.assertIn(away_expected, routes)

    def test_leader_real_only_checkbox_filters_leaders(self) -> None:
        with base.open_read_connection(SAVE_A) as conn:
            all_snapshot = dashboard_queries.get_dashboard(conn)
            real_snapshot = dashboard_queries.get_dashboard(conn, leaderboards_is_real=True)

        check = self.page.findChild(QCheckBox, "dashboardLeaderRealOnlyCheck")
        self.assertIsNotNone(check)
        check.setChecked(True)
        self.assertTrue(self.page._leader_real_only)

        # 刷新后展示的球员链接全部落在真实球员榜单集合内。
        # 注意：首页只展示每个联赛的榜首（top_scorers[0] / assist_leaders[0]）。
        real_player_ids = {
            entry.player.player_id
            for leaders in real_snapshot.league_leaders
            for entry in (
                leaders.top_scorers[0] if leaders.top_scorers else None,
                leaders.assist_leaders[0] if leaders.assist_leaders else None,
            )
            if entry is not None
        }
        shown_player_ids = {
            link.route.params.get("player")
            for link in self.page._leader_links
            if link.route is not None and link.route.name == "player"
        }
        self.assertTrue(shown_player_ids)
        self.assertTrue(shown_player_ids <= real_player_ids)

        # 刷新会重建区块与复选框；重新查找当前实例再取消勾选。
        check = self.page.findChild(QCheckBox, "dashboardLeaderRealOnlyCheck")
        self.assertIsNotNone(check)
        check.setChecked(False)
        self.assertFalse(self.page._leader_real_only)
        # 取消勾选后主页展示的球员链接应恢复为完整榜单（含真实球员榜首）。
        all_player_ids = {
            entry.player.player_id
            for leaders in all_snapshot.league_leaders
            for entry in (
                leaders.top_scorers[0] if leaders.top_scorers else None,
                leaders.assist_leaders[0] if leaders.assist_leaders else None,
            )
            if entry is not None
        }
        shown_after_uncheck = {
            link.route.params.get("player")
            for link in self.page._leader_links
            if link.route is not None and link.route.name == "player"
        }
        self.assertEqual(shown_after_uncheck, all_player_ids)
        with base.open_read_connection(SAVE_A) as conn:
            self.assertEqual(
                dashboard_queries.get_dashboard(conn).league_leaders,
                all_snapshot.league_leaders,
            )

    def test_pending_block_hidden_without_pending(self) -> None:
        self.assertIsNotNone(self.page._pending_frame)
        self.assertFalse(self.page._pending_frame.isVisibleTo(self.page))

    def test_single_scroll_surface_both_sizes(self) -> None:
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                _show_page(self.page, size)
                surfaces = _vertical_scroll_surfaces(self.page)
                self.assertEqual(len(surfaces), 1)
                self.assertIsInstance(surfaces[0], QScrollArea)
                self.assertEqual(self.page.findChildren(QTextEdit), [])
                self.assertEqual(self.page.findChildren(QPlainTextEdit), [])
                self.page.hide()


# -- 首页待办提示块（存档 B：转会待办；存档 D：能力+选秀待办） -------------------


class DashboardPendingBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()

    def test_transfer_pending_block_routes_to_transfers(self) -> None:
        with base.open_read_connection(SAVE_B) as conn:
            counts = dashboard_queries.get_dashboard(conn).pending_counts
        self.assertGreaterEqual(counts.transfer_review, 1)
        harness, page = _make_dashboard(SAVE_B)
        self.assertTrue(page._pending_frame.isVisibleTo(page))
        button = page._pending_buttons.get("transfer_review")
        self.assertIsNotNone(button)
        _show_page(page)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertEqual(harness.routes[-1], Route("transfers", season=1))
        page.hide()

    def test_ability_and_draft_pending_buttons_route(self) -> None:
        with base.open_read_connection(SAVE_D) as conn:
            counts = dashboard_queries.get_dashboard(conn).pending_counts
        self.assertGreaterEqual(counts.ability_review, 1)
        self.assertGreaterEqual(counts.draft, 1)
        harness, page = _make_dashboard(SAVE_D)
        self.assertTrue(page._pending_frame.isVisibleTo(page))
        ability_button = page._pending_buttons.get("ability_review")
        draft_button = page._pending_buttons.get("draft")
        self.assertIsNotNone(ability_button)
        self.assertIsNotNone(draft_button)
        _show_page(page)
        QTest.mouseClick(ability_button, Qt.MouseButton.LeftButton)
        self.assertEqual(harness.routes[-1], Route("season_overview", season=1))
        QTest.mouseClick(draft_button, Qt.MouseButton.LeftButton)
        self.assertEqual(harness.routes[-1], Route("draft", season=1))
        page.hide()

    def test_pending_total_reflected_in_status(self) -> None:
        harness, page = _make_dashboard(SAVE_B)
        with base.open_read_connection(SAVE_B) as conn:
            counts = dashboard_queries.get_dashboard(conn).pending_counts
        total = counts.ability_review + counts.transfer_review + counts.draft
        self.assertEqual(page._status_labels["pending"].text(), str(total))


# -- 周报（存档 A） ------------------------------------------------------------


class WeeklyReportPageTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()

    def _week_matches(self, week: int) -> List[match_queries.MatchRow]:
        with base.open_read_connection(SAVE_A) as conn:
            season = base.resolve_current_season(conn).season_number
            return match_queries.list_matches(conn, season, week_number=week)

    def test_week3_groups_match_query_and_row_activation(self) -> None:
        matches = self._week_matches(3)
        self.assertTrue(matches)
        harness, page = _make_weekly(3)
        with base.open_read_connection(SAVE_A) as conn:
            week_labels = base.load_week_labels(conn)
        expected_phase = week_labels[2]["label"] if len(week_labels) > 2 else _WEEK_CALENDAR[2].label
        self.assertEqual(page._phase_label.text(), f"第 3 周 · {expected_phase}")
        # 分组行数之和 == list_matches(week=3)；各分组行数 == 各赛事该周场数。
        total_rows = sum(table.model.rowCount() for table in page._tables)
        self.assertEqual(total_rows, len(matches))
        by_competition: Dict[str, int] = {}
        for row in matches:
            by_competition[row.competition] = by_competition.get(row.competition, 0) + 1
        # 表按 _GROUP_ORDER 排列，行数多重集与查询结果的赛事分布一致。
        self.assertEqual(
            sorted(table.model.rowCount() for table in page._tables),
            sorted(by_competition.values()),
        )
        # 行激活 → match 路由（EntityTable 行激活 = 双击 / Enter）。
        first = matches[0]
        table = page._tables[0]
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            harness.routes[-1],
            Route("match", match=first.match_id),
        )

    def test_week25_winter_break_empty_state(self) -> None:
        harness, page = _make_weekly(25)
        self.assertIs(page._body_stack.currentWidget(), page._empty)
        titles = [label.text() for label in page._empty.findChildren(QLabel)]
        self.assertIn("第 25 周为休赛周（冬窗/夏窗）", titles)
        # 空状态本身不引入任何滚动面。
        self.assertEqual(_vertical_scroll_surfaces(page._empty), [])
        # 休赛周仍可切换周次：上一周/下一周按钮常驻且可用。
        self.assertTrue(page._prev_button.isVisibleTo(page))
        self.assertTrue(page._next_button.isVisibleTo(page))
        page._next_button.click()
        self.assertEqual(harness.routes[-1], Route("weekly_report", week=26))

    def test_prev_next_navigation_between_adjacent_weeks(self) -> None:
        harness, page = _make_weekly(3, apply_to_pages=True)
        self.assertIsNotNone(page._next_button)
        page._next_button.click()
        self.assertEqual(harness.routes[-1], Route("weekly_report", week=4))
        self.assertEqual(page._week, 4)
        self.assertIsNotNone(page._prev_button)
        page._prev_button.click()
        self.assertEqual(harness.routes[-1], Route("weekly_report", week=3))
        self.assertEqual(page._week, 3)

    def test_boundary_weeks_disable_buttons(self) -> None:
        harness, page = _make_weekly(1)
        self.assertFalse(page._prev_button.isEnabled())
        self.assertTrue(page._next_button.isEnabled())
        harness52, page52 = _make_weekly(TOTAL_WEEKS)
        self.assertTrue(page52._prev_button.isEnabled())
        self.assertFalse(page52._next_button.isEnabled())

    def test_no_nested_vertical_scrolling_both_sizes(self) -> None:
        for week in (3, 25):
            harness, page = _make_weekly(week)
            for size in WINDOW_SIZES:
                with self.subTest(week=week, size=size):
                    _show_page(page, size)
                    # 页面恰有一个 QScrollArea（唯一主滚动面）。
                    scroll_areas = page.findChildren(QScrollArea)
                    self.assertEqual(len(scroll_areas), 1)
                    self.assertIs(scroll_areas[0], page._scroll)
                    # 每组 EntityTable 完整展开：纵向滚动条永不激活（禁止小框内滚动）。
                    for table in page._tables:
                        self.assertIsInstance(table, EntityTable)
                        self.assertEqual(
                            table.view.verticalScrollBar().maximum(),
                            0,
                            f"第 {week} 周分组表存在内部滚动",
                        )
                    if week == 25:
                        self.assertEqual(page._tables, [])
            page.hide()


# -- 赛季总览（存档 A / C / D） -------------------------------------------------


class SeasonOverviewPageTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()

    # -- 时间线与状态（存档 A：进行中） --------------------------------------

    def test_timeline_has_52_week_links_and_routes(self) -> None:
        harness, page = _make_season(1)
        self.assertEqual(len(page._week_links), TOTAL_WEEKS)
        self.assertEqual(set(page._week_links), set(range(1, TOTAL_WEEKS + 1)))
        for week_number in (1, 3, 25, 52):
            self.assertEqual(
                page._week_links[week_number].route,
                Route("weekly_report", week=week_number),
            )
        _show_page(page)
        _click_link(page._week_links[25])
        self.assertEqual(harness.routes[-1], Route("weekly_report", week=25))
        page.hide()

    def test_timeline_marks_played_current_and_break_weeks(self) -> None:
        with base.open_read_connection(SAVE_A) as conn:
            season_id = base.season_id_for(conn, 1)
            played = {
                int(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT week_number FROM matches WHERE season_id = ? AND status = 'completed'",
                    (season_id,),
                )
            }
        self.assertTrue(played)
        harness, page = _make_season(1)
        current_week = 4
        self.assertIn("（当前）", page._week_links[current_week].text())
        for week_number in played - {current_week}:
            self.assertIn(
                "#18355a",
                page._week_links[week_number].styleSheet(),
                f"已赛周 W{week_number} 应高亮",
            )
        self.assertIn("#1d4f8f", page._week_links[current_week].styleSheet(), "当前周应有标记色")
        self.assertIn("#26381f", page._week_links[25].styleSheet(), "冬窗周应着色")
        self.assertIn("#342a1d", page._week_links[50].styleSheet(), "夏窗周应着色")
        self.assertIn("#3a3217", page._week_links[46].styleSheet(), "附加赛周应着色")

    def test_status_text_in_progress(self) -> None:
        harness, page = _make_season(1)
        status = page._status_label.text()
        self.assertIn("进行中", status)
        self.assertIn("已模拟 4/52 周", status)
        expected_phase = _WEEK_CALENDAR[4].label  # 周指针 4 → 本周（第 5 周）阶段
        self.assertIn(expected_phase, status)

    # -- 待办链接与只读回退（存档 A：无待办） ---------------------------------

    def test_transfer_and_draft_links_route_to_centers(self) -> None:
        harness, page = _make_season(1)
        self.assertIsNotNone(page._transfer_link)
        self.assertIsNotNone(page._draft_link)
        _show_page(page)
        _click_link(page._transfer_link)
        self.assertEqual(harness.routes[-1], Route("transfers", season=1))
        _click_link(page._draft_link)
        self.assertEqual(harness.routes[-1], Route("draft", season=1))
        page.hide()

    def test_review_empty_without_pending_and_submit_disabled(self) -> None:
        harness, page = _make_season(1)
        self.assertEqual(page._review_items, [])
        self.assertIsNotNone(page._review_hint)
        self.assertIn("暂无待审核能力变动", page._review_hint.text())
        self.assertFalse(page._review_submit.isEnabled())

    def test_single_scroll_surface_both_sizes(self) -> None:
        harness, page = _make_season(1)
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                _show_page(page, size)
                surfaces = _vertical_scroll_surfaces(page)
                self.assertEqual(len(surfaces), 1)
                self.assertIsInstance(surfaces[0], QScrollArea)
                self.assertEqual(page.findChildren(QTextEdit), [])
                self.assertEqual(page.findChildren(QPlainTextEdit), [])
                page.hide()

    # -- 能力审核写流程（存档 D：能力 + 选秀待办） ------------------------------

    def _pending_of(self, save_name: str) -> List[dict]:
        with base.open_read_connection(save_name) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM pending_actions WHERE type = 'ability_review' ORDER BY ordinal"
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def test_review_table_rows_match_pending_count(self) -> None:
        pending = self._pending_of(SAVE_D)
        self.assertGreater(len(pending), 0)
        service = SimulatorUIService()
        harness, page = _make_season(1, save_name=SAVE_D, service=service)
        self.assertEqual(len(page._review_items), len(pending))
        self.assertEqual(
            sorted(item["name"] for item in page._review_items),
            sorted(item["name"] for item in pending),
        )
        # 每行一组“保留/采纳”互斥按钮，默认保留。
        self.assertEqual(len(page._review_buttons), len(pending))
        for name, buttons in page._review_buttons.items():
            self.assertTrue(buttons["keep"].isChecked())
            self.assertFalse(buttons["approve"].isChecked())
        self.assertTrue(page._review_submit.isEnabled())

        # 安全约束：service 为 None 时审核表仍只读展示（待办来自同一份数据），
        # 提交按钮禁用并显示说明。
        readonly_harness, readonly_page = _make_season(1, save_name=SAVE_D)
        self.assertEqual(len(readonly_page._review_items), len(pending))
        self.assertFalse(readonly_page._review_submit.isEnabled())
        readonly_page.hide()

    def test_submit_all_approved_clears_pending_and_applies_ability(self) -> None:
        service = SimulatorUIService()
        pending = self._pending_of(SAVE_D)
        harness, page = _make_season(1, save_name=SAVE_D, service=service)
        before = {item["name"]: item for item in page._review_items}
        self.assertEqual(len(before), len(pending))

        # 全部选择“采纳”（语义：能力越高越好，采纳 = 采用 new_ability）。
        for buttons in page._review_buttons.values():
            buttons["approve"].setChecked(True)
            buttons["keep"].setChecked(False)
        _show_page(page)
        QTest.mouseClick(page._review_submit, Qt.MouseButton.LeftButton)
        QApplication.processEvents()

        # 提交后：待办清空 + 池内能力 == new_ability + 页面反馈。
        snapshot = service.load_state(SAVE_D).snapshot
        self.assertEqual(snapshot.pending_ability_review, [])
        state = load_state_json(SAVE_D)
        pool = {profile["name"]: int(profile["ability"]) for profile in state["real_player_pool"]}
        for name, item in before.items():
            self.assertEqual(pool[name], int(item["new_ability"]), f"{name} 的能力应等于采纳后的 new_ability")
        self.assertIn("暂无待审核能力变动", page._review_hint.text())
        self.assertEqual(
            page._review_status_label.text(),
            f"已提交 {len(before)} 项（采纳 {len(before)} 项）",
        )
        self.assertFalse(page._review_submit.isEnabled())
        page.hide()

    # -- 赛季选择器与归档冠军（存档 C：两个赛季） ------------------------------

    def test_season_selector_navigates_to_new_route(self) -> None:
        harness, page = _make_season(2, save_name=SAVE_C, apply_to_pages=True)
        combo = page._season_combo
        self.assertIsNotNone(combo)
        self.assertEqual(combo.count(), 2)
        target = combo.findData(1)
        self.assertGreaterEqual(target, 0)
        combo.setCurrentIndex(target)
        self.assertEqual(harness.routes[-1], Route("season_overview", season=1))
        # 外壳应用新路由后页面按第 1 赛季（已归档）刷新。
        self.assertEqual(page._season, 1)
        self.assertIn("已结束", page._status_label.text())

    def test_archived_season_shows_champions_with_team_links(self) -> None:
        with base.open_read_connection(SAVE_C) as conn:
            archive = competition_queries.load_archive(conn, 1)
        self.assertIsNotNone(archive)
        premier_champion_name = archive["premier_order"][0]
        harness, page = _make_season(1, save_name=SAVE_C)
        self.assertIn("已结束", page._status_label.text())
        # 冠军区包含“一级联赛冠军”标题与冠军球队链接。
        texts = [label.text() for label in page.findChildren(QLabel)]
        self.assertIn("一级联赛冠军", texts)
        champion_routes = [link.route for link in page._champion_links]
        self.assertTrue(champion_routes)
        team_routes = [route for route in champion_routes if route.name == "team"]
        self.assertTrue(team_routes)
        with base.open_read_connection(SAVE_C) as conn:
            row = conn.execute(
                "SELECT team_id FROM teams WHERE name = ?", (premier_champion_name,)
            ).fetchone()
        expected = Route("team", team=int(row["team_id"]), season=1)
        self.assertIn(expected, team_routes)

    def test_active_season_shows_standings_leader_summary(self) -> None:
        harness, page = _make_season(2, save_name=SAVE_C)
        with base.open_read_connection(SAVE_C) as conn:
            season_id = base.season_id_for(conn, 2)
            premier_top = competition_queries.league_standings_rows(conn, season_id, 2, "premier")[0]
        self.assertIn("进行中", page._status_label.text())
        leader_routes = [
            link.route
            for link in page.findChildren(EntityLink)
            if link.route is not None and link.route.name == "team"
        ]
        self.assertIn(Route("team", team=premier_top.team_id, season=2), leader_routes)


# -- 截图（4 种关键状态 × 两种尺寸） --------------------------------------------


class ScreenshotTests(unittest.TestCase):
    def _capture(self, page: QWidget, size: Tuple[int, int], name: str) -> Path:
        page.resize(*size)
        page.show()
        QApplication.processEvents()
        pixmap = page.grab()
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target = SCREENSHOT_DIR / name
        self.assertTrue(pixmap.save(str(target)), f"截图保存失败：{target}")
        page.hide()
        return target

    def test_capture_four_states_both_sizes(self) -> None:
        _app()
        _shared_root()
        produced: List[Path] = []

        # 首页（存档 A，无待办）。
        harness, dashboard = _make_dashboard(SAVE_A)
        for width, height in WINDOW_SIZES:
            produced.append(self._capture(dashboard, (width, height), f"dashboard_home_{width}x{height}.png"))

        # 周报：第 3 周（有比赛）与第 25 周（冬窗休赛周）。
        harness, weekly_matches = _make_weekly(3)
        harness, weekly_break = _make_weekly(25)
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(weekly_matches, (width, height), f"weekly_matches_{width}x{height}.png")
            )
            produced.append(
                self._capture(weekly_break, (width, height), f"weekly_break_{width}x{height}.png")
            )

        # 赛季总览（存档 D，含能力审核 + 选秀待办）。
        harness, season = _make_season(1, save_name=SAVE_D)
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(season, (width, height), f"season_overview_pending_{width}x{height}.png")
            )

        for path in produced:
            self.assertTrue(path.exists(), f"缺少截图：{path}")
            self.assertGreater(path.stat().st_size, 0, f"截图为空：{path}")


if __name__ == "__main__":
    unittest.main()
