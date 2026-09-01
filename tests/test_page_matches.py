"""阶段 4 验收测试：比赛中心（MatchCenterPage）与比赛详情页（MatchDetailPage）。

共享夹具（与 tests/test_page_players.py / test_page_teams.py 一致）：
1. 初始化第 1 赛季并跑完整赛季（766 场全部 completed）；
2. 开启第 2 赛季并推进 3 周（已有 68 场 completed、700 场 scheduled，
   含第 3 周的优胜者杯第 1 轮 8 场）。

覆盖内容（对应实施方案 §8.7 与任务规格）：
- 列表页：行数与 list_matches 一致（season=1）、状态/赛事/周次过滤各自生效、
  行激活双路径（双击 + Enter）→ match 路由、筛选保存/恢复、路由参数优先于
  页面状态、赛季切换 navigate、空状态；
- 详情页：已赛比分板/事件完整顺序/22 行球员表（含全 0 行）、主队与球员点击
  → 对应路由、上一场/下一场邻居正确（边界禁用）、未赛详情"未赛"与两队
  摘要及球队链接、match_id 不存在 → EmptyState、route_context 面包屑上下文；
- 零嵌套纵向滚动：列表页与两种详情页在 1440×860 与 1680×980 各一遍，页面
  至多一个可纵向滚动的滚动面（列表页恰为主表 QTableView、已赛详情恰为外层
  QScrollArea，球员数据表固定高度不滚动）；
- 截图：列表页/已赛详情/未赛详情 × 两种尺寸保存到 Reviews/ui_audit/phase4。

运行：
    QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_page_matches -v
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import atexit
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from tests import support
from tests.support import create_save, run_season, run_weeks, seeded_provider
from football_simulator.queries import base, match_queries, team_queries
from football_simulator.ui_v2.navigation import Route

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractScrollArea,
        QApplication,
        QHeaderView,
        QLabel,
        QPushButton,
        QScrollArea,
        QTableView,
    )
    from football_simulator.ui_v2 import theme
    from football_simulator.ui_v2.components import EmptyState, EntityLink
    from football_simulator.ui_v2.pages.entity_page_base import PageContext
    from football_simulator.ui_v2.pages.match_detail_page import (
        _HEADER_HEIGHT,
        _ROW_HEIGHT,
        _TABLE_BORDER,
        _LinkColumnDelegate,
        MatchDetailPage,
    )
    from football_simulator.ui_v2.pages.matches_page import MatchCenterPage

    HAS_PYSIDE6 = True
except ImportError:  # pragma: no cover - 无 GUI 环境占位
    HAS_PYSIDE6 = False

if not HAS_PYSIDE6:
    raise unittest.SkipTest("需要 PySide6（用 .venv-ui-v2/bin/python 运行）")

# ---------------------------------------------------------------------------
# 共享存档夹具：赛季 1 完整跑完，第 2 赛季推进 3 周
# ---------------------------------------------------------------------------

SAVE_NAME = "page_matches"
PROJECT_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = PROJECT_DIR / "Reviews" / "ui_audit" / "phase4"
WINDOW_SIZES: Tuple[Tuple[int, int], ...] = ((1440, 860), (1680, 980))

_SHARED: Dict[str, Path] = {}
_APP: Optional[QApplication] = None


def _teardown_shared() -> None:
    root = _SHARED.get("root")
    sim_state.set_rng_provider(None)
    sim_runtime.set_save_root_override(None)
    if root is not None:
        shutil.rmtree(str(root), ignore_errors=True)


def _shared_save() -> str:
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_page_matches_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())
        create_save(SAVE_NAME)
        run_season(SAVE_NAME)
        create_save(SAVE_NAME)  # 开启第 2 赛季
        run_weeks(SAVE_NAME, 3)
        _SHARED["root"] = root
        atexit.register(_teardown_shared)
    else:
        sim_runtime.set_save_root_override(_SHARED["root"])
        sim_state.set_rng_provider(seeded_provider())
    return SAVE_NAME


# ---------------------------------------------------------------------------
# 共享事实（只查询一次；数据驱动，不写死球队/比分）
# ---------------------------------------------------------------------------

_FACTS: Dict[str, object] = {}


def _facts() -> Dict[str, object]:
    if not _FACTS:
        _shared_save()
        with base.open_read_connection(SAVE_NAME) as conn:
            season1 = match_queries.list_matches(conn, 1)
            season2 = match_queries.list_matches(conn, 2)
            season2_completed = [row for row in season2 if row.is_completed]
            season2_scheduled = [row for row in season2 if not row.is_completed]
            premier1 = [row for row in season1 if row.competition == base.COMPETITION_PREMIER]
            playoff1 = [row for row in season1 if row.competition == base.COMPETITION_PLAYOFF]
            mid = premier1[50]
            prev_id, next_id = match_queries.get_match_neighbors(
                conn, mid.match_id, competition=base.COMPETITION_PREMIER
            )
            first_prev, _ = match_queries.get_match_neighbors(
                conn, playoff1[0].match_id, competition=base.COMPETITION_PLAYOFF
            )
            _, last_next = match_queries.get_match_neighbors(
                conn, playoff1[-1].match_id, competition=base.COMPETITION_PLAYOFF
            )
            scheduled = season2_scheduled[0]
            sched_prev, sched_next = match_queries.get_match_neighbors(
                conn, scheduled.match_id, competition=scheduled.competition
            )
            _FACTS.update(
                {
                    "season1": season1,
                    "season2": season2,
                    "season2_completed": season2_completed,
                    "season2_scheduled": season2_scheduled,
                    "mid": mid,
                    "mid_detail": match_queries.get_match_detail(conn, mid.match_id),
                    "mid_neighbors": (prev_id, next_id),
                    "playoff_first": playoff1[0],
                    "playoff_first_prev": first_prev,
                    "playoff_last": playoff1[-1],
                    "playoff_last_next": last_next,
                    "scheduled": scheduled,
                    "scheduled_detail": match_queries.get_match_detail(conn, scheduled.match_id),
                    "scheduled_neighbors": (sched_prev, sched_next),
                }
            )
            # 未赛比赛主客队的当前赛季积分榜行（standings_row 摘要）
            _FACTS["scheduled_standings"] = {
                scheduled.home.team_id: team_queries.get_team_season_profile(
                    conn, scheduled.home.team_id, scheduled.season_number
                ).standings_row,
                scheduled.away.team_id: team_queries.get_team_season_profile(
                    conn, scheduled.away.team_id, scheduled.season_number
                ).standings_row,
            }
    return _FACTS


# ---------------------------------------------------------------------------
# PageContext 测试替身与 Qt 公共设施
# ---------------------------------------------------------------------------


class _Harness:
    """手工构造的 PageContext：记录路由与页面状态（参照 test_page_teams.py）。"""

    def __init__(self, save_name: str) -> None:
        self.save_name = save_name
        self.routes: List[Route] = []
        self.states: Dict[str, Dict[str, object]] = {}
        self.current: Optional[Route] = None

    def context(self) -> PageContext:
        return PageContext(
            save_name_provider=lambda: self.save_name,
            navigate=self._navigate,
            route_provider=lambda: self.current,
            page_state_get=self._state_get,
            page_state_set=self._state_set,
        )

    def _navigate(self, route: Route) -> None:
        self.routes.append(route)
        self.current = route

    def _state_get(self, key: str) -> Optional[Dict[str, object]]:
        state = self.states.get(key)
        return dict(state) if state else None

    def _state_set(self, key: str, value: Dict[str, object]) -> None:
        self.states[key] = dict(value)


def _make_list_page(route: Route, save_name: str = SAVE_NAME) -> Tuple[_Harness, MatchCenterPage]:
    harness = _Harness(_shared_save() if save_name == SAVE_NAME else save_name)
    page = MatchCenterPage(harness.context())
    page.apply_route(route)
    return harness, page


def _make_detail_page(match_id: str) -> Tuple[_Harness, MatchDetailPage]:
    harness = _Harness(_shared_save())
    page = MatchDetailPage(harness.context())
    page.apply_route(Route("match", match=match_id))
    try:
        # 全阵容 22 行口径断言；默认只显示真实球员另有专测。
        page._make_real_only_check().setChecked(False)
    except Exception:
        pass
    return harness, page


def _app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        _APP.setStyleSheet(theme.APP_STYLE)
    return _APP


def _show_page(page, size: Tuple[int, int] = (1440, 860)) -> None:
    page.resize(*size)
    page.show()
    QApplication.processEvents()
    QApplication.processEvents()


# -- 滚动面统计（§8.2 / §12.4：同一路径上不允许两个纵向滚动面） -----------------


def _visible_scroll_surfaces(widget) -> List[QAbstractScrollArea]:
    """可见的 QAbstractScrollArea（QHeaderView 属于表格自身，排除）。"""
    surfaces: List[QAbstractScrollArea] = []
    for child in widget.findChildren(QAbstractScrollArea):
        if isinstance(child, QHeaderView):
            continue
        if not child.isVisible():
            continue
        surfaces.append(child)
    return surfaces


def _scrollable_surfaces(widget) -> List[QAbstractScrollArea]:
    """可见且纵向滚动条可用的滚动面（真正承担纵向滚动的容器）。"""
    surfaces = []
    for surface in _visible_scroll_surfaces(widget):
        if surface.verticalScrollBar().maximum() > 0:
            surfaces.append(surface)
    return surfaces


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class _PageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _shared_save()
        cls.app = _app()

    def tearDown(self) -> None:
        self.app.processEvents()


# ---------------------------------------------------------------------------
# a. 比赛中心列表页
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class MatchCenterTests(_PageTestCase):
    def _query_rows(self, season: int, **filters) -> List[match_queries.MatchRow]:
        with base.open_read_connection(SAVE_NAME) as conn:
            return match_queries.list_matches(conn, season, **filters)

    def test_row_count_matches_query_season1(self) -> None:
        harness, page = _make_list_page(Route("matches", season=1))
        expected = self._query_rows(1)
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual(len(expected), 766)  # 380 premier + 380 second + 6 附加赛
        self.assertEqual(
            [row.match_id for row in page._rows],
            [row.match_id for row in expected],
        )
        self.assertEqual(page._stack.currentIndex(), 0)  # 主表页而非空状态
        self.assertEqual(page._season_combo.currentData(), 1)
        self.assertEqual(page._summary_label.text(), "共 766 场 · 已赛 766 · 未赛 0")

    def test_status_filter_matches_query(self) -> None:
        harness, page = _make_list_page(Route("matches", season=2))
        page._status_combo.setCurrentText("已赛")
        expected = self._query_rows(2, status="completed")
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual(len(expected), 68)
        page._status_combo.setCurrentText("未赛")
        expected = self._query_rows(2, status="scheduled")
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual(len(expected), 700)
        self.assertTrue(all(row.score_text == "vs" for row in page._rows))
        page._status_combo.setCurrentText("全部状态")
        self.assertEqual(len(page._rows), len(self._query_rows(2)))

    def test_competition_filter_matches_query(self) -> None:
        harness, page = _make_list_page(Route("matches", season=1))
        page._competition_combo.setCurrentText("次级联赛")
        expected = self._query_rows(1, competition="次级联赛")
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual(len(expected), 380)
        self.assertTrue(all(row.competition == "次级联赛" for row in page._rows))
        harness2, page2 = _make_list_page(Route("matches", season=2))
        page2._competition_combo.setCurrentText("优胜者杯")
        expected = self._query_rows(2, competition="优胜者杯")
        self.assertEqual(len(page2._rows), len(expected))
        self.assertEqual(len(expected), 8)

    def test_week_filter_matches_query(self) -> None:
        harness, page = _make_list_page(Route("matches", season=1))
        page._week_combo.setCurrentIndex(page._week_combo.findData(5))
        expected = self._query_rows(1, week_number=5)
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual(len(expected), 20)
        self.assertTrue(all(row.week_text == "第 5 周" for row in page._rows))

    def test_row_activation_navigates_via_double_click_and_enter(self) -> None:
        harness, page = _make_list_page(Route("matches", season=1))
        _show_page(page)
        view = page._table.view
        # 双击路径（offscreen 下首个鼠标事件需要先单击建立指针上下文）
        target = page._rows[3]
        view.setCurrentIndex(view.model().index(3, 0))
        self.app.processEvents()
        rect = view.visualRect(view.model().index(3, 0))
        QTest.mouseClick(
            view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center()
        )
        QTest.mouseDClick(
            view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center()
        )
        self.assertTrue(harness.routes)
        self.assertEqual(harness.routes[-1], Route("match", match=target.match_id))
        # 键盘 Enter 路径
        harness.routes.clear()
        target2 = page._rows[9]
        view.setCurrentIndex(view.model().index(9, 0))
        view.setFocus()
        self.app.processEvents()
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(harness.routes[-1], Route("match", match=target2.match_id))

    def test_filter_state_saved_and_restored(self) -> None:
        harness, page = _make_list_page(Route("matches", season=2))
        page._competition_combo.setCurrentText("优胜者杯")
        page._week_combo.setCurrentIndex(page._week_combo.findData(3))
        page._status_combo.setCurrentText("已赛")
        route_key = Route("matches", season=2).route_key
        self.assertIn(route_key, harness.states)  # 筛选变化已写入页面状态

        harness2 = _Harness(_shared_save())
        harness2.states.update(harness.states)  # 模拟"返回列表页"按 route_key 恢复
        page2 = MatchCenterPage(harness2.context())
        page2.apply_route(Route("matches", season=2))
        self.assertEqual(page2._competition_combo.currentText(), "优胜者杯")
        self.assertEqual(page2._week_combo.currentData(), 3)
        self.assertEqual(page2._status_combo.currentText(), "已赛")
        expected = self._query_rows(2, competition="优胜者杯", week_number=3, status="completed")
        self.assertEqual(len(page2._rows), len(expected))
        self.assertEqual(len(expected), 8)

    def test_route_params_override_page_state(self) -> None:
        """路由显式 competition 优先于保存状态；无显式 week 时恢复状态周次。"""
        harness = _Harness(_shared_save())
        key = Route("matches", season=1, competition="次级联赛").route_key
        harness.states[key] = {
            "season": 1,
            "competition": "优胜者杯",
            "week": 3,
            "status": "全部状态",
        }
        page = MatchCenterPage(harness.context())
        page.apply_route(Route("matches", season=1, competition="次级联赛"))
        self.assertEqual(page._competition_combo.currentText(), "次级联赛")
        self.assertEqual(page._week_combo.currentData(), 3)
        expected = self._query_rows(1, competition="次级联赛", week_number=3)
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual(len(expected), 10)
        self.assertTrue(all(row.competition == "次级联赛" for row in page._rows))

    def test_season_change_navigates_with_current_filters(self) -> None:
        """赛季切换 → navigate 新路由（保留具体赛事），保证后退/前进一致。"""
        harness, page = _make_list_page(Route("matches", season=1))
        page._competition_combo.setCurrentText("一级联赛")
        harness.routes.clear()
        index = page._season_combo.findData(2)
        page._season_combo.setCurrentIndex(index)
        self.assertEqual(
            harness.routes[-1],
            Route("matches", season=2, competition="一级联赛"),
        )

    def test_missing_save_shows_empty_state(self) -> None:
        harness, page = _make_list_page(Route("matches", season=1), save_name="no_such_save")
        self.assertIsInstance(page._stack.currentWidget(), EmptyState)
        self.assertEqual(page._rows, [])

    def test_filter_without_matches_shows_empty_state(self) -> None:
        harness, page = _make_list_page(Route("matches", season=1))
        page._competition_combo.setCurrentText("优胜者杯")
        self.assertIsInstance(page._stack.currentWidget(), EmptyState)
        self.assertEqual(page._rows, [])


# ---------------------------------------------------------------------------
# b. 比赛详情页（已赛赛后报告）
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class MatchDetailCompletedTests(_PageTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        facts = _facts()
        cls.mid: match_queries.MatchRow = facts["mid"]
        cls.detail: match_queries.MatchDetail = facts["mid_detail"]
        cls.prev_id, cls.next_id = facts["mid_neighbors"]

    def _make_page(self):
        return _make_detail_page(self.mid.match_id)

    def test_scoreboard_shows_score_and_team_routes(self) -> None:
        harness, page = self._make_page()
        match = self.detail.match
        self.assertEqual(page._page_stack.currentIndex(), 0)
        self.assertEqual(
            page._score_label.text(), f"{match.home_goals} - {match.away_goals}"
        )
        self.assertIsInstance(page._home_link, EntityLink)
        self.assertIsInstance(page._away_link, EntityLink)
        self.assertEqual(page._home_link.text(), match.home.display_name)
        self.assertEqual(page._away_link.text(), match.away.display_name)
        self.assertEqual(
            page._home_link.route,
            Route("team", team=match.home.team_id, season=match.season_number),
        )
        self.assertEqual(
            page._away_link.route,
            Route("team", team=match.away.team_id, season=match.season_number),
        )
        info_text = page._info_label.text()
        self.assertIn(match.competition, info_text)
        self.assertIn(f"第 {match.round_number} 轮", info_text)
        self.assertIn(f"第 {match.week_number} 周", info_text)
        self.assertIn("已赛", info_text)

    def test_events_full_list_in_original_order(self) -> None:
        harness, page = self._make_page()
        events = self.detail.key_events
        self.assertGreater(len(events), 6)  # 证明不是旧版"前 6 条"截断
        self.assertEqual(len(page._event_labels), len(events))
        for index, (label, text) in enumerate(zip(page._event_labels, events), start=1):
            self.assertEqual(label.text(), f"{index}. {text}")

    def test_player_table_full_22_rows_with_zero_rows(self) -> None:
        harness, page = self._make_page()
        table = page._player_table
        self.assertIsNotNone(table)
        view = table.view
        lines = self.detail.player_lines
        self.assertEqual(len(lines), 22)
        self.assertEqual(view.model().rowCount(), 22)
        stat_fields = (
            "goals",
            "assists",
            "chances_created",
            "successful_defenses",
            "successful_saves",
            "clean_sheets",
        )
        for row_index, line in enumerate(lines):
            row = table.model.row_at(row_index)
            self.assertEqual(row.player_id, line.player.player_id)
            self.assertEqual(row.player_name, line.player.display_name)
            self.assertEqual(row.team_name, line.team.display_name)
            self.assertEqual(row.appeared, 1)
            for field_name in stat_fields:
                self.assertEqual(getattr(row, field_name), getattr(line, field_name))
        # 必须包含六项全 0 的出场行（对应"注册阵容记出场"口径）
        zero_lines = [
            line for line in lines if all(getattr(line, field_name) == 0 for field_name in stat_fields)
        ]
        self.assertTrue(zero_lines, "22 行中应包含六项全 0 的球员行")

    def test_player_table_fully_expanded_without_vertical_scroll(self) -> None:
        harness, page = self._make_page()
        _show_page(page)
        if getattr(page, "_match_tabs", None) is not None:
            page._match_tabs.setCurrentIndex(1)  # 球员数据页签
            QApplication.processEvents()
        table = page._player_table
        view = table.view
        expected_height = _HEADER_HEIGHT + 22 * _ROW_HEIGHT + _TABLE_BORDER
        self.assertEqual(table.minimumHeight(), expected_height)
        self.assertEqual(table.maximumHeight(), expected_height)
        self.assertEqual(view.verticalScrollBar().maximum(), 0)

    def test_real_only_checkbox_filters_player_table(self) -> None:
        harness, page = self._make_page()
        lines = self.detail.player_lines
        real_count = sum(1 for line in lines if line.player.is_real)
        self.assertLess(real_count, len(lines), "已赛详情应同时含真实与默认球员行")

        real_ids = {line.player.player_id for line in lines if line.player.is_real}

        check = page._make_real_only_check()
        check.setChecked(True)
        table = page._player_table
        self.assertIsNotNone(table)
        self.assertEqual(table.view.model().rowCount(), real_count)
        self.assertEqual(
            {table.model.row_at(i).player_id for i in range(real_count)},
            real_ids,
        )

        # 取消勾选恢复完整的 22 行出场记录。
        check.setChecked(False)
        table = page._player_table
        self.assertEqual(table.view.model().rowCount(), 22)

    def test_home_team_link_click_navigates_to_team_route(self) -> None:
        harness, page = self._make_page()
        _show_page(page)
        before = len(harness.routes)
        QTest.mouseClick(
            page._home_link,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            page._home_link.rect().center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(
            harness.routes[-1],
            Route("team", team=self.detail.match.home.team_id, season=1),
        )

    def test_player_cell_click_navigates_to_player_route(self) -> None:
        harness, page = self._make_page()
        table = page._player_table
        delegate = table.view.itemDelegateForColumn(0)
        self.assertIsInstance(delegate, _LinkColumnDelegate)
        row0 = table.model.row_at(0)
        expected = Route("player", player=row0.player_id, season=1)
        _show_page(page)
        before = len(harness.routes)
        rect = table.view.visualRect(table.view.model().index(0, 0))
        QTest.mouseClick(
            table.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(harness.routes[-1], expected)

    def test_team_cell_click_navigates_to_team_route(self) -> None:
        harness, page = self._make_page()
        table = page._player_table
        delegate = table.view.itemDelegateForColumn(1)
        self.assertIsInstance(delegate, _LinkColumnDelegate)
        row0 = table.model.row_at(0)
        expected = Route("team", team=row0.team_id, season=1)
        _show_page(page)
        before = len(harness.routes)
        rect = table.view.visualRect(table.view.model().index(0, 1))
        QTest.mouseClick(
            table.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(harness.routes[-1], expected)

    def test_prev_next_buttons_navigate_neighbors(self) -> None:
        harness, page = self._make_page()
        self.assertIsNotNone(self.prev_id)
        self.assertIsNotNone(self.next_id)
        self.assertTrue(page._prev_button.isEnabled())
        self.assertTrue(page._next_button.isEnabled())
        page._next_button.click()
        self.assertEqual(harness.routes[-1], Route("match", match=self.next_id))
        page._prev_button.click()
        self.assertEqual(harness.routes[-1], Route("match", match=self.prev_id))

    def test_boundary_neighbors_are_disabled(self) -> None:
        facts = _facts()
        harness, page = _make_detail_page(facts["playoff_first"].match_id)
        self.assertIsNone(facts["playoff_first_prev"])
        self.assertFalse(page._prev_button.isEnabled())
        self.assertTrue(page._next_button.isEnabled())
        harness2, page2 = _make_detail_page(facts["playoff_last"].match_id)
        self.assertIsNone(facts["playoff_last_next"])
        self.assertTrue(page2._prev_button.isEnabled())
        self.assertFalse(page2._next_button.isEnabled())

    def test_route_context_provides_breadcrumb_context(self) -> None:
        harness, page = self._make_page()
        match = self.detail.match
        context = page.route_context()
        self.assertEqual(context["season"], 1)
        self.assertEqual(context["week"], match.week_number)
        self.assertIn(match.home.display_name, context["match_label"])
        self.assertIn(str(match.home_goals), context["match_label"])


# ---------------------------------------------------------------------------
# c. 比赛详情页（未赛赛前页与错误状态）
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class MatchDetailScheduledTests(_PageTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        facts = _facts()
        cls.scheduled: match_queries.MatchRow = facts["scheduled"]
        cls.detail: match_queries.MatchDetail = facts["scheduled_detail"]
        cls.prev_id, cls.next_id = facts["scheduled_neighbors"]
        cls.standings: Dict[int, object] = facts["scheduled_standings"]

    def test_pregame_page_shows_unplayed_state_and_team_summaries(self) -> None:
        harness, page = _make_detail_page(self.scheduled.match_id)
        match = self.detail.match
        self.assertEqual(page._page_stack.currentIndex(), 0)
        self.assertEqual(page._score_label.text(), "未赛")
        self.assertIn("未赛", page._info_label.text())
        self.assertIn(f"第 {match.season_number} 赛季", page._info_label.text())
        # 未赛没有事件与球员数据（引擎未模拟，不虚构）
        self.assertEqual(page._event_labels, [])
        self.assertIsNone(page._player_table)
        # 两队 EntityLink（指向比赛赛季的球队页）
        self.assertIsInstance(page._home_link, EntityLink)
        self.assertEqual(
            page._home_link.route,
            Route("team", team=match.home.team_id, season=2),
        )
        self.assertEqual(
            page._away_link.route,
            Route("team", team=match.away.team_id, season=2),
        )
        # 两队当前赛季摘要（standings_row：赛/胜/平/负/积分）
        for team_id, line in page._summary_lines.items():
            standings = self.standings[team_id]
            expected = (
                f"赛 {standings.played} · 胜 {standings.wins} · 平 {standings.draws}"
                f" · 负 {standings.losses} · 积分 {standings.points} · 排名第 {standings.rank}"
            )
            self.assertEqual(line.text(), expected)
        self.assertEqual(len(page._summary_links), 2)

    def test_summary_team_link_click_navigates_to_team_route(self) -> None:
        harness, page = _make_detail_page(self.scheduled.match_id)
        _show_page(page)
        link = page._summary_links[0]
        before = len(harness.routes)
        QTest.mouseClick(
            link,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            link.rect().center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(harness.routes[-1].name, "team")
        self.assertEqual(harness.routes[-1].int_param("season"), 2)

    def test_pregame_prev_next_navigates_schedule(self) -> None:
        harness, page = _make_detail_page(self.scheduled.match_id)
        self.assertIsNotNone(self.prev_id)
        self.assertIsNotNone(self.next_id)
        page._next_button.click()
        self.assertEqual(harness.routes[-1], Route("match", match=self.next_id))
        page._prev_button.click()
        self.assertEqual(harness.routes[-1], Route("match", match=self.prev_id))

    def test_unknown_match_shows_empty_state(self) -> None:
        harness, page = _make_detail_page("m-no-such-match")
        current = page._page_stack.currentWidget()
        self.assertIsInstance(current, EmptyState)
        self.assertEqual(page._detail, None)
        titles = [label.text() for label in current.findChildren(QLabel)]
        self.assertIn("比赛不存在", titles)
        self.assertEqual(page.route_context(), {})


# ---------------------------------------------------------------------------
# d. 零嵌套纵向滚动 + 截图（1440×860 与 1680×980）
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class MatchScrollAndScreenshotTests(_PageTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        facts = _facts()
        harness, cls.list_page = _make_list_page(Route("matches", season=1))
        _, cls.completed_page = _make_detail_page(facts["mid"].match_id)
        _, cls.scheduled_page = _make_detail_page(facts["scheduled"].match_id)

    PAGES = ("list", "completed_detail", "scheduled_detail")

    def _page_for(self, key: str):
        return {
            "list": self.list_page,
            "completed_detail": self.completed_page,
            "scheduled_detail": self.scheduled_page,
        }[key]

    def test_at_most_one_vertical_scroll_surface_two_sizes(self) -> None:
        """三种页面 × 两种尺寸：至多一个可纵向滚动面，且归属符合页面类型。"""
        for width, height in WINDOW_SIZES:
            for key in self.PAGES:
                with self.subTest(size=f"{width}x{height}", page=key):
                    page = self._page_for(key)
                    _show_page(page, (width, height))
                    surfaces = _scrollable_surfaces(page)
                    self.assertLessEqual(
                        len(surfaces),
                        1,
                        f"{key} 在 {width}x{height} 出现多个纵向滚动面："
                        f"{[type(s).__name__ for s in surfaces]}",
                    )
                    if key == "list":
                        # 列表页主表占满剩余高度，是唯一纵向滚动面
                        self.assertEqual(len(surfaces), 1)
                        self.assertIsInstance(surfaces[0], QTableView)
                        self.assertEqual(surfaces[0], self.list_page._table.view)
                    elif key == "completed_detail":
                        # 已赛详情：外层 QScrollArea 是唯一滚动面；球员表完整展开不滚动
                        self.completed_page._match_tabs.setCurrentIndex(1)
                        QApplication.processEvents()
                        _show_page(page, (width, height))
                        surfaces = _scrollable_surfaces(page)
                        self.assertEqual(len(surfaces), 1)
                        self.assertIsInstance(surfaces[0], QScrollArea)
                        player_view = self.completed_page._player_table.view
                        self.assertEqual(player_view.verticalScrollBar().maximum(), 0)
                    else:  # scheduled_detail：内容较短时允许 0 个滚动面，但不得出现第二个
                        for surface in _visible_scroll_surfaces(page):
                            self.assertIsInstance(surface, QScrollArea)

    def test_list_page_table_fills_page_height(self) -> None:
        """列表页主表纵向占满中央剩余高度（外层无 QScrollArea）。"""
        _show_page(self.list_page, (1440, 860))
        self.assertIsNone(self.list_page.findChild(QScrollArea))
        table = self.list_page._table
        self.assertGreaterEqual(table.view.height(), 600)

    def test_screenshots_saved(self) -> None:
        out_dir = SCREENSHOT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: List[Path] = []
        for width, height in WINDOW_SIZES:
            size_name = f"{width}x{height}"
            shots = (
                ("matches_list", self.list_page),
                ("match_detail", self.completed_page),
                ("match_detail_scheduled", self.scheduled_page),
            )
            for stem, page in shots:
                _show_page(page, (width, height))
                path = out_dir / f"{stem}_{size_name}.png"
                self.assertTrue(page.grab().save(str(path)), f"截图失败：{path}")
                saved.append(path)
        for path in saved:
            self.assertGreater(path.stat().st_size, 0, f"截图为空：{path}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
