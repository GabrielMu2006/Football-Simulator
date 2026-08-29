"""历史与荣誉 / 存档管理页测试（阶段 5，Agent F4）。

共享夹具（参照 tests/test_page_dashboard_weekly_season.py 的多存档模式，固定随机源）：
- 存档 A（hist_a）：完整跑完第 1 赛季（有归档）→ 开启第 2 赛季推进 3 周；
- 存档 B（hist_b）：仅建档未初始化（目录内没有 save.sqlite3）；
- 存档 C（hist_c）：仅建档未初始化，供"初始化赛季"写流程测试；
- 存档 D（hist_d）：连续两个完整赛季（两个归档）→ 赛季选择器导航。

覆盖内容：
- 历史页（§8.8）：赛季选择器只含有归档的赛季并经 navigate 换路由、
  无参默认最新已归档赛季、赛季总览冠军/Top20 前三/赛季入口与
  ``get_season_archive_detail`` 一致且链接路由正确（球队 = 稳定 team_id、
  球员 = ``real::<slug>`` 稳定 ID）、最终排名（名次以归档为准 + 联赛统计
  联接）、个人奖项（Top20 全 20 行 + 赛事个人奖网格）、球队荣誉表、
  结算轨迹完整行数 + 筛选、页签记忆；
- 存档页（§8.8 写流程）：列表含全部存档与初始化状态、当前存档标记、
  新建存档（合法名 → 列表更新 + request_save_reload；非法名 → ValueError
  行内提示合法规则）、删除存档（QMessageBox 确认/取消）、未初始化存档
  "初始化赛季" → service.initialize + request_save_reload、无 service 只读；
- 滚动硬规则（§8.2）：历史页每页签恰一个纵向滚动面、存档页单外层滚动，
  1440×860 与 1680×980 两种尺寸；
- 截图输出到 Reviews/ui_audit/phase4/（history_* / saves_*）。

运行：QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_page_history_saves -v
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractScrollArea,
        QApplication,
        QComboBox,
        QLabel,
        QLineEdit,
        QListView,
        QGraphicsView,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
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
from football_simulator.queries import base, competition_queries, history_queries
from football_simulator.ui_v2.components import EntityLink, EntityTable
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import PageContext
from football_simulator.ui_v2.pages.history_page import HistoryPage
from football_simulator.ui_v2.pages.saves_page import SavesPage
from football_simulator.ui_v2.services import SimulatorUIService

from tests.support import create_save, run_season, run_weeks, seeded_provider

SAVE_A = "hist_a"  # 第 1 赛季完整归档 + 第 2 赛季 3 周
SAVE_B = "hist_b"  # 仅建档未初始化
SAVE_C = "hist_c"  # 仅建档未初始化（初始化写流程测试用）
SAVE_D = "hist_d"  # 两个已归档赛季（赛季选择器）

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
    # 恢复夹具开始前的 current_save.txt（service.create_save/initialize 会写它）。
    previous = _SHARED.get("current_save_backup")
    current_file = sim_runtime.current_save_path()
    try:
        if previous is None:
            current_file.unlink(missing_ok=True)
        else:
            current_file.parent.mkdir(parents=True, exist_ok=True)
            current_file.write_text(previous, encoding="utf-8")
    except OSError:
        pass
    if root is not None:
        shutil.rmtree(str(root), ignore_errors=True)


def _shared_root() -> Path:
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_page_history_saves_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())
        current_file = sim_runtime.current_save_path()
        _SHARED["current_save_backup"] = (
            current_file.read_text(encoding="utf-8") if current_file.exists() else None
        )
        service = SimulatorUIService()
        # 存档 A：完整第 1 赛季（归档）→ 开启第 2 赛季推进 3 周。
        create_save(SAVE_A)
        run_season(SAVE_A)
        create_save(SAVE_A)
        run_weeks(SAVE_A, 3)
        # 存档 B / C：仅建档（目录 + config.json），没有 save.sqlite3。
        service.create_save(SAVE_B)
        service.create_save(SAVE_C)
        # 存档 D：两个完整赛季（两个归档）。
        create_save(SAVE_D)
        run_season(SAVE_D)
        create_save(SAVE_D)
        run_season(SAVE_D)
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
    return _APP


# -- 页面工厂 ----------------------------------------------------------------


# 页面实例在本测试进程内保持存活（与生产一致：MainWindow 全程持有页面）。
# 实证（与 F1 赛事页同类）：GC 已展示过的页面会触发 Qt C++ 树级联析构重入
# （QTableViewWrapper dtor → deleteChildren → delegate 析构 → SIGSEGV），
# 与 delegate 生命周期约定无关；保持引用即可消除该测试期析构路径。
_KEEP_ALIVE: List[Tuple[_Harness, QWidget]] = []


class _Harness:
    """手工构造的 PageContext：记录路由、页面状态与 request_save_reload。"""

    def __init__(self, save_name: str, service: Optional[SimulatorUIService] = None, apply_to_pages: bool = False) -> None:
        self.save_name = save_name
        self.service = service
        self.routes: List[Route] = []
        self.states: Dict[str, Dict[str, object]] = {}
        self.reloads: List[str] = []
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
            request_save_reload=self._request_reload,
        )

    def attach(self, page: QWidget) -> None:
        self.pages.append(page)

    def _navigate(self, route: Route) -> None:
        self.routes.append(route)
        self.current = route
        if self.apply_to_pages:
            for page in self.pages:
                page.apply_route(route)

    def _request_reload(self, save_name: str) -> None:
        self.reloads.append(save_name)

    def _state_get(self, key: str) -> Optional[Dict[str, object]]:
        state = self.states.get(key)
        return dict(state) if state else None

    def _state_set(self, key: str, value: Dict[str, object]) -> None:
        self.states[key] = dict(value)


def _make_history(
    save_name: str,
    season: Optional[int] = None,
    service: Optional[SimulatorUIService] = None,
    apply_to_pages: bool = False,
    harness: Optional[_Harness] = None,
) -> Tuple[_Harness, HistoryPage]:
    _shared_root()
    if harness is None:
        harness = _Harness(save_name, service=service, apply_to_pages=apply_to_pages)
    page = HistoryPage(harness.context())
    harness.attach(page)
    # 页面实例在本测试进程内保持存活（与生产一致：MainWindow 全程持有页面）。
    # 实证：GC 已展示过的页面会触发 Qt C++ 树级联析构重入（QTableViewWrapper
    # dtor → deleteChildren → delegate 析构 → SIGSEGV），保持引用即可消除。
    _KEEP_ALIVE.append((harness, page))
    route = Route("history") if season is None else Route("history", season=season)
    page.apply_route(route)
    return harness, page


_UNSET = object()  # 哨兵：区分“未传 service”（默认真实服务）与显式 service=None（只读）


def _make_saves(service: Optional[SimulatorUIService] = _UNSET) -> Tuple[_Harness, SavesPage]:
    _shared_root()
    harness = _Harness(SAVE_A, service=SimulatorUIService() if service is _UNSET else service)
    page = SavesPage(harness.context())
    harness.attach(page)
    _KEEP_ALIVE.append((harness, page))
    page.apply_route(Route("saves"))
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


def _archive_detail(save_name: str, season: int) -> history_queries.SeasonArchiveDetail:
    with base.open_read_connection(save_name) as conn:
        return history_queries.get_season_archive_detail(conn, season)


def _team_ids(save_name: str) -> Dict[str, int]:
    with base.open_read_connection(save_name) as conn:
        return {str(row[0]): int(row[1]) for row in conn.execute("SELECT name, team_id FROM teams")}


# -- 历史页：赛季选择器与空状态 -------------------------------------------------


class HistorySelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()

    def test_default_route_uses_latest_archived_season_and_archived_only_selector(self) -> None:
        harness, page = _make_history(SAVE_A)
        # 无参 → 最新已归档赛季（第 1 赛季；第 2 赛季进行中、无归档，不得出现）。
        self.assertEqual(page._season, 1)
        combo = page._season_combo
        self.assertIsNotNone(combo)
        self.assertEqual(combo.count(), 1)
        self.assertIn("第 1 赛季", combo.itemText(0))
        self.assertIn("已归档", combo.itemText(0))

    def test_season_selector_navigates_to_new_route(self) -> None:
        harness, page = _make_history(SAVE_D, apply_to_pages=True)
        self.assertEqual(page._season, 2)
        combo = page._season_combo
        self.assertEqual(combo.count(), 2)
        combo.setCurrentIndex(0)  # 切到第 1 赛季
        self.assertEqual(harness.routes[-1], Route("history", season=1))
        # 外壳应用新路由后页面按第 1 赛季刷新。
        self.assertEqual(page._season, 1)

    def test_unarchived_season_shows_explicit_empty_state(self) -> None:
        harness, page = _make_history(SAVE_A, season=2)
        self.assertIs(page._stack.currentWidget(), page._empty)
        titles = [label.text() for label in page._empty.findChildren(QLabel)]
        self.assertIn("第 2 赛季还没有赛季归档", titles)

    def test_uninitialized_save_shows_empty_state(self) -> None:
        harness, page = _make_history(SAVE_B)
        self.assertIs(page._stack.currentWidget(), page._empty)
        titles = [label.text() for label in page._empty.findChildren(QLabel)]
        self.assertTrue(any("存档" in title for title in titles))


# -- 历史页：数据一致性与链接路由（存档 A 第 1 赛季） -----------------------------


class HistoryPageTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.detail = _archive_detail(SAVE_A, 1)
        self.team_ids = _team_ids(SAVE_A)
        self.harness, self.page = _make_history(SAVE_A, season=1)

    # -- 赛季总览 ------------------------------------------------------------

    def test_overview_champions_match_detail_with_team_links(self) -> None:
        page = self.page
        detail = self.detail
        expected_champions: List[Optional[str]] = [
            detail.premier_order[0].team.display_name if detail.premier_order else None,
            detail.second_order[0].team.display_name if detail.second_order else None,
            detail.cup_champions.winners_cup,
            detail.cup_champions.challenge_cup,
            detail.cup_champions.super_cup,
        ]
        resolvable = [name for name in expected_champions if name and name in self.team_ids]
        # 总览页冠军区有 5 行标题，可解析的冠军都是球队链接。
        texts = [label.text() for label in page._content.findChildren(QLabel)]
        for title in ("一级联赛冠军", "次级联赛冠军", "优胜者杯冠军", "挑战杯冠军", "超级杯冠军"):
            self.assertIn(title, texts)
        team_routes = [link.route for link in page._champion_links]
        self.assertEqual(len(team_routes), len(resolvable))
        for name in resolvable:
            expected = Route("team", team=self.team_ids[name], season=1)
            self.assertIn(expected, team_routes)

    def test_overview_top3_and_season_entry_links(self) -> None:
        page = self.page
        detail = self.detail
        top3 = detail.top20[:3]
        self.assertEqual(len(page._top3_links), len(top3))
        for link, line in zip(page._top3_links, top3):
            self.assertTrue(line.player.player_id.startswith("real::"))
            self.assertEqual(
                link.route,
                Route("player", player=line.player.player_id, season=1),
            )
            self.assertEqual(link.text(), line.label)
        self.assertIsNotNone(page._season_entry_link)
        self.assertEqual(
            page._season_entry_link.route,
            Route("season_overview", season=1),
        )
        _show_page(page)
        _click_link(page._season_entry_link)
        self.assertEqual(self.harness.routes[-1], Route("season_overview", season=1))
        page.hide()

    # -- 最终排名 ------------------------------------------------------------

    def test_standings_match_archive_order_and_league_stats(self) -> None:
        page = self.page
        detail = self.detail
        with base.open_read_connection(SAVE_A) as conn:
            season_id = base.season_id_for(conn, 1)
            premier = competition_queries.league_standings_rows(conn, season_id, 1, "premier")
            second = competition_queries.league_standings_rows(conn, season_id, 1, "second")
        stats_by_name = {row.team_name: row for row in [*premier, *second]}

        # 默认级别 = 一级联赛：名次/球队与归档 premier_order 完全一致。
        combo = page._division_combo
        self.assertEqual(combo.currentText(), "一级联赛")
        premier_rows = page._standings_rows["一级联赛"]
        self.assertEqual([row.team_name for row in premier_rows], [ref.team.display_name for ref in detail.premier_order])
        self.assertEqual([row.rank for row in premier_rows], [ref.rank for ref in detail.premier_order])
        for row in premier_rows:
            stats = stats_by_name[row.team_name]
            self.assertEqual(row.played, stats.played)
            self.assertEqual(row.wins, stats.wins)
            self.assertEqual(row.draws, stats.draws)
            self.assertEqual(row.losses, stats.losses)
            self.assertEqual(row.goals_for, stats.goals_for)
            self.assertEqual(row.goals_against, stats.goals_against)
            self.assertEqual(row.points, stats.points)

        # 切换级别 → 次级联赛。
        combo.setCurrentIndex(1)
        second_rows = page._standings_rows["次级联赛"]
        self.assertEqual([row.team_name for row in second_rows], [ref.team.display_name for ref in detail.second_order])
        # 表格行数与切换后的数据集一致。
        self.assertEqual(page._standings_table.model.rowCount(), len(second_rows))

    def test_standings_row_activation_navigates_to_team(self) -> None:
        page = self.page
        champion = self.detail.premier_order[0]
        table = page._standings_table
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(
            self.harness.routes[-1],
            Route("team", team=champion.team.team_id, season=1),
        )

    # -- 个人奖项 ------------------------------------------------------------

    def test_top20_full_rows_match_detail_with_stable_player_routes(self) -> None:
        page = self.page
        detail = self.detail
        self.assertEqual(len(page._top20_rows), 20)
        self.assertEqual(len(detail.top20), 20)
        self.assertEqual(page._top20_table.model.rowCount(), 20)
        for row, line in zip(page._top20_rows, detail.top20):
            self.assertEqual(row.rank, line.rank)
            self.assertEqual(row.player_id, line.player.player_id)
            self.assertTrue(row.player_id.startswith("real::"))
            self.assertEqual(row.player_id, base.canonical_player_id_for_name(row.player_name))
            self.assertEqual(row.team_name, line.team_name)
            self.assertEqual(row.score, line.score)
        # 行激活 → 稳定球员路由。
        first = detail.top20[0]
        page._top20_table.view.activated.emit(page._top20_table.view.model().index(0, 0))
        self.assertEqual(
            self.harness.routes[-1],
            Route("player", player=first.player.player_id, season=1),
        )

    def test_top20_rating_and_value_use_final_settlement(self) -> None:
        page = self.page
        final_points = {
            point.player.player_id: point
            for point in self.detail.player_settlement_points
            if point.stage == "赛季末"
        }
        matched = 0
        for row in page._top20_rows:
            point = final_points.get(row.player_id)
            if point is None:
                self.assertIsNone(row.rating)
                self.assertIsNone(row.market_value)
            else:
                self.assertEqual(row.rating, point.season_rating)
                self.assertEqual(row.market_value, point.market_value)
                matched += 1
        self.assertGreater(matched, 0, "Top20 前列球员应能联接到赛季末结算点")

    def test_awards_grid_matches_detail_with_player_and_competition_links(self) -> None:
        page = self.page
        detail = self.detail
        player_routes = [link.route for link in page._award_links if link.route.name == "player"]
        competition_routes = [link.route for link in page._award_links if link.route.name == "competition"]
        self.assertTrue(player_routes)
        self.assertTrue(competition_routes)
        for line in detail.competition_awards:
            expected_player = Route("player", player=line.player.player_id, season=1)
            self.assertIn(expected_player, player_routes)
            expected_competition = Route("competition", competition=line.competition, season=1)
            self.assertIn(expected_competition, competition_routes)

    # -- 球队荣誉 ------------------------------------------------------------

    def test_honor_table_matches_detail_with_team_routes(self) -> None:
        page = self.page
        detail = self.detail
        model = page._honor_table.model
        self.assertEqual(model.rowCount(), len(detail.team_honor_table))
        rows = [model.row_at(index) for index in range(model.rowCount())]
        by_name = {row.team_name: row for row in rows}
        for line in detail.team_honor_table:
            row = by_name[line.team_name]
            self.assertEqual(row.division, line.division)
            self.assertEqual(row.league_result, line.league_result)
            self.assertEqual(row.honor_points, line.honor_points)
            self.assertEqual(row.total_titles, line.total_titles)
        # 一级联赛冠军行的联赛结果为“第 1 名”，行激活 → 球队路由。
        champion_name = detail.premier_order[0].team.display_name
        self.assertEqual(by_name[champion_name].league_result, "第 1 名")
        page._honor_table.view.activated.emit(page._honor_table.view.model().index(0, 0))
        self.assertEqual(
            self.harness.routes[-1],
            Route("team", team=self.team_ids[champion_name], season=1),
        )

    # -- 结算轨迹 ------------------------------------------------------------

    def test_settlement_full_rows_with_filter(self) -> None:
        page = self.page
        detail = self.detail
        total = len(detail.player_settlement_points)
        self.assertGreater(total, 0)
        # 默认完整呈现（不截断为前 N 条）。
        self.assertEqual(page._settlement_table.model.rowCount(), total)
        # 筛选输入过滤球员/球队。
        needle = detail.player_settlement_points[0].player.display_name[:2]
        page._on_settlement_search(needle)
        filtered_rows = [
            page._settlement_table.model.row_at(index)
            for index in range(page._settlement_table.model.rowCount())
        ]
        self.assertTrue(filtered_rows)
        self.assertLessEqual(len(filtered_rows), total)
        for row in filtered_rows:
            self.assertTrue(
                needle.lower() in row.player_name.lower() or needle.lower() in row.team_name.lower(),
                f"筛选结果 {row.player_name}/{row.team_name} 不包含 {needle}",
            )
        # 清空筛选 → 恢复完整行数。
        page._on_settlement_search("")
        self.assertEqual(page._settlement_table.model.rowCount(), total)
        # 行激活 → 稳定球员路由。
        first_point = detail.player_settlement_points[0]
        page._settlement_table.view.activated.emit(page._settlement_table.view.model().index(0, 0))
        self.assertEqual(
            self.harness.routes[-1],
            Route("player", player=first_point.player.player_id, season=1),
        )

    # -- 页签记忆 ------------------------------------------------------------

    def test_tab_and_division_state_remembered_across_routes(self) -> None:
        harness = _Harness(SAVE_A)
        page = HistoryPage(harness.context())
        harness.attach(page)
        _KEEP_ALIVE.append((harness, page))
        page.apply_route(Route("history", season=1))
        page._tabs.setCurrentIndex(2)
        page._division_combo.setCurrentIndex(1)  # 次级联赛
        self.assertEqual(harness.states["history?season=1"].get("tab"), 2)
        self.assertEqual(harness.states["history?season=1"].get("standingsDivision"), "次级联赛")
        # 新页面同路由恢复页签与级别。
        page2 = HistoryPage(harness.context())
        page2.apply_route(Route("history", season=1))
        self.assertEqual(page2._tabs.currentIndex(), 2)
        self.assertEqual(page2._division_combo.currentText(), "次级联赛")


# -- 存档页（写流程） -----------------------------------------------------------


class SavesPageTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.service = SimulatorUIService()
        self.harness, self.page = _make_saves(self.service)

    def _rows(self) -> Dict[str, Dict[str, QWidget]]:
        return self.page._save_rows

    def test_create_valid_name_updates_list_and_requests_reload(self) -> None:
        page = self.page
        input_box: QLineEdit = page._create_input
        input_box.setText("hist_new")
        page._create_button.click()
        self.assertIn("hist_new", self.service.available_saves())
        self.assertTrue(page._create_status.text().startswith("已创建存档 hist_new"))
        self.assertEqual(input_box.text(), "")
        self.assertIn("hist_new", page._save_rows)
        self.assertEqual(self.harness.reloads[-1], "hist_new")

    def test_create_invalid_name_shows_inline_rule_and_no_reload(self) -> None:
        page = self.page
        before = set(self.service.available_saves())
        page._create_input.setText("../bad name!")
        page._create_button.click()
        self.assertIn("存档名不合法", page._create_status.text())
        self.assertIn("存档名只能", page._create_status.text())
        self.assertEqual(set(self.service.available_saves()), before)
        self.assertEqual(self.harness.reloads, [])

    def test_delete_cancelled_keeps_save(self) -> None:
        page = self.page
        with patch(
            "football_simulator.ui_v2.pages.saves_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            page._save_rows[SAVE_B]["delete"].click()
        self.assertIn(SAVE_B, self.service.available_saves())
        self.assertIn("已取消删除", page._status_label.text())

    def test_delete_confirmed_updates_list_and_requests_reload(self) -> None:
        page = self.page
        self.service.create_save("hist_tmp")
        page.refresh()
        self.assertIn("hist_tmp", page._save_rows)
        with patch(
            "football_simulator.ui_v2.pages.saves_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            page._save_rows["hist_tmp"]["delete"].click()
        self.assertNotIn("hist_tmp", self.service.available_saves())
        self.assertNotIn("hist_tmp", page._save_rows)
        self.assertIn("已删除存档 hist_tmp", page._status_label.text())
        self.assertEqual(self.harness.reloads[-1], self.service.current_save_name())

    def test_initialize_uninitialized_save_requests_reload(self) -> None:
        page = self.page
        row = page._save_rows[SAVE_C]
        self.assertIn("未初始化", row["state"].text())
        self.assertTrue(row["initialize"].isEnabled())
        row["initialize"].click()
        self.assertTrue(base.database_path(SAVE_C).exists())
        self.assertEqual(self.harness.reloads[-1], SAVE_C)
        page.refresh()
        self.assertIn("已初始化", page._save_rows[SAVE_C]["state"].text())
        self.assertFalse(page._save_rows[SAVE_C]["initialize"].isVisibleTo(page._save_rows[SAVE_C]["row"]))

    def test_list_rows_current_marker_and_initialized_state(self) -> None:
        page = self.page
        saves = set(self.service.available_saves())
        self.assertLessEqual({SAVE_A, SAVE_B, SAVE_C, SAVE_D}, saves)
        self.assertEqual(set(page._save_rows), saves)
        current = self.service.current_save_name()
        self.assertIn(current, page._save_rows)
        self.assertIn("当前存档", page._save_rows[current]["name"].text())
        self.assertIn("已初始化", page._save_rows[SAVE_A]["state"].text())
        self.assertIn("未初始化", page._save_rows[SAVE_B]["state"].text())
        # 当前存档的“打开”也可用；未初始化存档提供“初始化赛季”。
        self.assertTrue(page._save_rows[SAVE_A]["open"].isEnabled())
        self.assertTrue(page._save_rows[SAVE_B]["initialize"].isEnabled())
        self.assertFalse(page._save_rows[SAVE_A]["initialize"].isVisibleTo(page._save_rows[SAVE_A]["row"]))

    def test_open_button_requests_save_reload(self) -> None:
        page = self.page
        page._save_rows[SAVE_A]["open"].click()
        self.assertEqual(self.harness.reloads[-1], SAVE_A)

    def test_readonly_without_service(self) -> None:
        harness, page = _make_saves(service=None)
        for button in page.findChildren(QPushButton):
            self.assertFalse(button.isEnabled(), f"{button.text()} 应禁用")
        self.assertEqual(page._save_rows, {})
        texts = [label.text() for label in page._content.findChildren(QLabel)]
        self.assertTrue(any("未启用写服务" in text for text in texts))
        self.assertIn("不兼容", "".join(texts))

    def test_single_scroll_surface_both_sizes(self) -> None:
        page = self.page
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                _show_page(page, size)
                scroll_areas = page.findChildren(QScrollArea)
                self.assertEqual(len(scroll_areas), 1)
                self.assertIs(scroll_areas[0], page._scroll)
                surfaces = _vertical_scroll_surfaces(page)
                self.assertEqual(len(surfaces), 1)
                self.assertEqual(page.findChildren(QTextEdit), [])
                self.assertEqual(page.findChildren(QPlainTextEdit), [])
                page.hide()


# -- 历史页：滚动硬规则 ---------------------------------------------------------


class HistoryScrollRuleTests(unittest.TestCase):
    def _assert_each_tab_single_scroll_surface(self, page: HistoryPage) -> None:
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

    def test_each_tab_single_scroll_surface_both_sizes(self) -> None:
        _app()
        _shared_root()
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                harness, page = _make_history(SAVE_A, season=1)
                _show_page(page, size)
                self._assert_each_tab_single_scroll_surface(page)
                page.hide()

    def test_two_season_page_each_tab_single_scroll_surface_both_sizes(self) -> None:
        _app()
        _shared_root()
        for size in WINDOW_SIZES:
            with self.subTest(size=size):
                harness, page = _make_history(SAVE_D, season=1)
                _show_page(page, size)
                self._assert_each_tab_single_scroll_surface(page)
                page.hide()


# -- 截图（关键状态 × 两种尺寸） --------------------------------------------------


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

    def test_capture_history_and_saves_states_both_sizes(self) -> None:
        _app()
        _shared_root()
        produced: List[Path] = []

        # 历史页 5 个页签（存档 A 第 1 赛季，归档完整）。
        harness, history = _make_history(SAVE_A, season=1)
        tab_names = ("overview", "standings", "awards", "honors", "settlement")
        for tab_index, tab_name in enumerate(tab_names):
            history._tabs.setCurrentIndex(tab_index)
            for width, height in WINDOW_SIZES:
                produced.append(
                    self._capture(history, (width, height), f"history_{tab_name}_{width}x{height}.png")
                )

        # 存档页 4 种状态：列表 / 非法名提示 / 新建成功 / 初始化。
        saves_service = SimulatorUIService()
        saves_harness = _Harness(SAVE_A, service=saves_service)
        saves = SavesPage(saves_harness.context())
        saves_harness.attach(saves)
        _KEEP_ALIVE.append((saves_harness, saves))
        saves.apply_route(Route("saves"))
        for width, height in WINDOW_SIZES:
            produced.append(self._capture(saves, (width, height), f"saves_list_{width}x{height}.png"))

        saves._create_input.setText("bad/../name!")
        saves._create_button.click()
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(saves, (width, height), f"saves_invalid_name_{width}x{height}.png")
            )

        saves._create_input.setText("hist_shot_new")
        saves._create_button.click()
        for width, height in WINDOW_SIZES:
            produced.append(self._capture(saves, (width, height), f"saves_created_{width}x{height}.png"))

        saves.refresh()
        if SAVE_D in saves._save_rows and saves._save_rows[SAVE_D]["initialize"].isEnabled():
            saves._save_rows[SAVE_D]["initialize"].click()
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(saves, (width, height), f"saves_initialized_{width}x{height}.png")
            )

        for path in produced:
            self.assertTrue(path.exists(), f"缺少截图：{path}")
            self.assertGreater(path.stat().st_size, 0, f"截图为空：{path}")


if __name__ == "__main__":
    unittest.main()
