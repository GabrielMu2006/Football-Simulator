"""球队目录与球队详情页测试（阶段 4，Agent E3）。

共享夹具（参照 tests/test_query_team.py 的共享存档模式）：
1. 初始化第 1 赛季并跑完整赛季（含转会、归档、升降级）；
2. 开启第 2 赛季并推进 3 周（此时存在"第 1 赛季次级 → 第 2 赛季一级"的升降级球队）。

覆盖内容（对应实施方案 §8.6）：
- 目录页：40 行、分区过滤 20/20、搜索、行激活路由、状态记忆、空状态；
- 详情页：概览与积分榜行一致、阵容 1GK/4DF/3MF/3FW、赛程行数 == fixtures、
  转会行数 == transfers_in + transfers_out、奖项与 awards 表一致、
  赛季历史归档行、升降级分区、链接合同（球员/比赛/球队）、页签记忆；
- 滚动硬规则（§8.2）：1440×860 与 1680×980 两种尺寸下每页签恰一个纵向滚动面；
- 截图输出到 Reviews/ui_audit/phase4/。

运行：QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_page_teams -v
"""

from __future__ import annotations

import atexit
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
        QGraphicsView,
        QListView,
        QLabel,
        QPlainTextEdit,
        QScrollArea,
        QTableView,
        QTableWidget,
        QTextEdit,
        QTreeView,
        QTreeWidget,
        QWidget,
    )

    HAS_PYSIDE6 = True
except ImportError:  # 系统 Python 无 PySide6：整模块跳过
    raise unittest.SkipTest("需要 PySide6（用 .venv-ui-v2/bin/python 运行）")

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.queries import base, team_queries
from football_simulator.queries.team_queries import list_teams
from football_simulator.ui_v2.components import EmptyState, EntityLink
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import PageContext
from football_simulator.ui_v2.pages.team_profile_page import (
    _FIXTURE_COLUMNS,
    _TRANSFER_COLUMNS,
)
from football_simulator.ui_v2.pages.team_profile_page import TeamProfilePage
from football_simulator.ui_v2.pages.teams_page import TeamsPage

from tests.support import create_save, run_season, run_weeks, seeded_provider

SAVE_NAME = "page_teams"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = PROJECT_ROOT / "Reviews" / "ui_audit" / "phase4"
WINDOW_SIZES: Tuple[Tuple[int, int], ...] = ((1440, 860), (1680, 980))

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


def _shared_save() -> str:
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_page_teams_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())
        create_save(SAVE_NAME)
        run_season(SAVE_NAME)
        create_save(SAVE_NAME)
        run_weeks(SAVE_NAME, 3)
        _SHARED["root"] = root
        atexit.register(_teardown_shared)
    else:
        sim_runtime.set_save_root_override(_SHARED["root"])
        sim_state.set_rng_provider(seeded_provider())
    return SAVE_NAME


def _app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


# -- 共享事实（只查询一次） ---------------------------------------------------

_FACTS: Dict[str, object] = {}


def _facts() -> Dict[str, object]:
    if not _FACTS:
        _shared_save()
        with base.open_read_connection(SAVE_NAME) as conn:
            premier1 = team_queries.list_teams(conn, 1, division="一级联赛")
            champion = min(premier1, key=lambda row: row.rank or 10_000)
            division1 = {
                row.team.team_id: row.season_division for row in team_queries.list_teams(conn, 1)
            }
            division2 = {
                row.team.team_id: row.season_division for row in team_queries.list_teams(conn, 2)
            }
            movers = [
                team_id for team_id, division in division1.items() if division2[team_id] != division
            ]
            trade_counts: Dict[str, int] = {}
            for row in conn.execute(
                "SELECT team_a, team_b FROM transfers WHERE season_number = 1"
            ):
                for name in (row["team_a"], row["team_b"]):
                    trade_counts[name] = trade_counts.get(name, 0) + 1
            busiest_trade_team = max(trade_counts, key=lambda name: trade_counts[name])
            busiest_trade_team_id = int(
                conn.execute(
                    "SELECT team_id FROM teams WHERE name = ?", (busiest_trade_team,)
                ).fetchone()["team_id"]
            )
            awards_row = conn.execute(
                """
                SELECT team_name, COUNT(*) AS total FROM awards
                WHERE season_id = (SELECT season_id FROM seasons WHERE season_number = 1)
                GROUP BY team_name ORDER BY total DESC LIMIT 1
                """
            ).fetchone()
            # 附加赛只在次级联赛第 3-6 名之间进行，因此“最多场次”的球队
            # 应在全部分区中取样。
            max_fixture_profile = max(
                (
                    team_queries.get_team_season_profile(conn, row.team.team_id, 1)
                    for row in list_teams(conn, 1)
                ),
                key=lambda profile: len(profile.fixtures),
            )
            _FACTS.update(
                {
                    "champion": champion,
                    "division1": division1,
                    "division2": division2,
                    "movers": movers,
                    "busiest_trade_team_id": busiest_trade_team_id,
                    "awards_team_name": awards_row["team_name"] if awards_row else None,
                    "awards_total": int(awards_row["total"]) if awards_row else 0,
                    "max_fixture_profile": max_fixture_profile,
                }
            )
    return _FACTS


# -- 页面工厂 ----------------------------------------------------------------


class _Harness:
    """手工构造的 PageContext：记录路由与页面状态，可选把路由应用到已注册页面。"""

    def __init__(self, save_name: str, apply_to_pages: bool = False) -> None:
        self.save_name = save_name
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


def _make_directory_page() -> Tuple[_Harness, TeamsPage]:
    harness = _Harness(_shared_save())
    page = TeamsPage(harness.context())
    page.apply_route(Route("teams"))
    return harness, page


def _make_profile_page(
    team_id: int, season: int, apply_to_pages: bool = False
) -> Tuple[_Harness, TeamProfilePage, Route]:
    harness = _Harness(_shared_save(), apply_to_pages=apply_to_pages)
    page = TeamProfilePage(harness.context())
    harness.attach(page)
    route = Route("team", team=team_id, season=season)
    page.apply_route(route)
    return harness, page, route


def _show_page(page: QWidget, size: Tuple[int, int] = (1440, 860)) -> None:
    page.resize(*size)
    page.show()
    QApplication.processEvents()


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


# -- 目录页测试 ---------------------------------------------------------------


class TeamsDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.harness, self.page = _make_directory_page()

    def test_directory_has_40_rows(self) -> None:
        self.assertEqual(self.page._table.model.rowCount(), 40)

    def test_division_filter_20_20(self) -> None:
        self.page._division_combo.setCurrentText("一级联赛")
        self.assertEqual(self.page._table.model.rowCount(), 20)
        rows = [self.page._table.model.row_at(i) for i in range(20)]
        self.assertTrue(all(row.season_division == "一级联赛" for row in rows))

        self.page._division_combo.setCurrentText("次级联赛")
        self.assertEqual(self.page._table.model.rowCount(), 20)
        rows = [self.page._table.model.row_at(i) for i in range(20)]
        self.assertTrue(all(row.season_division == "次级联赛" for row in rows))

    def test_search_filter_matches_query_layer(self) -> None:
        season = self.page._selected_season()
        self.assertIsNotNone(season)
        with base.open_read_connection(SAVE_NAME) as conn:
            all_rows = team_queries.list_teams(conn, int(season))
            needle = all_rows[0].team.display_name[:2]
            expected = team_queries.list_teams(
                conn, int(season), search=needle
            )
        self.assertTrue(expected)

        self.page._search_edit.setText(needle)
        self.page._load_rows()
        self.assertEqual(self.page._table.model.rowCount(), len(expected))
        displayed = [
            self.page._table.model.row_at(i).team_name
            for i in range(self.page._table.model.rowCount())
        ]
        self.assertTrue(all(needle in name for name in displayed))

    def test_row_activation_navigates_to_team_route(self) -> None:
        season = self.page._selected_season()
        source_row = self.page._table.model.row_at(0)
        proxy = self.page._table.view.model()
        self.page._table.view.activated.emit(proxy.index(0, 0))
        self.assertEqual(len(self.harness.routes), 1)
        self.assertEqual(
            self.harness.routes[0], Route("team", team=source_row.team_id, season=season)
        )

    def test_search_without_match_shows_empty_state(self) -> None:
        self.page._search_edit.setText("绝对不存在的球队名XYZ")
        self.page._load_rows()
        self.assertIs(self.page._stack.currentWidget(), self.page._empty)
        self.assertIsInstance(self.page._empty, EmptyState)
        # 空状态页面不得引入额外滚动面。
        self.assertEqual(
            [s for s in _vertical_scroll_surfaces(self.page._empty)],
            [],
        )

    def test_filters_and_season_persist_in_page_state(self) -> None:
        with base.open_read_connection(SAVE_NAME) as conn:
            rows = team_queries.list_teams(conn, int(self.page._selected_season()))
        needle = rows[0].team.display_name[:2]

        self.page._division_combo.setCurrentText("次级联赛")
        self.page._search_edit.setText(needle)
        # 与真实用户路径一致：FilterBar 防抖回调最终调用页面的
        # _on_filters_changed 槽（回调与信号都会到达这里）。
        self.page._on_filters_changed(needle)
        state = self.harness.states["teams"]
        self.assertEqual(state["divisionFilter"], "次级联赛")
        self.assertEqual(state["search"], needle)
        self.assertIn("season", state)

        season_before = self.page._selected_season()
        other_index = (self.page._season_combo.currentIndex() + 1) % self.page._season_combo.count()
        self.page._season_combo.setCurrentIndex(other_index)
        self.assertEqual(self.harness.states["teams"]["season"], self.page._season_combo.currentData())
        self.page._season_combo.setCurrentIndex(
            self.page._season_combo.findData(season_before)
        )

        # 外壳再次进入列表页：筛选与赛季恢复。
        self.page.apply_route(Route("teams"))
        self.assertEqual(self.page._division_combo.currentText(), "次级联赛")
        self.assertEqual(self.page._search_edit.text(), needle)
        self.assertEqual(self.page._selected_season(), season_before)
        with base.open_read_connection(SAVE_NAME) as conn:
            expected = team_queries.list_teams(
                conn, season_before, division="次级联赛", search=needle
            )
        self.assertEqual(self.page._table.model.rowCount(), len(expected))

    def test_directory_has_single_vertical_scroll_surface(self) -> None:
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                _show_page(self.page, size)
                surfaces = _vertical_scroll_surfaces(self.page)
                self.assertEqual(len(surfaces), 1)
                self.assertIsInstance(surfaces[0], QTableView)
                self.page.hide()


# -- 详情页测试 ---------------------------------------------------------------


class TeamProfilePageTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.facts = _facts()
        self.champion = self.facts["champion"]

    def _profile_of(self, team_id: int, season: int) -> team_queries.TeamSeasonProfile:
        with base.open_read_connection(SAVE_NAME) as conn:
            return team_queries.get_team_season_profile(conn, team_id, season)

    # -- 概览 ---------------------------------------------------------------

    def test_overview_matches_standings_row(self) -> None:
        harness, page, route = _make_profile_page(self.champion.team.team_id, 1)
        profile = self._profile_of(self.champion.team.team_id, 1)
        line = profile.standings_row
        metrics = page._overview_metrics
        self.assertEqual(metrics["名次"], f"第 {line.rank} 名")
        self.assertEqual(metrics["已赛"], str(line.played))
        self.assertEqual(metrics["胜"], str(line.wins))
        self.assertEqual(metrics["平"], str(line.draws))
        self.assertEqual(metrics["负"], str(line.losses))
        self.assertEqual(metrics["进球"], str(line.goals_for))
        self.assertEqual(metrics["失球"], str(line.goals_against))
        self.assertEqual(metrics["积分"], str(line.points))
        self.assertEqual(page.route_context(), {"team_name": profile.identity.display_name})
        self.assertEqual(page._division_badge.text(), profile.season_division)
        self.assertIn(f"积分 {line.points}", page._summary_label.text())
        self.assertIn(f"第 {line.rank} 名", page._summary_label.text())

    def test_overview_shows_team_honors_and_form(self) -> None:
        harness, page, route = _make_profile_page(self.champion.team.team_id, 1)
        profile = self._profile_of(self.champion.team.team_id, 1)
        self.assertIn("第 1 名", profile.team_honors)
        texts = [label.text() for label in page._overview_scroll.widget().findChildren(QLabel)]
        for honor in profile.team_honors:
            self.assertIn(honor, texts)
        self.assertIn("本赛季球队荣誉", texts)
        self.assertIn("联赛走势", texts)
        # 第 1 赛季完整：走势应为最近 5 场联赛徽标。
        self.assertTrue(
        any(text.startswith("最近 5 场联赛结果") for text in texts),
        f"概览应包含联赛走势标签，实际：{texts}",
    )

    # -- 阵容 ---------------------------------------------------------------

    def test_squad_tab_shape_and_player_navigation(self) -> None:
        harness, page, route = _make_profile_page(self.champion.team.team_id, 1)
        page._tabs.setCurrentIndex(1)
        table = page._squad_table
        self.assertEqual(table.model.rowCount(), 11)
        counts: Dict[str, int] = {}
        for index in range(11):
            row = table.model.row_at(index)
            counts[row.position] = counts.get(row.position, 0) + 1
        self.assertEqual(counts, {"GK": 1, "DF": 4, "MF": 3, "FW": 3})

        default_rows = [
            table.model.row_at(index)
            for index in range(11)
            if not table.model.row_at(index).player.is_real
        ]
        self.assertTrue(default_rows)
        self.assertTrue(all(row.market_value is None for row in default_rows))

        # 行激活 → 球员路由。
        table.view.activated.emit(table.view.model().index(0, 0))
        expected = Route("player", player=table.model.row_at(0).player.player_id, season=1)
        self.assertEqual(harness.routes[-1], expected)

    # -- 赛程与结果 -----------------------------------------------------------

    def test_fixtures_tab_row_count_matches_query(self) -> None:
        harness, page, route = _make_profile_page(self.champion.team.team_id, 1)
        profile = self._profile_of(self.champion.team.team_id, 1)
        page._tabs.setCurrentIndex(2)
        table = page._fixtures_table
        self.assertEqual(table.model.rowCount(), len(profile.fixtures))
        results = [table.model.row_at(i).result for i in range(table.model.rowCount())]
        # 第 1 赛季完整：全部已赛且只有联赛/附加赛（无杯赛）。
        self.assertTrue(all(result in ("胜", "平", "负") for result in results))
        competitions = {table.model.row_at(i).competition for i in range(table.model.rowCount())}
        self.assertTrue(competitions.issubset({"一级联赛", "升级附加赛"}))

        # 行激活 → 比赛路由。
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            harness.routes[-1], Route("match", match=profile.fixtures[0].match_id)
        )

    def test_fixtures_cover_playoff_participants(self) -> None:
        max_profile: team_queries.TeamSeasonProfile = self.facts["max_fixture_profile"]
        self.assertGreater(len(max_profile.fixtures), 38, "附加赛球队应有 38 场之外的赛程")
        harness, page, route = _make_profile_page(max_profile.identity.team_id, 1)
        page._tabs.setCurrentIndex(2)
        self.assertEqual(
            page._fixtures_table.model.rowCount(), len(max_profile.fixtures)
        )

    def test_opponent_cell_click_navigates_to_team_route(self) -> None:
        from football_simulator.ui_v2.pages.team_profile_page import (
            _LinkColumnDelegate as LinkColumnDelegate,
            _column_index,
        )

        harness, page, route = _make_profile_page(self.champion.team.team_id, 1)
        page._tabs.setCurrentIndex(2)
        table = page._fixtures_table
        opponent_column = _column_index(_FIXTURE_COLUMNS, "opponent")
        self.assertIsInstance(
            table.view.itemDelegateForColumn(opponent_column), LinkColumnDelegate
        )
        row0 = table.model.row_at(0)
        expected = Route("team", team=row0.opponent_team_id, season=1)
        self.assertEqual(page._fixture_opponent_route(row0), expected)

        _show_page(page)
        before = len(harness.routes)
        rect = table.view.visualRect(table.view.model().index(0, opponent_column))
        QTest.mouseClick(
            table.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(harness.routes[-1], expected)
        page.hide()

    # -- 转会 ---------------------------------------------------------------

    def test_transfers_tab_matches_query(self) -> None:
        team_id = int(self.facts["busiest_trade_team_id"])
        harness, page, route = _make_profile_page(team_id, 1)
        profile = self._profile_of(team_id, 1)
        expected = len(profile.transfers_in) + len(profile.transfers_out)
        self.assertGreater(expected, 0)
        page._tabs.setCurrentIndex(4)
        table = page._transfers_table
        self.assertEqual(table.model.rowCount(), expected)
        directions = [table.model.row_at(i).direction for i in range(expected)]
        self.assertEqual(directions.count("转入"), len(profile.transfers_in))
        self.assertEqual(directions.count("转出"), len(profile.transfers_out))

        # 行激活 → 球员路由。
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            harness.routes[-1],
            Route("player", player=table.model.row_at(0).player.player_id, season=1),
        )

    def test_transfer_counterpart_cell_click_navigates_to_team_route(self) -> None:
        from football_simulator.ui_v2.pages.team_profile_page import (
            _LinkColumnDelegate as LinkColumnDelegate,
            _column_index,
        )

        team_id = int(self.facts["busiest_trade_team_id"])
        harness, page, route = _make_profile_page(team_id, 1)
        page._tabs.setCurrentIndex(4)
        table = page._transfers_table
        counterpart_column = _column_index(_TRANSFER_COLUMNS, "counterpart_name")
        self.assertIsInstance(
            table.view.itemDelegateForColumn(counterpart_column), LinkColumnDelegate
        )
        row0 = table.model.row_at(0)
        expected = Route("team", team=row0.counterpart.team_id, season=1)
        self.assertEqual(page._transfer_counterpart_route(row0), expected)

        _show_page(page)
        before = len(harness.routes)
        rect = table.view.visualRect(table.view.model().index(0, counterpart_column))
        QTest.mouseClick(
            table.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(harness.routes[-1], expected)
        page.hide()

    # -- 奖项关联 -------------------------------------------------------------

    def test_awards_tab_matches_awards_table(self) -> None:
        awards_team_name = self.facts["awards_team_name"]
        self.assertIsNotNone(awards_team_name, "夹具应有球队获得个人奖项")
        with base.open_read_connection(SAVE_NAME) as conn:
            awards_team_id = int(
                conn.execute(
                    "SELECT team_id FROM teams WHERE name = ?", (awards_team_name,)
                ).fetchone()["team_id"]
            )
        harness, page, route = _make_profile_page(awards_team_id, 1)
        profile = self._profile_of(awards_team_id, 1)
        self.assertEqual(len(profile.player_awards), int(self.facts["awards_total"]))
        self.assertTrue(profile.player_awards)

        page._tabs.setCurrentIndex(5)
        grid = page._awards_table
        self.assertIsNotNone(grid)
        self.assertEqual(grid.data_row_count, len(profile.player_awards))

        # 球员列是 EntityLink 且路由正确；单击导航。
        award = profile.player_awards[0]
        player_link = grid.widget_at(1, 0)
        self.assertIsInstance(player_link, EntityLink)
        self.assertEqual(
            player_link.route, Route("player", player=award.player.player_id, season=1)
        )
        _show_page(page)
        before = len(harness.routes)
        QTest.mouseClick(
            player_link,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(player_link.width() // 2, player_link.height() // 2),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(
            harness.routes[-1], Route("player", player=award.player.player_id, season=1)
        )
        page.hide()

        if award.competition:
            competition_link = grid.widget_at(1, 2)
            self.assertIsInstance(competition_link, EntityLink)
            self.assertEqual(
                competition_link.route,
                Route("competition", competition=award.competition, season=1),
            )

    # -- 赛季历史 -------------------------------------------------------------

    def test_season_history_tab_matches_archive(self) -> None:
        harness, page, route = _make_profile_page(self.champion.team.team_id, 1)
        page._tabs.setCurrentIndex(3)
        rows = page._history_rows
        with base.open_read_connection(SAVE_NAME) as conn:
            seasons = base.load_seasons(conn)
            archive = team_queries.load_archive(conn, 1)
        self.assertIsNotNone(archive)
        self.assertEqual(len(rows), len(seasons))
        self.assertEqual(page._history_table.data_row_count, len(seasons))

        # 赛季倒序：第一行是第 2 赛季（进行中，无归档）。
        self.assertEqual(rows[0].season_number, 2)
        self.assertFalse(rows[0].has_archive)
        self.assertEqual(rows[0].league_result, "进行中")

        season1_row = next(row for row in rows if row.season_number == 1)
        self.assertTrue(season1_row.has_archive)
        premier_order = list(archive["premier_order"])
        expected_rank = premier_order.index(self.champion.team.display_name) + 1
        self.assertEqual(season1_row.rank, f"第 {expected_rank} 名")
        self.assertEqual(season1_row.level, "一级联赛")
        stats = next(
            item
            for item in archive["team_stats"]
            if item["team_name"] == self.champion.team.display_name
        )
        self.assertEqual(season1_row.league_result, stats["league_result"])
        self.assertEqual(season1_row.honor_points, str(stats["honor_points"]))

    # -- 升降级 ---------------------------------------------------------------

    def test_promotion_relegation_division_per_season(self) -> None:
        movers: List[int] = self.facts["movers"]
        self.assertTrue(movers, "夹具应存在升降级球队")
        team_id = movers[0]
        division1: Dict[int, str] = self.facts["division1"]
        division2: Dict[int, str] = self.facts["division2"]
        self.assertNotEqual(division1[team_id], division2[team_id])

        harness1, page1, route1 = _make_profile_page(team_id, 1)
        self.assertEqual(page1._division_badge.text(), division1[team_id])
        harness2, page2, route2 = _make_profile_page(team_id, 2)
        self.assertEqual(page2._division_badge.text(), division2[team_id])
        # 两个赛季概览积分均与查询层一致。
        profile1 = self._profile_of(team_id, 1)
        profile2 = self._profile_of(team_id, 2)
        self.assertEqual(page1._overview_metrics["积分"], str(profile1.standings_row.points))
        self.assertEqual(page2._overview_metrics["积分"], str(profile2.standings_row.points))

    # -- 空状态 / 页签记忆 / 赛季选择器 -------------------------------------------

    def test_unknown_team_shows_empty_state(self) -> None:
        harness = _Harness(_shared_save())
        page = TeamProfilePage(harness.context())
        page.apply_route(Route("team", team=999999, season=1))
        self.assertIs(page._stack.currentWidget(), page._empty)
        self.assertIsInstance(page._empty, EmptyState)
        self.assertEqual(page.route_context(), {"team_name": ""})

    def test_tab_selection_remembered_per_route(self) -> None:
        harness, page, route = _make_profile_page(self.champion.team.team_id, 1)
        page._tabs.setCurrentIndex(2)
        self.assertEqual(harness.states[route.route_key].get("tab"), 2)
        page.apply_route(route)
        self.assertEqual(page._tabs.currentIndex(), 2)

        # 其他路由（其他球队/赛季）不受影响。
        harness2, page2, route2 = _make_profile_page(self.champion.team.team_id, 2)
        self.assertEqual(page2._tabs.currentIndex(), 0)

    def test_season_selector_navigates_to_new_route(self) -> None:
        harness, page, route = _make_profile_page(
            self.champion.team.team_id, 1, apply_to_pages=True
        )
        combo = page._season_combo
        target = combo.findData(2)
        self.assertGreaterEqual(target, 0)
        combo.setCurrentIndex(target)
        self.assertEqual(
            harness.routes[-1], Route("team", team=self.champion.team.team_id, season=2)
        )
        # 外壳应用新路由后，页面按第 2 赛季数据刷新。
        profile2 = self._profile_of(self.champion.team.team_id, 2)
        self.assertEqual(page._overview_metrics["积分"], str(profile2.standings_row.points))


# -- 滚动硬规则与截图 ---------------------------------------------------------


class ScrollRuleTests(unittest.TestCase):
    def _assert_each_tab_single_scroll_surface(self, page: QWidget) -> None:
        tabs = page._tabs
        for index in range(tabs.count()):
            tab_page = tabs.widget(index)
            surfaces = _vertical_scroll_surfaces(tab_page)
            self.assertEqual(
                len(surfaces),
                1,
                f"页签「{tabs.tabText(index)}」应恰有一个纵向滚动面，实际 {len(surfaces)}",
            )
        # §8.2 规则 4：只读信息不得使用 QTextEdit/QPlainTextEdit。
        self.assertEqual(page.findChildren(QTextEdit), [])
        self.assertEqual(page.findChildren(QPlainTextEdit), [])

    def test_profile_tabs_single_scroll_surface_both_sizes(self) -> None:
        _app()
        facts = _facts()
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                harness, page, route = _make_profile_page(facts["champion"].team.team_id, 1)
                _show_page(page, size)
                self._assert_each_tab_single_scroll_surface(page)
                page.hide()

    def test_directory_single_scroll_surface_both_sizes(self) -> None:
        _app()
        harness, page = _make_directory_page()
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                _show_page(page, size)
                surfaces = _vertical_scroll_surfaces(page)
                self.assertEqual(len(surfaces), 1)
                self.assertIsInstance(surfaces[0], QTableView)
                page.hide()


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

    def test_capture_directory_and_profile_tabs_both_sizes(self) -> None:
        _app()
        facts = _facts()
        produced: List[Path] = []

        harness, directory = _make_directory_page()
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(directory, (width, height), f"teams_dir_{width}x{height}.png")
            )

        champion_team_id = facts["champion"].team.team_id
        harness, page, route = _make_profile_page(champion_team_id, 1)
        tab_keys = ("overview", "squad", "fixtures", "history", "transfers")
        for width, height in WINDOW_SIZES:
            for index, key in enumerate(tab_keys):
                page._tabs.setCurrentIndex(index)
                QApplication.processEvents()
                produced.append(
                    self._capture(
                        page, (width, height), f"team_profile_{key}_{width}x{height}.png"
                    )
                )

        # 奖项页签使用奖项最多的球队，保证截图内容非空。
        awards_team_name = facts["awards_team_name"]
        assert awards_team_name is not None
        with base.open_read_connection(SAVE_NAME) as conn:
            awards_team_id = int(
                conn.execute(
                    "SELECT team_id FROM teams WHERE name = ?", (awards_team_name,)
                ).fetchone()["team_id"]
            )
        awards_harness, awards_page, awards_route = _make_profile_page(awards_team_id, 1)
        awards_page._tabs.setCurrentIndex(5)
        QApplication.processEvents()
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(
                    awards_page, (width, height), f"team_profile_awards_{width}x{height}.png"
                )
            )

        for path in produced:
            self.assertTrue(path.exists(), f"缺少截图：{path}")
            self.assertGreater(path.stat().st_size, 0, f"截图为空：{path}")


if __name__ == "__main__":
    unittest.main()
