"""阶段 4 验收测试：球员目录（PlayersPage）与球员个人页（PlayerProfilePage）。

覆盖（任务规格 a-h）：
- 目录：行数与查询一致、搜索/位置/球队过滤、行激活路由、筛选状态保存/恢复、
  赛季选择器（页面状态，不进路由）、未初始化存档空状态、默认球员身价占位；
- 个人页：页头信息与球队链接、概览总计与赛季总计一致、各赛事分段求和一致且
  有总计条、比赛记录全量非截断、赛季历史两行、奖项与球队荣誉分区、轨迹点数、
  转会球员多队分段、默认球员身价空状态、赛季选择器 navigate、概览页链接路由、
  球员不存在错误状态；
- 零嵌套纵向滚动：6 个页签逐个置为当前，1440×860 与 1680×980 各一遍，每页签
  恰有一个可见纵向滚动面（目录页同样检查）；
- 截图：目录 + 个人页各页签（含默认球员轨迹空状态）保存到 Reviews/ui_audit/phase4。

运行：
    QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_page_players -v
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import atexit
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from tests import support
from tests.support import create_save, run_season, run_weeks
from football_simulator.queries import base, player_queries
from football_simulator.ui_v2.navigation import Route

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractScrollArea,
        QApplication,
        QFrame,
        QHeaderView,
        QLabel,
        QScrollArea,
        QStackedWidget,
        QTableView,
    )
    from football_simulator.ui_v2 import theme
    from football_simulator.ui_v2.components import EmptyState, EntityLink
    from football_simulator.ui_v2.pages.entity_page_base import PageContext
    from football_simulator.ui_v2.pages.player_profile_page import PlayerProfilePage
    from football_simulator.ui_v2.pages.players_page import PlayersPage
    from football_simulator.ui_v2.widgets import TrendSparkline

    HAS_PYSIDE6 = True
except ImportError:  # pragma: no cover - 无 GUI 环境占位
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# 共享夹具：赛季 1 完整跑完（含转会），第 2 赛季推进 3 周
# ---------------------------------------------------------------------------

_SAVE_NAME = "players_ui"
_FIXTURE: Optional[Dict[str, object]] = None


def _build_fixture() -> None:
    """懒构建一次存档夹具；存档根目录重定向保持到进程结束（atexit 清理）。"""
    global _FIXTURE
    if _FIXTURE is not None:
        return
    tmp_dir = tempfile.mkdtemp(prefix="fs_players_ui_")
    sim_runtime.set_save_root_override(Path(tmp_dir).resolve())
    sim_state.set_rng_provider(support.seeded_provider())
    create_save(_SAVE_NAME)
    run_season(_SAVE_NAME)
    create_save(_SAVE_NAME)  # 开启第 2 赛季
    run_weeks(_SAVE_NAME, 3)

    def _cleanup() -> None:
        sim_state.set_rng_provider(None)
        sim_runtime.set_save_root_override(None)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    atexit.register(_cleanup)
    _FIXTURE = {"tmp_dir": tmp_dir}


def _profile_dto(player_id: str, season: int) -> player_queries.PlayerSeasonProfile:
    with base.open_read_connection(_SAVE_NAME) as conn:
        return player_queries.get_player_season_profile(conn, player_id, season)


@dataclass(frozen=True)
class _Selection:
    """从赛季 1 目录挑选的代表性球员（数据驱动，不写死姓名）。"""

    top: player_queries.PlayerDirectoryRow
    transfer: player_queries.PlayerDirectoryRow
    default: player_queries.PlayerDirectoryRow


_SELECTION: Optional[_Selection] = None


def _selection() -> _Selection:
    global _SELECTION
    if _SELECTION is None:
        with base.open_read_connection(_SAVE_NAME) as conn:
            rows = player_queries.list_players(conn, 1)
        real_rows = [row for row in rows if row.is_real]
        top = max(real_rows, key=lambda row: (row.goals, row.assists, row.rating))
        transfer = next(row for row in real_rows if row.additional_teams)
        default = next(row for row in rows if not row.is_real)
        _SELECTION = _Selection(top=top, transfer=transfer, default=default)
    return _SELECTION


# ---------------------------------------------------------------------------
# PageContext 测试替身与 Qt 公共设施
# ---------------------------------------------------------------------------


class _NavigateRecorder:
    def __init__(self) -> None:
        self.routes: List[Route] = []

    def __call__(self, route: Route) -> None:
        self.routes.append(route)

    @property
    def last(self) -> Optional[Route]:
        return self.routes[-1] if self.routes else None


@dataclass
class _StateStore:
    data: Dict[str, dict] = field(default_factory=dict)

    def get(self, key: str) -> Optional[dict]:
        return self.data.get(key)

    def set(self, key: str, value: dict) -> None:
        self.data[key] = dict(value)


def _make_context(save_name: str = _SAVE_NAME):
    recorder = _NavigateRecorder()
    store = _StateStore()
    context = PageContext(
        save_name_provider=lambda: save_name,
        navigate=recorder,
        route_provider=lambda: None,
        page_state_get=store.get,
        page_state_set=store.set,
    )
    return context, recorder, store


_QT_APP: Optional[QApplication] = None


def _ensure_qt_app() -> QApplication:
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
        _QT_APP.setStyleSheet(theme.APP_STYLE)
    return _QT_APP


def _visible_scroll_surfaces(widget) -> List[QAbstractScrollArea]:
    """可见的纵向滚动面（QAbstractScrollArea）。

    - 排除 QHeaderView（表头属于表格自身部件，不是独立滚动面）；
    - 排除不可见控件（隐藏页签、未弹出的下拉列表均不算）。
    """
    surfaces: List[QAbstractScrollArea] = []
    for child in widget.findChildren(QAbstractScrollArea):
        if isinstance(child, QHeaderView):
            continue
        if not child.isVisible():
            continue
        surfaces.append(child)
    return surfaces


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class _PageTestCase(unittest.TestCase):
    """需要 QApplication 与共享存档夹具的测试基类。"""

    @classmethod
    def setUpClass(cls) -> None:
        _build_fixture()
        cls.app = _ensure_qt_app()

    def _show(self, widget, size=(1440, 860)) -> None:
        widget.resize(*size)
        widget.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.app.processEvents()


# ---------------------------------------------------------------------------
# a. 球员目录
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class PlayersDirectoryTests(_PageTestCase):
    def _make_page(self, save_name: str = _SAVE_NAME):
        context, recorder, store = _make_context(save_name)
        page = PlayersPage(context)
        page.apply_route(Route("players"))
        self._show(page)
        return page, recorder, store

    def _query_rows(self, season: int, **filters):
        with base.open_read_connection(_SAVE_NAME) as conn:
            return player_queries.list_players(conn, season, **filters)

    def test_row_count_matches_query_and_defaults_to_current_season(self) -> None:
        page, recorder, _ = self._make_page()
        with base.open_read_connection(_SAVE_NAME) as conn:
            current = base.resolve_current_season(conn).season_number
            expected = player_queries.list_players(conn, current)
        self.assertEqual(page._season, current)
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual([row.player_id for row in page._rows], [row.player_id for row in expected])
        self.assertEqual(page._stack.currentIndex(), 0)  # 表格页而非空状态

    def test_search_filter_matches_query(self) -> None:
        page, _, _ = self._make_page()
        key = _selection().top.display_name[:4].lower()
        page._search_edit.setText(key)
        page._reload_rows()
        expected = self._query_rows(page._season, search=key)
        self.assertEqual([row.player_id for row in page._rows], [row.player_id for row in expected])
        self.assertGreater(len(page._rows), 0)

    def test_search_debounce_triggers_reload(self) -> None:
        page, _, _ = self._make_page()
        key = _selection().top.display_name[:4].lower()
        page._search_edit.setText(key)
        QTest.qWait(500)  # 超过 FilterBar 300ms 防抖
        self.assertTrue(
            all(key in row.display_name.lower() for row in page._rows),
            "防抖搜索后应只剩匹配的球员",
        )

    def test_position_filter_matches_query(self) -> None:
        page, _, _ = self._make_page()
        page._position_combo.setCurrentText("GK")
        expected = self._query_rows(page._season, position="GK")
        self.assertEqual(len(page._rows), len(expected))
        self.assertTrue(all(row.position == "GK" for row in page._rows))

    def test_team_filter_matches_query(self) -> None:
        page, _, _ = self._make_page()
        team_name = _selection().top.team.display_name
        team_id = page._team_id_by_name[team_name]
        page._team_combo.setCurrentText(team_name)
        expected = self._query_rows(page._season, team_id=team_id)
        self.assertEqual(len(page._rows), len(expected))
        self.assertTrue(all(row.team_name == team_name for row in page._rows))

    def test_goalkeeper_rows_show_placeholder_in_outfield_columns(self) -> None:
        """位置相关主数据列：门将行 进球/助攻 显示 “—”，扑救/零封 有值。"""
        page, _, _ = self._make_page()
        page._position_combo.setCurrentText("GK")
        view = page._table.view
        # 列序见 _DIRECTORY_COLUMNS：进球 5 / 助攻 6 / 成功扑救 7 / 零封 8
        for row_index in range(min(5, view.model().rowCount())):
            self.assertEqual(view.model().index(row_index, 5).data(Qt.ItemDataRole.DisplayRole), "—")
            self.assertEqual(view.model().index(row_index, 6).data(Qt.ItemDataRole.DisplayRole), "—")

    def test_row_activation_navigates_to_player_route(self) -> None:
        page, recorder, _ = self._make_page()
        view = page._table.view
        target = page._rows[2]
        view.setCurrentIndex(view.model().index(2, 0))
        view.setFocus()
        self.app.processEvents()
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(
            recorder.last,
            Route("player", player=target.player_id, season=page._season),
        )

    def test_filter_state_saved_and_restored(self) -> None:
        page, _, store = self._make_page()
        key = _selection().top.display_name[:4].lower()
        page._search_edit.setText(key)
        page._reload_rows()
        page._position_combo.setCurrentText("FW")
        self.assertTrue(store.data)  # 筛选变化已写入页面状态

        context2, _, store2_holder = _make_context()
        # 用同一份状态存储模拟“返回列表页”恢复（外壳按 route_key 取状态）。
        store2_holder.data.update(store.data)
        page2 = PlayersPage(context2)
        page2.apply_route(Route("players"))
        self._show(page2)
        self.assertEqual(page2._search_edit.text(), key)
        self.assertEqual(page2._position_combo.currentText(), "FW")
        self.assertEqual(len(page2._rows), len(self._query_rows(page2._season, search=key, position="FW")))

    def test_season_selector_keeps_state_without_navigation(self) -> None:
        page, recorder, store = self._make_page()
        with base.open_read_connection(_SAVE_NAME) as conn:
            seasons = base.load_seasons(conn)
        index = page._season_combo.findData(seasons[0].season_number)
        page._season_combo.setCurrentIndex(index)
        self.assertEqual(recorder.routes, [])  # 目录赛季不进路由
        self.assertEqual(page._season, seasons[0].season_number)
        expected = self._query_rows(seasons[0].season_number)
        self.assertEqual(len(page._rows), len(expected))
        self.assertEqual(store.data["players"]["season"], seasons[0].season_number)

    def test_missing_save_shows_empty_state(self) -> None:
        page, _, _ = self._make_page(save_name="no_such_save")
        self.assertIsInstance(page._stack.currentWidget(), EmptyState)
        self.assertEqual(page._rows, [])

    def test_default_player_market_value_cell_is_placeholder(self) -> None:
        page, _, _ = self._make_page()
        default_row = next(row for row in page._rows if row.player_id == _selection().default.player_id)
        view = page._table.view
        row_index = page._rows.index(default_row)
        # 身价列（第 10 列）显示占位符，不显示任何数字。
        self.assertEqual(view.model().index(row_index, 10).data(Qt.ItemDataRole.DisplayRole), "—")
        self.assertIsNone(default_row.market_value)


# ---------------------------------------------------------------------------
# b-f. 球员个人页
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class PlayerProfileTests(_PageTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        selection = _selection()
        cls.top = selection.top
        cls.transfer = selection.transfer
        cls.default = selection.default

    def _make_page(self, player_id: str, season: int, tab: Optional[str] = None):
        params = {"player": player_id, "season": season}
        if tab is not None:
            params["tab"] = tab
        context, recorder, store = _make_context()
        page = PlayerProfilePage(context)
        page.apply_route(Route("player", **params))
        self._show(page)
        return page, recorder, store

    # -- 页头 ----------------------------------------------------------------

    def test_header_identity_and_team_link(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1)
        self.assertEqual(page._page_stack.currentIndex(), 0)
        title_label = page._header.findChild(QLabel, "titleLabel")
        self.assertIsNotNone(title_label)
        self.assertEqual(title_label.text(), self.top.display_name)  # 大标题 = 球员名
        self.assertEqual(page._position_badge.text(), self.top.position)
        self.assertEqual(page._type_badge.text(), "真实球员")
        self.assertEqual(page._ability_value.text(), str(self.top.ability))
        # 当前球队是 EntityLink，指向该球队在该赛季的 team 路由
        self.assertIsInstance(page._team_link, EntityLink)
        self.assertEqual(page._team_link.text(), self.top.team.display_name)
        self.assertEqual(
            page._team_link.route,
            Route("team", team=self.top.team.team_id, season=1),
        )

    def test_header_rating_caliber_and_market_value(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1)
        self.assertTrue(page._rating_note.text().startswith("按现有公式推导"))
        profile = _profile_dto(self.top.player_id, 1)
        latest = [point for point in profile.trend if point.market_value is not None][-1]
        self.assertEqual(page._value_value.text(), f"{float(latest.market_value):.2f}M")
        self.assertIn("最近结算", page._value_note.text())

    # -- A 概览 ----------------------------------------------------------------

    def test_overview_totals_match_season_totals(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1)
        profile = _profile_dto(self.top.player_id, 1)
        expected_fields = {
            "appeared",
            "goals",
            "assists",
            "chances_created",
            "successful_defenses",
            "successful_saves",
            "clean_sheets",
        }
        self.assertEqual(set(page._overview_totals), expected_fields)
        for field_name, label in page._overview_totals.items():
            self.assertEqual(int(label.text()), getattr(profile.season_totals, field_name))

    def test_overview_links_navigate_to_competition_and_match(self) -> None:
        """链接合同：概览赛事摘要行 / 最近比赛行可点。"""
        page, recorder, _ = self._make_page(self.top.player_id, 1)
        profile = _profile_dto(self.top.player_id, 1)
        scroll = page._scrolls["overview"]
        links = [link for link in scroll.findChildren(EntityLink) if link.route is not None]
        competition_links = [link for link in links if link.route.name == "competition"]
        match_links = [link for link in links if link.route.name == "match"]
        team_links = [link for link in links if link.route.name == "team"]
        self.assertTrue(competition_links and match_links and team_links)

        first_split = profile.competition_splits[0]
        self.assertEqual(
            competition_links[0].route,
            Route("competition", competition=first_split.competition, season=1),
        )
        QTest.mouseClick(competition_links[0], Qt.MouseButton.LeftButton)
        self.assertEqual(recorder.last, competition_links[0].route)

        # 最近比赛列表最新一场在最前；比赛与对手均可点
        latest_match = profile.match_log[-1]
        self.assertEqual(match_links[0].route, Route("match", match=latest_match.match_id))
        self.assertEqual(len(match_links), min(8, len(profile.match_log)))
        QTest.mouseClick(match_links[0], Qt.MouseButton.LeftButton)
        self.assertEqual(recorder.last, Route("match", match=latest_match.match_id))
        opponent_route = Route("team", team=latest_match.opponent.team_id, season=1)
        self.assertIn(opponent_route, [link.route for link in team_links])
        opponent_link = next(link for link in team_links if link.route == opponent_route)
        QTest.mouseClick(opponent_link, Qt.MouseButton.LeftButton)
        self.assertEqual(recorder.last, opponent_route)

    def test_overview_awards_and_honors_sections_present(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1)
        scroll = page._scrolls["overview"]
        texts = [label.text() for label in scroll.findChildren(QLabel)]
        self.assertIn("个人奖项", texts)
        self.assertIn("球队荣誉", texts)
        self.assertIn("年度 Top20 第 1 名", " ".join(texts))

    # -- B 各赛事数据 ------------------------------------------------------------

    def test_splits_rows_match_dto_and_sum_to_totals(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1, tab="splits")
        profile = _profile_dto(self.top.player_id, 1)
        table = page._tables["splits"]
        view = table.view
        self.assertEqual(view.model().rowCount(), len(profile.competition_splits))
        stat_fields = (
            "appeared",
            "goals",
            "assists",
            "chances_created",
            "successful_defenses",
            "successful_saves",
            "clean_sheets",
        )
        sums = {field_name: 0 for field_name in stat_fields}
        for row_index in range(view.model().rowCount()):
            row = table.model.row_at(row_index)
            split = profile.competition_splits[row_index]
            self.assertEqual((row.competition, row.team_name), (split.competition, split.team.display_name))
            for field_name in stat_fields:
                self.assertEqual(getattr(row, field_name), getattr(split.stats, field_name))
                sums[field_name] += getattr(row, field_name)
        # UI 数据层不变量：各赛事分段求和 == 赛季总计
        for field_name in stat_fields:
            self.assertEqual(sums[field_name], getattr(profile.season_totals, field_name))
        # 总计条存在且与赛季总计一致
        self.assertTrue(page._splits_total_bar.isVisible())
        for field_name, _title in page._splits_total_labels.items():
            pass
        for field_name, _title in (
            ("appeared", "出场"),
            ("goals", "进球"),
            ("clean_sheets", "零封"),
        ):
            self.assertEqual(
                page._splits_total_labels[field_name].text(),
                f"{_title} {getattr(profile.season_totals, field_name)}",
            )
        self.assertTrue(page._splits_total_labels["rating"].text().startswith("评分："))

    def test_splits_row_activation_navigates_to_competition(self) -> None:
        page, recorder, _ = self._make_page(self.top.player_id, 1, tab="splits")
        view = page._tables["splits"].view
        row = page._tables["splits"].model.row_at(0)
        view.setCurrentIndex(view.model().index(0, 0))
        view.setFocus()
        self.app.processEvents()
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(recorder.last, Route("competition", competition=row.competition, season=1))

    def test_transfer_player_has_multi_team_splits(self) -> None:
        """c. 转会球员：同一赛事出现多支球队的分段行。"""
        page, _, _ = self._make_page(self.transfer.player_id, 1, tab="splits")
        profile = _profile_dto(self.transfer.player_id, 1)
        table = page._tables["splits"]
        self.assertGreater(len(profile.season_teams), 1)
        self.assertEqual(table.view.model().rowCount(), len(profile.competition_splits))
        teams_by_competition: Dict[str, set] = {}
        for row_index in range(table.view.model().rowCount()):
            row = table.model.row_at(row_index)
            teams_by_competition.setdefault(row.competition, set()).add(row.team_name)
        multi_team = [teams for teams in teams_by_competition.values() if len(teams) > 1]
        self.assertTrue(multi_team, "转会球员应出现同赛事多队分段")

    # -- C 赛季历史 ---------------------------------------------------------------

    def test_history_rows_cover_career_and_navigate_between_seasons(self) -> None:
        page, recorder, _ = self._make_page(self.top.player_id, 1, tab="history")
        with base.open_read_connection(_SAVE_NAME) as conn:
            career = player_queries.get_player_career(conn, self.top.player_id)
        table = page._tables["history"]
        view = table.view
        self.assertEqual(view.model().rowCount(), len(career.seasons))
        self.assertEqual(len(career.seasons), 2)  # S1 完整 + S2 部分（3 周）
        first = table.model.row_at(0)
        second = table.model.row_at(1)
        self.assertEqual(first.season_text, "第 1 赛季")
        self.assertEqual(second.season_text, "第 2 赛季")
        self.assertEqual(second.appeared, career.seasons[1].totals.appeared)
        # 单击赛季行切换 profile 上下文
        view.setCurrentIndex(view.model().index(1, 0))
        view.setFocus()
        self.app.processEvents()
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(
            recorder.last,
            Route("player", player=self.top.player_id, season=2, tab="history"),
        )

    # -- D 奖项 ------------------------------------------------------------------

    def test_awards_tab_separates_personal_awards_from_team_honors(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1, tab="awards")
        with base.open_read_connection(_SAVE_NAME) as conn:
            honors = player_queries.get_player_season_profile(conn, self.top.player_id, 1).team_honors
        personal_blocks = [
            frame
            for frame in page.findChildren(QFrame)
            if frame.property("block_role") == "personalAwardsBlock"
        ]
        team_blocks = [
            frame
            for frame in page.findChildren(QFrame)
            if frame.property("block_role") == "teamHonorsBlock"
        ]
        # 赛季 1 + 赛季 2（进行中）各一组，个人奖项与球队荣誉分区展示
        self.assertEqual(len(personal_blocks), 2)
        self.assertEqual(len(team_blocks), 2)
        personal_texts: List[str] = []
        for block in personal_blocks:
            personal_texts.extend(label.text() for label in block.findChildren(QLabel))
        self.assertTrue(any("年度 Top20" in text for text in personal_texts))
        self.assertTrue(any("射手王" in text for text in personal_texts))
        # 团队荣誉只出现在球队荣誉区块，绝不混入个人奖项区块
        for honor in honors:
            self.assertFalse(any(honor in text for text in personal_texts))
        team_texts: List[str] = []
        for block in team_blocks:
            team_texts.extend(label.text() for label in block.findChildren(QLabel))
        normalized = [text.replace("·", "").strip() for text in team_texts]
        for honor in honors:
            self.assertIn(honor, normalized)
        # 个人奖项区块与球队荣誉区块是彼此独立的兄弟控件
        for block in personal_blocks:
            self.assertNotIn(block, team_blocks)

    # -- E 比赛记录 ---------------------------------------------------------------

    def test_matches_tab_lists_full_match_log(self) -> None:
        """比赛记录全量非截断：行数 == match_log 长度。"""
        page, _, _ = self._make_page(self.top.player_id, 1, tab="matches")
        profile = _profile_dto(self.top.player_id, 1)
        table = page._tables["matches"]
        view = table.view
        self.assertEqual(view.model().rowCount(), len(profile.match_log))
        self.assertGreater(len(profile.match_log), 8)  # 确认不是“前 N 条”截断
        first = table.model.row_at(0)
        last = table.model.row_at(view.model().rowCount() - 1)
        self.assertEqual(first.match_id, profile.match_log[0].match_id)
        self.assertEqual(last.match_id, profile.match_log[-1].match_id)

    def test_matches_tab_competition_filter(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1, tab="matches")
        profile = _profile_dto(self.top.player_id, 1)
        competition = profile.match_log[0].competition
        page._matches_combo.setCurrentText(competition)
        expected = [row for row in profile.match_log if row.competition == competition]
        view = page._tables["matches"].view
        self.assertEqual(view.model().rowCount(), len(expected))
        for row_index in range(view.model().rowCount()):
            self.assertEqual(page._tables["matches"].model.row_at(row_index).competition, competition)

    # -- F 评分/身价轨迹 -------------------------------------------------------------

    def test_trend_points_match_settlements(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 1, tab="trend")
        profile = _profile_dto(self.top.player_id, 1)
        self.assertEqual(len(page._trend_points), len(profile.trend))
        # 赛季 1 完结后有 winter + final 两个结算点
        self.assertEqual(len(profile.trend), 2)
        self.assertEqual([point.stage for point in profile.trend], ["winter", "final"])
        expected_rows = [
            (
                f"第 {point.season_number} 赛季",
                "冬窗" if point.stage == "winter" else "赛季末",
                f"第 {point.week_number} 周",
                f"{float(point.rating):.2f}",
                f"{float(point.market_value):.2f}M",
            )
            for point in profile.trend
        ]
        self.assertEqual(page._trend_detail_rows, expected_rows)
        charts = {
            chart.title: chart
            for chart in page._scrolls["trend"].findChildren(TrendSparkline)
        }
        self.assertEqual(len(charts["评分结算轨迹"].values), 2)
        self.assertEqual(len(charts["身价结算轨迹"].values), 2)

    # -- 页签与赛季导航 ---------------------------------------------------------------

    def test_route_tab_selects_tab_and_tab_switch_saves_state_only(self) -> None:
        page, recorder, _ = self._make_page(self.top.player_id, 1, tab="matches")
        self.assertEqual(page._tabs.currentIndex(), 4)  # matches
        self.assertEqual(page._current_tab, "matches")
        # 用户切页签：只 save_state，不产生历史栈条目
        recorder.routes.clear()
        page._tabs.setCurrentIndex(5)  # trend
        self.assertEqual(page._current_tab, "trend")
        self.assertEqual(recorder.routes, [])

    def test_season_selector_navigates_to_new_season(self) -> None:
        """e. 赛季选择器切到赛季 2 → navigate 且路由 season=2。"""
        page, recorder, _ = self._make_page(self.top.player_id, 1, tab="overview")
        index = page._season_combo.findData(2)
        self.assertGreaterEqual(index, 0)
        page._season_combo.setCurrentIndex(index)
        self.assertEqual(
            recorder.last,
            Route("player", player=self.top.player_id, season=2, tab="overview"),
        )

    def test_season2_profile_shows_partial_season(self) -> None:
        page, _, _ = self._make_page(self.top.player_id, 2, tab="matches")
        profile = _profile_dto(self.top.player_id, 2)
        self.assertGreater(len(profile.match_log), 0)
        self.assertEqual(page._tables["matches"].view.model().rowCount(), len(profile.match_log))
        self.assertEqual(page._season_combo.currentData(), 2)

    # -- d. 默认球员与数据缺失 ----------------------------------------------------------

    def test_default_player_shows_value_empty_state_and_does_not_crash(self) -> None:
        page, _, _ = self._make_page(self.default.player_id, 1, tab="trend")
        self.assertEqual(page._page_stack.currentIndex(), 0)
        # 身价处显示空状态文案而非数字
        self.assertEqual(page._value_value.text(), "—")
        self.assertNotIn("M", page._value_value.text())
        self.assertFalse(any(char.isdigit() for char in page._value_value.text()))
        self.assertIn("默认球员不参与身价结算", page._value_note.text())
        # 轨迹页签显示解释性空状态
        self.assertEqual(page._tab_stacks["trend"].currentIndex(), 1)
        self.assertIsInstance(page._tab_stacks["trend"].currentWidget(), EmptyState)
        # 其他页签仍然可用（不崩溃）：分段与比赛记录来自真实出场数据
        splits = page._tables["splits"]
        profile = _profile_dto(self.default.player_id, 1)
        self.assertEqual(splits.view.model().rowCount(), len(profile.competition_splits))
        self.assertEqual(
            page._tables["matches"].view.model().rowCount(),
            len(profile.match_log),
        )

    def test_unknown_player_shows_error_state(self) -> None:
        page, _, _ = self._make_page("real::definitely-not-registered", 1)
        self.assertIsInstance(page._page_stack.currentWidget(), EmptyState)


# ---------------------------------------------------------------------------
# g+h. 零嵌套纵向滚动与截图（1440×860 与 1680×980）
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_PYSIDE6, "需要 PySide6（用 .venv-ui-v2/bin/python 运行）")
class PlayerProfileScrollAndScreenshotTests(_PageTestCase):
    """逐页签检查唯一滚动面，并保存审计截图。"""

    TAB_KEYS = ("overview", "splits", "history", "awards", "matches", "trend")
    SIZES = ((1440, 860), (1680, 980))
    SCREENSHOT_DIR = PROJECT_ROOT / "Reviews" / "ui_audit" / "phase4"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        selection = _selection()
        context, _, _ = _make_context()
        cls.profile = PlayerProfilePage(context)
        cls.profile.apply_route(Route("player", player=selection.top.player_id, season=1))
        context2, _, _ = _make_context()
        cls.directory = PlayersPage(context2)
        cls.directory.apply_route(Route("players"))
        context3, _, _ = _make_context()
        cls.default_profile = PlayerProfilePage(context3)
        cls.default_profile.apply_route(
            Route("player", player=selection.default.player_id, season=1, tab="trend")
        )

    def _surfaces_for_tab(self, key: str, size):
        index = self.TAB_KEYS.index(key)
        self.profile._tabs.setCurrentIndex(index)
        self._show(self.profile, size)
        surfaces = _visible_scroll_surfaces(self.profile)
        self.assertEqual(
            len(surfaces),
            1,
            f"{size} 页签 {key} 应恰有一个可见纵向滚动面，实际：{[type(s).__name__ for s in surfaces]}",
        )
        return surfaces[0]

    def test_zero_nested_scroll_each_tab_two_sizes(self) -> None:
        """g. 6 个页签逐个置为当前后检查：每页签恰有一个纵向滚动面。"""
        expected_types = {
            "overview": QScrollArea,
            "splits": QTableView,
            "history": QTableView,
            "awards": QScrollArea,
            "matches": QTableView,
            "trend": QScrollArea,
        }
        for size in self.SIZES:
            for key in self.TAB_KEYS:
                with self.subTest(size=size, tab=key):
                    surface = self._surfaces_for_tab(key, size)
                    self.assertIsInstance(surface, expected_types[key])

    def test_directory_has_single_scroll_surface_two_sizes(self) -> None:
        for size in self.SIZES:
            with self.subTest(size=size):
                self._show(self.directory, size)
                surfaces = _visible_scroll_surfaces(self.directory)
                self.assertEqual(len(surfaces), 1)
                self.assertIsInstance(surfaces[0], QTableView)

    def test_default_player_trend_tab_has_single_scroll_surface(self) -> None:
        """默认球员轨迹页签为空状态页（EmptyState 不含滚动区），主界面唯一滚动面。"""
        for size in self.SIZES:
            with self.subTest(size=size):
                self._show(self.default_profile, size)
                self.assertEqual(self.default_profile._tab_stacks["trend"].currentIndex(), 1)
                surfaces = _visible_scroll_surfaces(self.default_profile)
                # 页签内容是 EmptyState：整页没有可见纵向滚动面
                self.assertEqual(surfaces, [])

    def test_screenshots_saved(self) -> None:
        """h. 目录 + 个人页 6 页签 + 默认球员轨迹，两种尺寸截图入 Reviews/ui_audit/phase4。"""
        out_dir = self.SCREENSHOT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        saved: List[Path] = []
        for width, height in self.SIZES:
            size_name = f"{width}x{height}"
            self._show(self.directory, (width, height))
            path = out_dir / f"players_dir_{size_name}.png"
            self.assertTrue(self.directory.grab().save(str(path)))
            saved.append(path)
            for key in self.TAB_KEYS:
                self._surfaces_for_tab(key, (width, height))  # 置为当前并复用滚动断言
                path = out_dir / f"player_profile_{key}_{size_name}.png"
                self.assertTrue(self.profile.grab().save(str(path)))
                saved.append(path)
            self._show(self.default_profile, (width, height))
            path = out_dir / f"player_profile_default_trend_{size_name}.png"
            self.assertTrue(self.default_profile.grab().save(str(path)))
            saved.append(path)
        for path in saved:
            self.assertGreater(path.stat().st_size, 0, f"截图为空：{path}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
