"""赛事详情页测试（阶段 5，Agent F1）。

共享夹具（参照 tests/test_page_teams.py 的共享存档模式）：
1. 初始化第 1 赛季并跑完整赛季（第 1 赛季无杯赛：优胜者杯/挑战杯/超级杯均
   "未举办"）；
2. 开启第 2 赛季并跑完整赛季（第 2 赛季有优胜者杯 + 挑战杯，跑完均为
   "已结束"；超级杯自第 3 赛季起举办，第 2 赛季仍"未举办"）。

覆盖内容（对应实施方案 §8.4 与任务规格）：
- 六赛事 overview 状态（两赛季 × 六赛事：S1 杯赛"未举办"、S2 杯赛"已结束"）；
- 联赛积分榜 20 行且与 team_queries 一致（抽样 2 队 rank/points）；
- 杯赛签表行数 / 冠军（S2 优胜者杯）与"本届未举办"空状态（S1）；
- 球员榜榜首 == player_queries 该赛事口径最大值（进球榜抽样比对）；
- 奖项 / 历史页签与查询层一致；
- 链接合同：球队 / 球员 / 比赛路由（行激活 + 单击）；
- 赛事切换下拉与赛季选择器 → navigate 正确路由；
- 页签记忆（save_state / restore_state）；
- 滚动硬规则（§8.2）：1440×860 与 1680×980 两种尺寸 × 全页签滚动面检查；
- 截图输出到 Reviews/ui_audit/phase4/competition_*.png。

运行：QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_page_competition -v
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
        QTabWidget,
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
from football_simulator.queries import base, competition_queries, history_queries, player_queries, team_queries
from football_simulator.ui_v2.components import EmptyState, EntityLink
from football_simulator.ui_v2 import theme
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.competition_page import (
    _AWARD_COLUMNS,
    _LEADER_COLUMNS,
    _STANDINGS_COLUMNS,
    _column_index,
)
from football_simulator.ui_v2.pages.competition_page import CompetitionPage
from football_simulator.ui_v2.pages.entity_page_base import PageContext

from tests.support import create_save, run_season, seeded_provider

SAVE_NAME = "page_competition"
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

_LEAGUE = base.COMPETITION_PREMIER
_WINNERS = base.COMPETITION_WINNERS_CUP


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
    """两个完整赛季：第 1 赛季无杯赛，第 2 赛季有优胜者杯 + 挑战杯。"""
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_page_competition_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())
        create_save(SAVE_NAME)
        run_season(SAVE_NAME)
        create_save(SAVE_NAME)
        run_season(SAVE_NAME)
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
        # 与生产外壳一致应用全局主题（test_page_matches/test_page_players 同惯例），
        # 保证截图与断言所见即产品视觉（深色 chrome）。
        _APP.setStyleSheet(theme.APP_STYLE)
    return _APP


# -- 共享事实（只查询一次） ---------------------------------------------------

_FACTS: Dict[str, object] = {}


def _facts() -> Dict[str, object]:
    if not _FACTS:
        _shared_save()
        with base.open_read_connection(SAVE_NAME) as conn:
            overviews = {
                season: {
                    item.competition.competition_id: item
                    for item in competition_queries.list_competitions(conn, season)
                }
                for season in (1, 2)
            }
            premier1 = competition_queries.get_competition_profile(conn, _LEAGUE, 1)
            winners2 = competition_queries.get_competition_profile(conn, _WINNERS, 2)
            refs_by_name = {ref.display_name: ref for ref in base.load_team_refs(conn)}
            directory1 = player_queries.list_players(conn, 1, competition=_LEAGUE)
            top20 = history_queries.get_competition_history(conn, _LEAGUE)
            _FACTS.update(
                {
                    "overviews": overviews,
                    "premier1": premier1,
                    "winners2": winners2,
                    "refs_by_name": refs_by_name,
                    "directory1": directory1,
                    "top20": top20,
                }
            )
    return _FACTS


# -- 页面工厂 ----------------------------------------------------------------

# 页面实例在本测试进程内保持存活（与生产一致：MainWindow 全程持有页面）。
# 实证：GC 时机拆解已展示的页面会触发 Qt C++ 树的级联析构重入
# （QTableViewWrapper dtor → deleteChildren → delegate 析构 → SIGSEGV），
# 与 delegate 生命周期约定无关；保持引用即可消除该测试期析构路径。
_KEEP_ALIVE: List[Tuple[_Harness, QWidget]] = []


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


def _make_page(
    competition: str, season: int, apply_to_pages: bool = False
) -> Tuple[_Harness, CompetitionPage, Route]:
    harness = _Harness(_shared_save(), apply_to_pages=apply_to_pages)
    page = CompetitionPage(harness.context())
    harness.attach(page)
    _KEEP_ALIVE.append((harness, page))
    route = Route("competition", competition=competition, season=season)
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


def _label_texts(widget: QWidget) -> List[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


# -- 状态摘要与六赛事 overview -------------------------------------------------


class OverviewStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.facts = _facts()
        self.harness = _Harness(_shared_save())
        self.page = CompetitionPage(self.harness.context())

    def test_six_competition_statuses_both_seasons(self) -> None:
        overviews: Dict[int, Dict[str, competition_queries.CompetitionOverview]] = self.facts["overviews"]
        refs_by_name: Dict[str, base.TeamRef] = self.facts["refs_by_name"]
        checked = 0
        for season in (1, 2):
            for competition, overview in overviews[season].items():
                with self.subTest(season=season, competition=competition):
                    route = Route("competition", competition=competition, season=season)
                    self.page.apply_route(route)
                    self.assertEqual(
                        self.page._status_badge.text(),
                        overview.status,
                        f"第 {season} 赛季 {competition} 状态应为 {overview.status}",
                    )
                    self.assertIn(f"已完成 {overview.completed_matches}", self.page._progress_label.text())
                    if overview.total_matches is not None:
                        self.assertIn(f"共 {overview.total_matches} 场", self.page._progress_label.text())
                    # 冠军：已决出 → 可点球队链接；未决出/未举办 → 无链接。
                    if overview.champion:
                        self.assertIsNotNone(self.page._champion_link)
                        assert self.page._champion_link is not None
                        self.assertEqual(self.page._champion_link.text(), overview.champion)
                        self.assertEqual(
                            self.page._champion_link.route,
                            Route(
                                "team",
                                team=refs_by_name[overview.champion].team_id,
                                season=season,
                            ),
                        )
                    else:
                        self.assertIsNone(self.page._champion_link)
                    self.assertEqual(
                        self.page.route_context(),
                        {"competition_name": competition, "season": season},
                    )
                    checked += 1
        self.assertEqual(checked, 12)

    def test_season_one_cups_not_held_and_season_two_cups_finished(self) -> None:
        overviews: Dict[int, Dict[str, competition_queries.CompetitionOverview]] = self.facts["overviews"]
        for competition in (
            base.COMPETITION_WINNERS_CUP,
            base.COMPETITION_CHALLENGE_CUP,
            base.COMPETITION_SUPER_CUP,
        ):
            self.assertEqual(
                overviews[1][competition].status,
                competition_queries.STATUS_NOT_HELD,
                f"第 1 赛季 {competition} 应为未举办",
            )
        self.assertEqual(
            overviews[2][base.COMPETITION_WINNERS_CUP].status,
            competition_queries.STATUS_FINISHED,
        )
        self.assertEqual(
            overviews[2][base.COMPETITION_CHALLENGE_CUP].status,
            competition_queries.STATUS_FINISHED,
        )
        self.assertEqual(
            overviews[2][base.COMPETITION_SUPER_CUP].status,
            competition_queries.STATUS_NOT_HELD,
            "超级杯自第 3 赛季起举办，第 2 赛季应为未举办",
        )

    def test_not_held_cup_shows_empty_states(self) -> None:
        self.page.apply_route(Route("competition", competition=_WINNERS, season=1))
        overview = self.facts["overviews"][1][_WINNERS]  # type: ignore[index]
        self.assertEqual(overview.status, competition_queries.STATUS_NOT_HELD)

        # 概览：单滚动面内是"本届未举办"空状态。
        self.page._tabs.setCurrentIndex(0)
        overview_texts = _label_texts(self.page._overview_scroll.widget())
        self.assertIn("本届未举办", overview_texts)

        # 积分榜/签表：空状态，没有表格。
        self.page._tabs.setCurrentIndex(1)
        self.assertIsNone(self.page._stage_table)
        stage_empties = self.page._stage_container.findChildren(EmptyState)
        self.assertTrue(stage_empties)
        self.assertIn("本届未举办", _label_texts(stage_empties[0]))

        # 赛程与结果：空状态。
        self.page._tabs.setCurrentIndex(2)
        self.assertIs(self.page._matches_stack.currentWidget(), self.page._matches_empty_slot)
        self.assertIn("本届未举办", _label_texts(self.page._matches_empty_slot))

    def test_unknown_competition_shows_empty_state(self) -> None:
        self.page.apply_route(Route("competition", competition="不存在的杯赛", season=1))
        self.assertIs(self.page._stack.currentWidget(), self.page._empty)
        self.assertIsInstance(self.page._empty, EmptyState)
        self.assertIn("未知赛事", _label_texts(self.page._empty))
        self.assertEqual(self.page.route_context(), {})


# -- 积分榜（联赛） -------------------------------------------------------------


class StandingsTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.facts = _facts()

    def test_league_standings_rows_match_team_queries(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1)
        profile: competition_queries.CompetitionProfile = self.facts["premier1"]
        page._tabs.setCurrentIndex(1)
        table = page._stage_table
        self.assertIsNotNone(table)
        assert table is not None
        self.assertEqual(table.model.rowCount(), 20)
        self.assertEqual(table.model.rowCount(), len(profile.standings or ()))

        # 抽样 2 队：名次与积分与 team_queries 一致。
        with base.open_read_connection(SAVE_NAME) as conn:
            for row_index in (0, 9):
                row = table.model.row_at(row_index)
                team_profile = team_queries.get_team_season_profile(conn, row.team_id, 1)
                self.assertEqual(row.team_name, team_profile.identity.display_name)
                self.assertEqual(row.rank, team_profile.standings_row.rank)
                self.assertEqual(row.points, team_profile.standings_row.points)

        # 行激活 → 球队路由。
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            harness.routes[-1],
            Route("team", team=profile.standings[0].team_id, season=1),
        )

    def test_league_team_cell_click_navigates_to_team_route(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1)
        page._tabs.setCurrentIndex(1)
        table = page._stage_table
        assert table is not None
        team_column = _column_index(_STANDINGS_COLUMNS, "team_name")
        row0 = table.model.row_at(0)
        expected = Route("team", team=row0.team_id, season=1)

        _show_page(page)
        before = len(harness.routes)
        rect = table.view.visualRect(table.view.model().index(0, team_column))
        QTest.mouseClick(
            table.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(harness.routes[-1], expected)
        page.hide()


# -- 杯赛签表 ----------------------------------------------------------------


class CupStageTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.facts = _facts()

    def test_winners_cup_stage_rows_and_champion(self) -> None:
        harness, page, route = _make_page(_WINNERS, 2)
        profile: competition_queries.CompetitionProfile = self.facts["winners2"]
        refs_by_name: Dict[str, base.TeamRef] = self.facts["refs_by_name"]
        self.assertTrue(profile.stage_rows, "第 2 赛季优胜者杯应有签表行")
        self.assertTrue(profile.champion)

        # 杯赛签表页签 = 唯一 QScrollArea：小组积分表 + 淘汰树（不再放独立表格）。
        page._tabs.setCurrentIndex(1)
        self.assertIsNone(page._stage_table)
        surfaces = page._stage_container.findChildren(QScrollArea)
        self.assertEqual(len(surfaces), 1)
        texts = _label_texts(page._stage_container)
        self.assertIn("A 组", texts)
        self.assertIn("淘汰赛对局（→ 晋级方；括号为两回合总比分，A=客场进球优势，P=点球大战）", texts)

        # 决赛晋级方 == 冠军，且冠军名出现在页签内容里。
        final_round = next(
            round_block
            for round_block in profile.knockout_rounds
            if round_block.stage == "决赛（次回合）"
        )
        self.assertEqual(final_round.pairs[0].advancing, profile.champion)
        self.assertIn(profile.champion, texts)

        # 状态摘要冠军可点。
        self.assertIsInstance(page._champion_link, EntityLink)
        assert page._champion_link is not None
        self.assertEqual(page._champion_link.text(), profile.champion)
        self.assertEqual(
            page._champion_link.route,
            Route("team", team=refs_by_name[profile.champion].team_id, season=2),
        )

        # 比赛行激活 → 比赛路由（全部比赛在“赛程与结果”页签）。
        page._tabs.setCurrentIndex(2)
        table = page._matches_table
        self.assertEqual(table.model.rowCount(), len(profile.matches))
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            harness.routes[-1],
            Route("match", match=profile.matches[0].match_id),
        )
        _show_page(page)
        page.hide()

    def test_winners_cup_group_tables_and_knockout_tree(self) -> None:
        harness, page, route = _make_page(_WINNERS, 2)
        profile: competition_queries.CompetitionProfile = self.facts["winners2"]

        # 小组积分表：4 组 × 4 队，全部来自已赛小组赛结果推导。
        self.assertEqual(len(profile.cup_groups), 4)
        self.assertEqual(sum(len(group.rows) for group in profile.cup_groups), 16)
        groups_by_name = {group.group_name: group for group in profile.cup_groups}
        self.assertEqual(set(groups_by_name), {"A", "B", "C", "D"})
        for group in profile.cup_groups:
            self.assertEqual([row.rank for row in group.rows], [1, 2, 3, 4])
            self.assertEqual(sum(row.played for row in group.rows), 24)  # 每队 6 场双循环
            # 积分排序链：积分 → 净胜球 → 进球。
            points = [row.points for row in group.rows]
            self.assertEqual(points, sorted(points, reverse=True))

        # 淘汰树：按 6 个轮次标签分组，共 14 场（QF 8 场 + SF 4 场 + F 2 场）。
        self.assertEqual(len(profile.knockout_rounds), 6)
        self.assertEqual(sum(len(round_block.pairs) for round_block in profile.knockout_rounds), 14)
        stages = [round_block.stage for round_block in profile.knockout_rounds]
        self.assertIn("四分之一决赛（首回合）", stages)
        self.assertIn("决赛（次回合）", stages)
        final = next(round_block for round_block in profile.knockout_rounds if round_block.stage == "决赛（次回合）")
        self.assertEqual(len(final.pairs), 1)
        self.assertEqual(final.pairs[0].advancing, profile.champion)

        # 两回合展示规则：首回合不写晋级方；次回合显示晋级方 + 总比分（+ 判定 A/P）。
        qf_leg1 = next(round_block for round_block in profile.knockout_rounds if round_block.stage == "四分之一决赛（首回合）")
        qf_leg2 = next(round_block for round_block in profile.knockout_rounds if round_block.stage == "四分之一决赛（次回合）")
        final_leg1 = next(round_block for round_block in profile.knockout_rounds if round_block.stage == "决赛（首回合）")
        self.assertTrue(all(pair.advancing is None for pair in qf_leg1.pairs))
        self.assertTrue(all(pair.advancing is None for pair in final_leg1.pairs))
        self.assertEqual(len(qf_leg1.pairs), 4)
        self.assertEqual(len(qf_leg2.pairs), 4)
        self.assertTrue(all(pair.advancing for pair in qf_leg2.pairs))
        self.assertTrue(all(pair.aggregate_goals is not None for pair in qf_leg2.pairs))
        for pair in qf_leg2.pairs:
            if pair.decision is not None:
                self.assertIn(pair.decision, {"A", "P"})
        self.assertIsNotNone(final.pairs[0].aggregate_goals)

        # 页签内容：小组表 + 淘汰树渲染在签表表格上方。
        page._tabs.setCurrentIndex(1)
        _show_page(page)
        texts = _label_texts(page._stage_container)
        self.assertIn("A 组", texts)
        self.assertIn("小组积分表（按积分、净胜球、进球排序）", texts)
        self.assertIn("淘汰赛对局（→ 晋级方；括号为两回合总比分，A=客场进球优势，P=点球大战）", texts)
        page.hide()

    def test_playoff_stage_tab_shows_matches_and_champion_bar(self) -> None:
        harness, page, route = _make_page(base.COMPETITION_PLAYOFF, 1)
        with base.open_read_connection(SAVE_NAME) as conn:
            profile = competition_queries.get_competition_profile(conn, base.COMPETITION_PLAYOFF, 1)
        self.assertTrue(profile.champion, "第 1 赛季附加赛应已决出升级成功方")
        page._tabs.setCurrentIndex(1)
        table = page._stage_table
        self.assertIsNotNone(table)
        assert table is not None
        self.assertEqual(table.model.rowCount(), len(profile.matches))

        # 冠军横条含升级成功方链接。
        texts = _label_texts(page._stage_container)
        self.assertIn("升级成功方：", texts)
        links = page._stage_container.findChildren(EntityLink)
        self.assertTrue(links)
        self.assertEqual(
            links[0].route,
            Route(
                "team",
                team=self.facts["refs_by_name"][profile.champion].team_id,  # type: ignore[index]
                season=1,
            ),
        )


# -- 球员榜 / 奖项 / 历史 -------------------------------------------------------


class LeaderboardTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.facts = _facts()

    def test_leaderboard_top_scorer_matches_player_queries(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1)
        page._tabs.setCurrentIndex(3)
        table = page._leader_scorers_table
        self.assertIsNotNone(table)
        assert table is not None
        rows = [table.model.row_at(i) for i in range(table.model.rowCount())]
        self.assertTrue(rows)
        self.assertEqual({row.board for row in rows}, {"射手榜"})
        self.assertTrue(all(row.team_name for row in rows), "榜单行应显示球员所属球队")

        scorers = rows
        self.assertEqual(scorers[0].rank, 1)
        top = scorers[0]

        # player_queries 该赛事口径：榜首进球 == 最大值，且该球员口径一致。
        with base.open_read_connection(SAVE_NAME) as conn:
            directory = player_queries.list_players(conn, 1, competition=_LEAGUE)
        self.assertTrue(directory)
        max_goals = max(row.goals for row in directory)
        self.assertEqual(top.stat_value, max_goals)
        matched = next(row for row in directory if row.player_id == top.player_id)
        self.assertEqual(matched.goals, top.stat_value)

        # 行激活 → 球员路由；球员列单击 → 球员路由。
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            harness.routes[-1],
            Route("player", player=top.player_id, season=1),
        )
        player_column = _column_index(_LEADER_COLUMNS, "player_name")
        _show_page(page)
        before = len(harness.routes)
        rect = table.view.visualRect(table.view.model().index(0, player_column))
        QTest.mouseClick(
            table.view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(len(harness.routes), before + 1)
        self.assertEqual(harness.routes[-1], Route("player", player=top.player_id, season=1))
        page.hide()

    def test_leaderboard_real_only_checkbox_filters_rows(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1)
        page._tabs.setCurrentIndex(3)
        checker = page._make_leader_real_check()
        checker.setChecked(True)

        def _all_rows() -> list:
            return [
                row
                for table in (
                    page._leader_scorers_table,
                    page._leader_assisters_table,
                    page._leader_rated_table,
                )
                for row in (table.model.row_at(i) for i in range(table.model.rowCount()))
            ]

        rows = _all_rows()
        self.assertTrue(rows)
        with base.open_read_connection(SAVE_NAME) as conn:
            real_profile = competition_queries.get_competition_profile(
                conn, _LEAGUE, 1, leaderboards_is_real=True
            )
        real_ids = {
            entry.player.player_id
            for board in (real_profile.leaderboards.top_scorers, real_profile.leaderboards.top_assisters, real_profile.leaderboards.top_rated)
            for entry in board
        }
        self.assertEqual({row.player_id for row in rows}, real_ids)
        self.assertEqual(len(rows), sum(len(board) for board in (
            real_profile.leaderboards.top_scorers,
            real_profile.leaderboards.top_assisters,
            real_profile.leaderboards.top_rated,
        )))

        # 取消勾选：榜单恢复为全部球员（含默认球员）的口径。
        checker.setChecked(False)
        rows_all = _all_rows()
        with base.open_read_connection(SAVE_NAME) as conn:
            profile = competition_queries.get_competition_profile(conn, _LEAGUE, 1)
        self.assertEqual(
            len(rows_all),
            sum(len(board) for board in (
                profile.leaderboards.top_scorers,
                profile.leaderboards.top_assisters,
                profile.leaderboards.top_rated,
            )),
        )

    def test_awards_tab_matches_query(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1)
        profile: competition_queries.CompetitionProfile = self.facts["premier1"]
        self.assertTrue(profile.awards, "第 1 赛季一级联赛应有奖项（射手王/助攻王/MVP）")
        page._tabs.setCurrentIndex(4)
        table = page._awards_table
        self.assertIsNotNone(table)
        assert table is not None
        self.assertEqual(table.model.rowCount(), len(profile.awards))
        types = [table.model.row_at(i).award_type_text for i in range(table.model.rowCount())]
        self.assertEqual(set(types), {"射手王", "助攻王", "MVP"})

        # 行激活 → 球员路由。
        first_award = profile.awards[0]
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            harness.routes[-1],
            Route("player", player=first_award.player.player_id, season=1),
        )
        self.assertEqual(_column_index(_AWARD_COLUMNS, "player_name"), 2)

    def test_history_tab_rows_and_champion_links(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1)
        top20: List[history_queries.CompetitionSeasonLine] = self.facts["top20"]
        refs_by_name: Dict[str, base.TeamRef] = self.facts["refs_by_name"]
        self.assertEqual(len(top20), 2, "两个完整赛季应都有归档")
        page._tabs.setCurrentIndex(5)
        table = page._history_table
        self.assertIsNotNone(table)
        assert table is not None
        self.assertEqual(table.model.rowCount(), len(top20))

        # 第 1 赛季行：冠军与归档 premier_order[0] 一致，链接指向该赛季的球队页。
        season1_line = next(line for line in top20 if line.season_number == 1)
        self.assertTrue(season1_line.champion)
        row1 = next(
            table.model.row_at(i)
            for i in range(table.model.rowCount())
            if table.model.row_at(i).season_number == 1
        )
        self.assertEqual(row1.champion_name, season1_line.champion)
        expected = Route(
            "team", team=refs_by_name[season1_line.champion].team_id, season=1
        )
        self.assertEqual(page._history_champion_route(row1), expected)

        # 行激活 → 该赛季的同一赛事路由。
        table.view.activated.emit(table.view.model().index(0, 0))
        first_season = top20[0].season_number
        self.assertEqual(
            harness.routes[-1],
            Route("competition", competition=_LEAGUE, season=first_season),
        )


# -- 导航：赛事切换与赛季选择器 ---------------------------------------------------


class NavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.facts = _facts()

    def test_competition_switch_navigates_to_new_route(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1, apply_to_pages=True)
        combo = page._competition_combo
        target = combo.findText(_WINNERS)
        self.assertGreaterEqual(target, 0)
        combo.setCurrentIndex(target)
        self.assertEqual(
            harness.routes[-1], Route("competition", competition=_WINNERS, season=1)
        )
        # 外壳应用新路由后，页面按新赛事刷新：第 1 赛季优胜者杯未举办。
        self.assertEqual(page._status_badge.text(), competition_queries.STATUS_NOT_HELD)

    def test_season_selector_navigates_to_new_route(self) -> None:
        harness, page, route = _make_page(_WINNERS, 1, apply_to_pages=True)
        self.assertEqual(page._status_badge.text(), competition_queries.STATUS_NOT_HELD)
        combo = page._season_combo
        target = combo.findData(2)
        self.assertGreaterEqual(target, 0)
        combo.setCurrentIndex(target)
        self.assertEqual(
            harness.routes[-1], Route("competition", competition=_WINNERS, season=2)
        )
        # 外壳应用新路由后，页面按第 2 赛季刷新：优胜者杯已结束且有冠军。
        self.assertEqual(page._status_badge.text(), competition_queries.STATUS_FINISHED)
        self.assertIsNotNone(page._champion_link)


# -- 页签记忆 / 空状态 ---------------------------------------------------------


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.facts = _facts()

    def test_tab_selection_remembered_per_route(self) -> None:
        harness, page, route = _make_page(_LEAGUE, 1)
        page._tabs.setCurrentIndex(3)
        self.assertEqual(harness.states[route.route_key].get("tab"), 3)
        page.apply_route(route)
        self.assertEqual(page._tabs.currentIndex(), 3)

        # 其他路由（其他赛事/赛季）不受影响。
        harness2, page2, route2 = _make_page(_LEAGUE, 2)
        self.assertEqual(page2._tabs.currentIndex(), 0)

    def test_missing_save_shows_empty_state(self) -> None:
        harness = _Harness("不存在的存档")
        page = CompetitionPage(harness.context())
        page.apply_route(Route("competition", competition=_LEAGUE, season=1))
        self.assertIs(page._stack.currentWidget(), page._empty)
        self.assertIn("存档不可用", _label_texts(page._empty))


# -- 滚动硬规则与截图 ---------------------------------------------------------


class ScrollRuleTests(unittest.TestCase):
    def _assert_each_tab_single_scroll_surface(self, page: CompetitionPage) -> None:
        tabs = page._tabs
        for index in range(tabs.count()):
            tab_page = tabs.widget(index)
            sub_tabs = tab_page.findChildren(QTabWidget)
            if sub_tabs:
                # 球员榜拆分为三个子页签：每个子页签恰有一个滚动面。
                sub = sub_tabs[0]
                for sub_index in range(sub.count()):
                    sub.setCurrentIndex(sub_index)
                    QApplication.processEvents()
                    surfaces = _vertical_scroll_surfaces(sub.widget(sub_index))
                    self.assertEqual(
                        len(surfaces),
                        1,
                        f"子页签「{sub.tabText(sub_index)}」应恰有一个纵向滚动面，实际 {len(surfaces)}",
                    )
                continue
            surfaces = _vertical_scroll_surfaces(tab_page)
            self.assertEqual(
                len(surfaces),
                1,
                f"页签「{tabs.tabText(index)}」应恰有一个纵向滚动面，实际 {len(surfaces)}",
            )
        # §8.2 规则 4：只读信息不得使用 QTextEdit/QPlainTextEdit。
        self.assertEqual(page.findChildren(QTextEdit), [])
        self.assertEqual(page.findChildren(QPlainTextEdit), [])

    def _assert_each_tab_at_most_one_scroll_surface(self, page: CompetitionPage) -> None:
        tabs = page._tabs
        for index in range(tabs.count()):
            tab_page = tabs.widget(index)
            sub_tabs = tab_page.findChildren(QTabWidget)
            if sub_tabs:
                sub = sub_tabs[0]
                for sub_index in range(sub.count()):
                    sub.setCurrentIndex(sub_index)
                    QApplication.processEvents()
                    surfaces = _vertical_scroll_surfaces(sub.widget(sub_index))
                    self.assertLessEqual(
                        len(surfaces),
                        1,
                        f"子页签「{sub.tabText(sub_index)}」不得出现嵌套纵向滚动，实际 {len(surfaces)}",
                    )
                continue
            surfaces = _vertical_scroll_surfaces(tab_page)
            self.assertLessEqual(
                len(surfaces),
                1,
                f"页签「{tabs.tabText(index)}」不得出现嵌套纵向滚动，实际 {len(surfaces)}",
            )

    def test_league_tabs_single_scroll_surface_both_sizes(self) -> None:
        _app()
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                harness, page, route = _make_page(_LEAGUE, 1)
                _show_page(page, size)
                self._assert_each_tab_single_scroll_surface(page)
                page.hide()

    def test_cup_tabs_single_scroll_surface_both_sizes(self) -> None:
        _app()
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                harness, page, route = _make_page(_WINNERS, 2)
                _show_page(page, size)
                self._assert_each_tab_single_scroll_surface(page)
                page.hide()

    def test_not_held_cup_has_no_nested_scroll(self) -> None:
        _app()
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                harness, page, route = _make_page(_WINNERS, 1)
                _show_page(page, size)
                self._assert_each_tab_at_most_one_scroll_surface(page)
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

    def test_capture_competition_tabs_both_sizes(self) -> None:
        _app()
        self.facts = _facts()
        produced: List[Path] = []

        # 概览 / 积分榜 / 球员榜：第 1 赛季一级联赛（已结束、榜单齐全）。
        harness, league_page, route = _make_page(_LEAGUE, 1)
        tab_shots = ((0, "overview"), (1, "standings"), (3, "leaderboards"))
        for width, height in WINDOW_SIZES:
            for tab_index, key in tab_shots:
                league_page._tabs.setCurrentIndex(tab_index)
                QApplication.processEvents()
                produced.append(
                    self._capture(
                        league_page, (width, height), f"competition_{key}_{width}x{height}.png"
                    )
                )

        # 签表：第 2 赛季优胜者杯（已结束，签表完整）。
        harness, cup_page, route = _make_page(_WINNERS, 2)
        cup_page._tabs.setCurrentIndex(1)
        QApplication.processEvents()
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(cup_page, (width, height), f"competition_stage_{width}x{height}.png")
            )

        for path in produced:
            self.assertTrue(path.exists(), f"缺少截图：{path}")
            self.assertGreater(path.stat().st_size, 0, f"截图为空：{path}")


if __name__ == "__main__":
    unittest.main()
