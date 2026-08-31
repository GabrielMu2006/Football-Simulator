"""阶段 4 验收测试：路由驱动的应用外壳（MainWindow，Agent D2）。

运行（在项目根目录）::

    QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_main_window_shell -v

覆盖（对应实施方案 §7.1/§8.1/§8.2 验收要点）：

- 侧栏点击 → Router 路由 + 页面切换，侧栏高亮跟随 Router；
- 后退/前进按钮的状态与行为；
- 实体路由（真实 match_id/team_id/player_id）切换到延迟加载页面；并行页面
  尚未落地时出现"页面迁移中"占位也算通过（两种情况都容忍）；
- 面包屑随路由更新、可点击项导航；
- 全局搜索：输入球员名 → 结果 → 选中导航；
- 赛事 hub 页签随 competition 路由切换；
- 存档切换 → 页面状态清空 + 实体详情路由回主页；
- "处理待办(N)"数量徽标（第 25 周后的转会审核场景）；
- 模拟下一周后自动导航 weekly_report；
- init/simulate 防重复提交（计数 stub + 重入）；

测试存档全部位于临时目录（``tests.support`` 的 override 方式），不触碰项目
``saves/``；``current_save.txt``（service 会写入）在清理时原样恢复。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

    HAS_PYSIDE6 = True
except ImportError:  # 系统 Python 无 PySide6：整模块跳过
    HAS_PYSIDE6 = False
    raise unittest.SkipTest("需要 PySide6（用 .venv-ui-v2/bin/python 运行）")

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.queries import (
    list_matches,
    list_players,
    open_read_connection,
    resolve_current_season,
)
from football_simulator.queries.base import load_team_refs
from football_simulator.ui_v2 import main_window as main_window_module
from football_simulator.ui_v2.components import EmptyState, EntityLink
from football_simulator.ui_v2.main_window import MainWindow
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase
from football_simulator.ui_v2.services import SimulatorUIService
def _wait_simulate_done(window, timeout_s: float = 15.0) -> None:
    """等待后台模拟线程完成。

    必须用 time.sleep（释放 GIL）+ processEvents 组合：QTest.qWait 这类
    不释放 GIL 的等待会让 worker 的 Python 代码饿死（生产环境中 Qt 事件
    循环空闲时释放 GIL，不存在该问题）。
    """
    import time as _time

    from PySide6.QtWidgets import QApplication as _QApp

    deadline = _time.time() + timeout_s
    while _time.time() < deadline and window._simulate_in_progress:
        _QApp.processEvents()
        _time.sleep(0.02)


from tests import support

SAVE_A = "shell_save_a"
SAVE_B = "shell_save_b"
SAVE_GUARD = "shell_save_guard"

EXPECTED_NAV_KEYS = {
    "dashboard",
    "season_overview",
    "matches",
    "competition",
    "teams",
    "players",
    "transfers",
    "draft",
    "history",
    "saves",
}


class _CountingService:
    """SimulatorUIService 计数包装：统计 init/simulate 调用并支持重入钩子。"""

    def __init__(self, inner: SimulatorUIService) -> None:
        self._inner = inner
        self.init_calls = 0
        self.simulate_calls = 0
        self.on_initialize = None
        self.on_simulate = None

    def __getattr__(self, name: str):  # noqa: D105 - 其余调用透传
        return getattr(self._inner, name)

    def initialize(self, save_name: str, force: bool = False):
        self.init_calls += 1
        if self.on_initialize is not None:
            self.on_initialize()
        return self._inner.initialize(save_name, force=force)

    def simulate_week(self, save_name: str):
        self.simulate_calls += 1
        if self.on_simulate is not None:
            self.on_simulate()
        return self._inner.simulate_week(save_name)


class MainWindowShellTests(unittest.TestCase):
    """offscreen 构造 MainWindow 并驱动 Router/侧栏/搜索/存档流程。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="fs_shell_")
        self._tmp_path = Path(self._tmp_dir).resolve()
        sim_runtime.set_save_root_override(self._tmp_path)
        sim_state.set_rng_provider(support.seeded_provider())
        self._backup_current_save_file()
        # 弹窗（QMessageBox）在 offscreen 下会模态阻塞，测试期间整体替换。
        self._msg_patcher = mock.patch.object(main_window_module, "QMessageBox")
        self._msg_patcher.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        self._msg_patcher.stop()
        self._restore_current_save_file()
        sim_state.set_rng_provider(None)
        sim_runtime.set_save_root_override(None)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _backup_current_save_file(self) -> None:
        # service.load_state 会写 <项目根>/current_save.txt（不受存档根 override
        # 影响），测试结束后恢复原内容。
        self._current_save_file = sim_runtime.current_save_path()
        self._current_save_existed = self._current_save_file.exists()
        self._current_save_content = (
            self._current_save_file.read_text(encoding="utf-8") if self._current_save_existed else None
        )

    def _restore_current_save_file(self) -> None:
        if self._current_save_existed:
            self._current_save_file.write_text(self._current_save_content or "", encoding="utf-8")
        elif self._current_save_file.exists():
            self._current_save_file.unlink()

    # -- fixture ---------------------------------------------------------

    def _build_saves(self, weeks_a: int = 2) -> None:
        support.create_save(SAVE_A)
        support.run_weeks(SAVE_A, weeks_a)
        support.create_save(SAVE_B)

    def _make_window(
        self,
        service=None,
        save_name: str = SAVE_A,
        weeks_a: int = 2,
        build: bool = True,
    ) -> MainWindow:
        if build:
            self._build_saves(weeks_a)
        if service is None:
            service = SimulatorUIService()
        service.load_state(save_name)  # 写入"当前存档"，决定窗口启动加载目标
        window = MainWindow(service)
        self.addCleanup(window.close)
        window.show()
        QTest.qWait(10)
        return window

    def _nav_row(self, window: MainWindow, key: str) -> int:
        for index in range(window.nav_list.count()):
            item = window.nav_list.item(index)
            if item.data(Qt.UserRole) == key:
                return index
        raise AssertionError(f"侧栏缺少导航项：{key}")

    def _click_nav_row(self, window: MainWindow, key: str) -> None:
        row = self._nav_row(window, key)
        rect = window.nav_list.visualItemRect(window.nav_list.item(row))
        QTest.mouseClick(window.nav_list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())

    def _breadcrumb_labels(self, window: MainWindow) -> list[str]:
        return [label.text() for label in window.breadcrumb_bar.findChildren(QLabel)]

    # -- 侧栏与路由 -------------------------------------------------------

    def test_sidebar_shape_converges_to_ten_items(self) -> None:
        window = self._make_window()
        self.assertEqual(window.nav_list.count(), 10)
        keys = {window.nav_list.item(i).data(Qt.UserRole) for i in range(window.nav_list.count())}
        self.assertEqual(keys, EXPECTED_NAV_KEYS)
        labels = [window.nav_list.item(i).text() for i in range(window.nav_list.count())]
        self.assertNotIn("本周战报", labels)  # 一级导航移除，改为顶部栏按钮
        self.assertIn("赛事", labels)
        self.assertEqual(window.weekly_button.text(), "本周战报")
        self.assertEqual(window.simulate_button.text(), "模拟下一周")
        self.assertEqual(window.init_button.text(), "初始化赛季")
        # 外壳自身（顶部栏）不得制造滚动容器（§8.2）
        self.assertEqual(window.top_bar.findChildren(QScrollArea), [])

    def test_sidebar_click_navigates_and_highlight_follows_router(self) -> None:
        window = self._make_window()
        self.assertEqual(window.router.current, Route("dashboard"))
        self.assertEqual(window.nav_list.currentRow(), self._nav_row(window, "dashboard"))

        self._click_nav_row(window, "teams")
        self.assertEqual(window.router.current, Route("teams"))
        self.assertIsInstance(window.stack.currentWidget(), (EntityPageBase, EmptyState))
        self.assertEqual(window.nav_list.currentRow(), self._nav_row(window, "teams"))

        self._click_nav_row(window, "saves")
        self.assertEqual(window.router.current, Route("saves"))
        self.assertIn(type(window.stack.currentWidget()).__module__, (
            "football_simulator.ui_v2.pages.saves_page",
            "football_simulator.ui_v2.pages.saves_page_v2",
        ))  # saves 页面（legacy 或新契约实例）
        self.assertEqual(window.nav_list.currentRow(), self._nav_row(window, "saves"))

    def test_back_forward_buttons_track_router_history(self) -> None:
        window = self._make_window()
        self.assertFalse(window.back_button.isEnabled())
        self.assertFalse(window.forward_button.isEnabled())

        window.nav_list.setCurrentRow(self._nav_row(window, "teams"))
        window.nav_list.setCurrentRow(self._nav_row(window, "players"))
        self.assertTrue(window.back_button.isEnabled())
        self.assertFalse(window.forward_button.isEnabled())

        window.back_button.click()
        self.assertEqual(window.router.current, Route("teams"))
        self.assertTrue(window.forward_button.isEnabled())
        self.assertEqual(window.nav_list.currentRow(), self._nav_row(window, "teams"))

        window.forward_button.click()
        self.assertEqual(window.router.current, Route("players"))
        self.assertFalse(window.forward_button.isEnabled())
        self.assertEqual(window.nav_list.currentRow(), self._nav_row(window, "players"))

    # -- 实体路由（延迟加载页面 / 占位容忍） --------------------------------

    def test_entity_routes_load_lazy_pages_or_migration_placeholder(self) -> None:
        window = self._make_window()
        with open_read_connection(SAVE_A) as conn:
            season = resolve_current_season(conn).season_number
            match = list_matches(conn, season)[0]
            team = load_team_refs(conn)[0]
            player = next(p for p in list_players(conn, season) if p.is_real)

        routes_and_nav_keys = [
            (Route("match", match=match.match_id), "matches"),
            (Route("team", team=team.team_id, season=season), "teams"),
            (Route("player", player=player.player_id, season=season), "players"),
        ]
        for route, nav_key in routes_and_nav_keys:
            window.router.navigate(route)
            page = window.stack.currentWidget()
            self.assertIsInstance(page, (EntityPageBase, EmptyState), msg=str(route))
            self.assertIs(window.stack.currentWidget(), page)
            self.assertEqual(window.router.current, route)
            self.assertEqual(window.nav_list.currentRow(), self._nav_row(window, nav_key))
            if isinstance(page, EmptyState):
                # 并行页面尚未落地：出现"页面迁移中"占位也算通过
                self.assertEqual(page._title_label.text(), "页面迁移中")

    # -- 面包屑 -----------------------------------------------------------

    def test_breadcrumbs_follow_route_and_navigate_on_click(self) -> None:
        window = self._make_window()
        with open_read_connection(SAVE_A) as conn:
            season = resolve_current_season(conn).season_number
            team = load_team_refs(conn)[0]

        window.router.navigate(Route("team", team=team.team_id, season=season))
        labels = self._breadcrumb_labels(window)
        self.assertIn("球队", labels)
        # 末位 crumb 优先用 E 页面提供的显示名，回退为通用标题"球队详情"
        self.assertTrue(
            any(label in ("球队详情", team.display_name) for label in labels),
            f"面包屑应包含当前球队：{labels}",
        )
        link = next(l for l in window.breadcrumb_bar.findChildren(EntityLink) if l.text() == "球队")
        QTest.mouseClick(link, Qt.MouseButton.LeftButton)
        self.assertEqual(window.router.current, Route("teams"))
        self.assertIn("球队", self._breadcrumb_labels(window))

        window.router.navigate(Route("players"))
        self.assertIn("球员", self._breadcrumb_labels(window))

        window.router.navigate(Route("dashboard"))
        labels = self._breadcrumb_labels(window)
        self.assertIn("主页", labels)

    # -- 赛事 hub ----------------------------------------------------------

    def test_competition_hub_tabs_follow_route(self) -> None:
        window = self._make_window()
        with open_read_connection(SAVE_A) as conn:
            season = resolve_current_season(conn).season_number

        self._click_nav_row(window, "competition")
        self.assertEqual(
            window.router.current,
            Route("competition", competition="一级联赛", season=season),
        )
        # 阶段 5：赛事路由已切换到查询驱动的 CompetitionPage（EntityPageBase
        # 契约，apply_route 按 route 参数渲染；legacy hub 仅为回退）。
        page = window.stack.currentWidget()
        self.assertIsInstance(page, EntityPageBase)
        self.assertEqual(page.current_route().params.get("competition"), "一级联赛")

        window.router.navigate(Route("competition", competition="优胜者杯", season=season))
        self.assertEqual(
            page.current_route().params.get("competition"), "优胜者杯"
        )
        window.router.navigate(Route("competition", competition="次级联赛", season=season))
        self.assertEqual(
            page.current_route().params.get("competition"), "次级联赛"
        )
        self.assertIn("次级联赛", self._breadcrumb_labels(window))

    # -- 全局搜索 ----------------------------------------------------------

    def test_global_search_finds_player_and_navigates(self) -> None:
        window = self._make_window()
        with open_read_connection(SAVE_A) as conn:
            season = resolve_current_season(conn).season_number
            target = next(p for p in list_players(conn, season) if p.is_real)

        window.search_box.line_edit.setText(target.display_name)
        QTest.qWait(window.search_box.DEBOUNCE_MS + 400)
        self.assertTrue(window.search_box.popup.isVisible())
        self.assertGreaterEqual(window.search_box.popup.count(), 1)

        item = None
        for index in range(window.search_box.popup.count()):
            candidate = window.search_box.popup.item(index)
            route = candidate.data(Qt.UserRole)
            if isinstance(route, Route) and route.params.get("player") == target.player_id:
                item = candidate
                break
        self.assertIsNotNone(item, "搜索结果应包含目标球员")
        rect = window.search_box.popup.visualItemRect(item)
        QTest.mouseClick(
            window.search_box.popup.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(window.router.current.name, "player")
        self.assertEqual(window.router.current.params.get("player"), target.player_id)
        self.assertEqual(window.router.current.int_param("season"), season)
        self.assertFalse(window.search_box.popup.isVisible())
        self.assertEqual(window.search_box.line_edit.text(), "")

    def test_global_search_finds_team_and_navigates(self) -> None:
        window = self._make_window()
        with open_read_connection(SAVE_A) as conn:
            season = resolve_current_season(conn).season_number
            team = load_team_refs(conn)[0]

        window.search_box.line_edit.setText(team.display_name)
        QTest.qWait(window.search_box.DEBOUNCE_MS + 400)
        self.assertTrue(window.search_box.popup.isVisible())
        item = None
        for index in range(window.search_box.popup.count()):
            candidate = window.search_box.popup.item(index)
            route = candidate.data(Qt.UserRole)
            if isinstance(route, Route) and route.params.get("team") == str(team.team_id):
                item = candidate
                break
        self.assertIsNotNone(item, "搜索结果应包含目标球队")
        rect = window.search_box.popup.visualItemRect(item)
        QTest.mouseClick(
            window.search_box.popup.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            rect.center(),
        )
        self.assertEqual(window.router.current.name, "team")
        self.assertEqual(window.router.current.int_param("team"), team.team_id)

        # 清空输入 → 结果清空、下拉收起
        window.search_box.line_edit.setText("")
        QTest.qWait(20)
        self.assertFalse(window.search_box.popup.isVisible())
        self.assertEqual(window.search_box.popup.count(), 0)

    # -- 存档切换 ----------------------------------------------------------

    def test_save_switch_clears_page_states_and_leaves_entity_route(self) -> None:
        window = self._make_window()
        with open_read_connection(SAVE_A) as conn:
            season = resolve_current_season(conn).season_number
            team = load_team_refs(conn)[0]

        route = Route("team", team=team.team_id, season=season)
        window.router.navigate(route)
        window.router.set_page_state(route.route_key, {"stub_state": [1, 2, 3]})
        self.assertIsNotNone(window.router.get_page_state(route.route_key))

        window.save_picker.setCurrentText(SAVE_B)
        self.assertEqual(window.save_picker.currentText(), SAVE_B)
        # 实体详情路由指向旧存档 ID → 回主页
        self.assertEqual(window.router.current, Route("dashboard"))
        # 旧存档的页面状态不得残留
        self.assertIsNone(window.router.get_page_state(route.route_key))

        # 非实体路由存档切换 → 原地刷新
        window.router.navigate(Route("saves"))
        window.save_picker.setCurrentText(SAVE_A)
        self.assertEqual(window.router.current, Route("saves"))
        self.assertIn(type(window.stack.currentWidget()).__module__, (
            "football_simulator.ui_v2.pages.saves_page",
            "football_simulator.ui_v2.pages.saves_page_v2",
        ))  # saves 页面（legacy 或新契约实例）

    # -- 待办与模拟 ---------------------------------------------------------

    def test_pending_badge_counts_pending_transfer_review_after_week_25(self) -> None:
        window = self._make_window(weeks_a=25)
        snapshot = window.snapshot
        expected = (
            len(snapshot.pending_ability_review)
            + len(snapshot.pending_transfer_review)
            + (1 if snapshot.pending_draft.get("status") == "awaiting_input" else 0)
        )
        self.assertGreater(expected, 0, "第 25 周后应存在待处理的转会审核")
        self.assertEqual(window.pending_button.text(), f"处理待办({expected})")
        self.assertTrue(window.pending_button.isEnabled())

        window.pending_button.click()
        self.assertEqual(window.router.current.name, "transfers")
        self.assertEqual(window.router.current.int_param("season"), snapshot.season_number)

    def test_simulate_week_keeps_page_and_weekly_button_works(self) -> None:
        window = self._make_window()
        before = window.snapshot.current_week
        window._simulate_week()
        _wait_simulate_done(window)  # 模拟已移入后台线程（§12.5）
        self.assertFalse(window._simulate_in_progress)
        self.assertEqual(window.snapshot.current_week, before + 1)
        # 用户确认 #9：单步模拟不再自动打开本周战报，停留在原页面。
        self.assertEqual(window.router.current.name, "dashboard")
        window.weekly_button.click()
        self.assertEqual(window.router.current.name, "weekly_report")
        self.assertEqual(window.router.current.int_param("week"), window.snapshot.current_week)
        # weekly_report 不在一级导航中，侧栏不应有高亮项
        self.assertEqual(window.nav_list.currentRow(), -1)

    def test_init_and_simulate_block_double_submit(self) -> None:
        service = _CountingService(SimulatorUIService())
        SimulatorUIService().create_save(SAVE_GUARD)  # 仅建档（未初始化），保证初始化可执行
        window = self._make_window(service=service, save_name=SAVE_GUARD, build=False)
        self.assertEqual(window.status_label.text(), "当前存档还没有赛季数据。")

        init_seen: list[bool] = []

        def reenter_init() -> None:
            init_seen.append(window.init_button.isEnabled())
            window._initialize_current_save()  # 重入应被防重复提交挡下

        service.on_initialize = reenter_init
        window._initialize_current_save()
        self.assertEqual(service.init_calls, 1)
        self.assertEqual(init_seen, [False])
        self.assertTrue(window.init_button.isEnabled())
        self.assertIsNotNone(window.snapshot)

        # 模拟已移入后台线程：进行中从主线程再次调用必须被
        # _simulate_in_progress 挡下（服务只执行一次）。
        window._simulate_week()
        window._simulate_week()  # 进行中重入：应立即返回，不触发第二次模拟
        _wait_simulate_done(window)
        self.assertEqual(service.simulate_calls, 1)
        self.assertTrue(window.simulate_button.isEnabled())
        self.assertEqual(window.snapshot.current_week, 1)

    # -- 大表旁路 -----------------------------------------------------------
