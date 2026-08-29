"""阶段 3 验收测试：导航外壳（纯 Python）与通用实体组件（PySide6 offscreen）。

运行（在项目根目录）::

    QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_navigation_shell -v

- ``RouteTests`` / ``RouterTests`` / ``BreadcrumbsTests``：纯路由逻辑，不需要 Qt。
- 其余用例：组件交互（QTest）与"无嵌套滚动结构"断言（§8.2 滚动硬规则）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
import unittest
from dataclasses import dataclass
from typing import Any, List, Optional

from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.navigation import Breadcrumb, Route, Router, breadcrumbs

try:
    from football_simulator.ui_v2.components import (
        ColumnSpec,
        EmptyState,
        EntityLink,
        EntityTable,
        FilterBar,
        PageHeader,
    )
    from football_simulator.ui_v2.components.filter_bar import SEARCH_DEBOUNCE_MS

    from PySide6.QtCore import QPoint, QPointF, QEvent, Qt
    from PySide6.QtGui import QEnterEvent, QFocusEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractScrollArea,
        QApplication,
        QHeaderView,
        QLabel,
        QMainWindow,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QTableView,
        QVBoxLayout,
        QWidget,
    )

    HAS_PYSIDE6 = True
except ImportError:  # 系统 Python 无 PySide6 时只跑纯路由测试
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# 纯 Python：Route
# ---------------------------------------------------------------------------


class RouteTests(unittest.TestCase):
    def test_unknown_route_raises(self) -> None:
        with self.assertRaises(ValueError):
            Route("no_such_route")
        with self.assertRaises(ValueError):
            Route("Teams")  # 大小写敏感

    def test_missing_required_param_raises(self) -> None:
        with self.assertRaises(ValueError):
            Route("weekly_report")  # 缺 week
        with self.assertRaises(ValueError):
            Route("player", player="real::pedri")  # 缺 season
        with self.assertRaises(ValueError):
            Route("competition", competition="cup")  # 缺 season

    def test_unknown_param_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            Route("teams", extra="1")
        with self.assertRaises(ValueError):
            Route("weekly_report", week=1, foo="bar")

    def test_none_param_raises(self) -> None:
        with self.assertRaises(ValueError):
            Route("weekly_report", week=None)

    def test_int_params_are_coerced(self) -> None:
        self.assertEqual(Route("weekly_report", week="3"), Route("weekly_report", week=3))
        self.assertEqual(Route("weekly_report", week=3).params, {"week": "3"})
        self.assertEqual(
            Route("matches", season=2, competition="一级联赛", week="12").params,
            {"season": "2", "competition": "一级联赛", "week": "12"},
        )

    def test_invalid_int_param_raises(self) -> None:
        for bad in ("第3周", "1.5", " ", 1.5, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    Route("weekly_report", week=bad)

    def test_to_path_escapes_and_sorts_params(self) -> None:
        self.assertEqual(
            Route("player", player="real::pedri", season=2).to_path(),
            "player?player=real%3A%3Apedri&season=2",
        )
        self.assertEqual(
            Route("player", player="佩德里", season=2).to_path(),
            "player?player=%E4%BD%A9%E5%BE%B7%E9%87%8C&season=2",
        )
        # 参数按名字排序：competition < season < week
        self.assertEqual(
            Route("matches", season=2, competition="杯赛", week=3).to_path(),
            "matches?competition=%E6%9D%AF%E8%B5%9B&season=2&week=3",
        )
        self.assertEqual(Route("teams").to_path(), "teams")
        self.assertEqual(Route("dashboard").to_path(), "dashboard")

    def test_parse_round_trip(self) -> None:
        samples = [
            Route("dashboard"),
            Route("weekly_report", week=7),
            Route("season_overview", season=3),
            Route("competition", competition="cup:ucl", season=3),
            Route("matches", season=3),
            Route("matches", season=3, competition="一级联赛", week=12),
            Route("match", match="real::match:3:12:1"),
            Route("teams"),
            Route("team", team=5, season=3),
            Route("players"),
            Route("player", player="real::pedri", season=3, tab="matches"),
            Route("transfers", season=3),
            Route("draft", season=3),
            Route("history", season=3),
            Route("history"),
            Route("saves"),
            Route("player", player="佩德里", season=3),
        ]
        for route in samples:
            with self.subTest(route=route.route_key):
                parsed = Route.parse(route.to_path())
                self.assertEqual(parsed, route)
                self.assertEqual(parsed.to_path(), route.to_path())
        self.assertEqual(Route.parse("teams"), Route("teams"))
        self.assertEqual(Route.parse("saves?"), Route("saves"))

    def test_parse_rejects_bad_paths(self) -> None:
        with self.assertRaises(ValueError):
            Route.parse("no_such_route")
        with self.assertRaises(ValueError):
            Route.parse("weekly_report?week=abc")
        with self.assertRaises(ValueError):
            Route.parse("")
        with self.assertRaises(ValueError):
            Route.parse(123)  # type: ignore[arg-type]

    def test_route_key_equals_to_path(self) -> None:
        route = Route("player", player="real::pedri", season=2)
        self.assertEqual(route.route_key, route.to_path())
        self.assertEqual(route.route_key, "player?player=real%3A%3Apedri&season=2")

    def test_hashable_and_value_equality(self) -> None:
        a = Route("team", team=1, season=2)
        b = Route("team", team="1", season="2")
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertEqual(len({a, b}), 1)
        self.assertNotEqual(a, Route("team", team=1, season=3))
        self.assertNotEqual(a, "team?season=2&team=1")

    def test_int_param_helper(self) -> None:
        route = Route("matches", season=3, week=5)
        self.assertEqual(route.int_param("season"), 3)
        self.assertEqual(route.int_param("week"), 5)
        self.assertIsNone(route.int_param("competition"))
        self.assertIsNone(Route("teams").int_param("season"))


# ---------------------------------------------------------------------------
# 纯 Python：Router
# ---------------------------------------------------------------------------


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = Router()
        self.seen: List[tuple] = []
        self.router.observe(lambda route, cause: self.seen.append((route.route_key, cause)))

    def test_first_navigate_sets_current_and_notifies(self) -> None:
        a = Route("teams")
        self.router.navigate(a)
        self.assertEqual(self.router.current, a)
        self.assertFalse(self.router.can_back)
        self.assertFalse(self.router.can_forward)
        self.assertEqual(self.seen, [(a.route_key, "navigate")])

    def test_navigate_same_key_is_noop(self) -> None:
        a = Route("teams")
        self.router.navigate(a)
        self.router.navigate(Route("teams"))
        self.assertEqual(self.seen, [(a.route_key, "navigate")])
        self.assertFalse(self.router.can_back)

    def test_navigate_rejects_non_route(self) -> None:
        with self.assertRaises(TypeError):
            self.router.navigate("teams")  # type: ignore[arg-type]

    def test_back_and_forward(self) -> None:
        a, b, c = Route("teams"), Route("players"), Route("saves")
        self.router.navigate(a)
        self.router.navigate(b)
        self.router.navigate(c)
        self.assertTrue(self.router.can_back)
        self.assertFalse(self.router.can_forward)

        self.router.back()
        self.assertEqual(self.router.current, b)
        self.assertTrue(self.router.can_forward)
        self.router.back()
        self.assertEqual(self.router.current, a)
        self.assertFalse(self.router.can_back)
        # 栈空时 back 无操作
        self.router.back()
        self.assertEqual(self.router.current, a)

        self.router.forward()
        self.assertEqual(self.router.current, b)
        self.router.forward()
        self.assertEqual(self.router.current, c)
        self.assertFalse(self.router.can_forward)
        self.router.forward()
        self.assertEqual(self.router.current, c)

        self.assertEqual(
            self.seen,
            [
                (a.route_key, "navigate"),
                (b.route_key, "navigate"),
                (c.route_key, "navigate"),
                (b.route_key, "back"),
                (a.route_key, "back"),
                (b.route_key, "forward"),
                (c.route_key, "forward"),
            ],
        )

    def test_new_navigate_clears_forward_stack(self) -> None:
        a, b = Route("teams"), Route("players")
        self.router.navigate(a)
        self.router.navigate(b)
        self.router.back()
        self.assertTrue(self.router.can_forward)
        c = Route("saves")
        self.router.navigate(c)
        self.assertFalse(self.router.can_forward)
        self.assertEqual(self.router.forward(), None)
        self.assertEqual(self.router.current, c)
        self.assertEqual([key for key, _ in self.seen][-1], c.route_key)

    def test_history_cap_drops_oldest(self) -> None:
        routes = [Route("team", team=i, season=1) for i in range(1, 206)]  # 205 条
        for route in routes:
            self.router.navigate(route)
        self.assertTrue(self.router.can_back)
        backs = 0
        while self.router.can_back:
            self.router.back()
            backs += 1
        self.assertEqual(backs, Router.MAX_HISTORY)
        # 最早的 205-200=5 条中，r1..r4 已被挤出，最旧可用的是第 5 条
        self.assertEqual(self.router.current, routes[4])

    def test_page_state_cache(self) -> None:
        key = Route("player", player="real::pedri", season=2).route_key
        self.assertIsNone(self.router.get_page_state(key))
        state = {"scroll": 42, "sort": ("goals", True)}
        self.router.set_page_state(key, state)
        self.assertIs(self.router.get_page_state(key), state)
        other = Route("players").route_key
        self.router.set_page_state(other, [1, 2, 3])
        self.router.clear_page_states()
        self.assertIsNone(self.router.get_page_state(key))
        self.assertIsNone(self.router.get_page_state(other))

    def test_unobserve_stops_notifications(self) -> None:
        a = Route("teams")

        def observer(route: Route, cause: str) -> None:  # pragma: no cover - 通过 seen 断言
            self.seen.append((route.route_key, cause))

        router = Router()
        router.observe(observer)
        router.navigate(a)
        router.unobserve(observer)
        router.unobserve(observer)  # 未注册时静默
        router.navigate(Route("players"))
        self.assertEqual([cause for _, cause in self.seen], ["navigate"])


# ---------------------------------------------------------------------------
# 纯 Python：breadcrumbs
# ---------------------------------------------------------------------------

_TOP_LEVEL_TITLES = {
    "dashboard": "主页",
    "weekly_report": "本周战报",
    "season_overview": "赛季总览",
    "matches": "比赛中心",
    "teams": "球队",
    "players": "球员",
    "transfers": "转会",
    "draft": "选秀",
    "history": "历史",
    "saves": "存档",
}


def _minimal_route(name: str) -> Route:
    required: dict = {
        "weekly_report": {"week": 2},
        "season_overview": {"season": 3},
        "matches": {"season": 3},
        "transfers": {"season": 3},
        "draft": {"season": 3},
    }
    return Route(name, **required.get(name, {}))


class BreadcrumbsTests(unittest.TestCase):
    def test_top_level_routes_are_single_crumb(self) -> None:
        for name, title in _TOP_LEVEL_TITLES.items():
            with self.subTest(route=name):
                crumbs = breadcrumbs(_minimal_route(name))
                self.assertEqual(len(crumbs), 1)
                self.assertEqual(crumbs[0].label, title)
                self.assertIsNone(crumbs[0].route)
                self.assertIsInstance(crumbs[0], Breadcrumb)

    def test_player_breadcrumb(self) -> None:
        route = Route("player", player="real::pedri", season=2)
        crumbs = breadcrumbs(route)
        self.assertEqual([crumb.label for crumb in crumbs], ["球员", "球员详情"])
        self.assertEqual(crumbs[0].route, Route("players"))
        self.assertIsNone(crumbs[1].route)
        with_context = breadcrumbs(route, {"player_name": "佩德里"})
        self.assertEqual(with_context[-1].label, "佩德里")

    def test_team_breadcrumb(self) -> None:
        route = Route("team", team=5, season=2)
        crumbs = breadcrumbs(route)
        self.assertEqual([crumb.label for crumb in crumbs], ["球队", "球队详情"])
        self.assertEqual(crumbs[0].route, Route("teams"))
        self.assertIsNone(crumbs[1].route)
        with_context = breadcrumbs(route, {"team_name": "曼联"})
        self.assertEqual(with_context[-1].label, "曼联")

    def test_competition_breadcrumb(self) -> None:
        route = Route("competition", competition="cup", season=3)
        crumbs = breadcrumbs(route, {"competition_name": "优胜者杯"})
        self.assertEqual([crumb.label for crumb in crumbs], ["赛季总览", "优胜者杯"])
        self.assertEqual(crumbs[0].route, Route("season_overview", season=3))
        self.assertIsNone(crumbs[1].route)

    def test_match_breadcrumb_with_full_context(self) -> None:
        route = Route("match", match="m1")
        crumbs = breadcrumbs(route, {"season": 3, "week": 5, "match_label": "曼联 2:1 利物浦"})
        self.assertEqual([crumb.label for crumb in crumbs], ["比赛中心", "第 5 周", "曼联 2:1 利物浦"])
        self.assertEqual(crumbs[0].route, Route("matches", season=3))
        self.assertEqual(crumbs[0].route.route_key, "matches?season=3")
        self.assertEqual(crumbs[1].route, Route("matches", season=3, week=5))
        self.assertIsNone(crumbs[2].route)

    def test_match_breadcrumb_without_week(self) -> None:
        crumbs = breadcrumbs(Route("match", match="m1"), {"season": 3})
        self.assertEqual([crumb.label for crumb in crumbs], ["比赛中心", "比赛详情"])
        self.assertEqual(crumbs[0].route.route_key, "matches?season=3")
        self.assertIsNone(crumbs[1].route)

    def test_match_breadcrumb_without_context_never_fabricates_params(self) -> None:
        crumbs = breadcrumbs(Route("match", match="m1"))
        self.assertEqual([crumb.label for crumb in crumbs], ["比赛详情"])
        self.assertIsNone(crumbs[0].route)
        # context 里有 week 但缺 season 时同样只给当前页
        crumbs = breadcrumbs(Route("match", match="m1"), {"week": 5})
        self.assertEqual([crumb.label for crumb in crumbs], ["比赛详情"])

    def test_context_label_falls_back_to_generic(self) -> None:
        crumbs = breadcrumbs(Route("match", match="m1"), {"season": "3"})  # season 为字符串也可用
        self.assertEqual(crumbs[0].route.route_key, "matches?season=3")


# ---------------------------------------------------------------------------
# Qt 组件测试公共设施
# ---------------------------------------------------------------------------

_QT_APP: Optional[QApplication] = None


def _ensure_qt_app() -> QApplication:
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


class _NavRecorder:
    """占位 navigator：记录导航目标，供断言。"""

    def __init__(self) -> None:
        self.routes: List[Route] = []

    def __call__(self, route: Route) -> None:
        self.routes.append(route)

    @property
    def last(self) -> Optional[Route]:
        return self.routes[-1] if self.routes else None

    def clear(self) -> None:
        self.routes.clear()


@dataclasses.dataclass
class _PlayerRow:
    """占位 ViewModel 行 DTO（本测试包不导入 state/queries）。"""

    player_id: str
    name: str
    goals: Optional[int]
    rating: Optional[float]


_COLUMNS = None  # 延迟构造（依赖 PySide6 的 ColumnSpec/Qt）


def _columns():
    global _COLUMNS
    if _COLUMNS is None:
        _COLUMNS = [
            ColumnSpec("name", "球员"),
            ColumnSpec("goals", "进球"),
            ColumnSpec("rating", "评分", alignment=Qt.AlignmentFlag.AlignRight),
        ]
    return _COLUMNS


def _sample_rows(count: int = 25) -> List[_PlayerRow]:
    # goals 打乱顺序且包含 0/2/10 等值，用于区分数值排序与字典序排序
    rows = []
    for i in range(count):
        goals: Optional[int] = (i * 7 + 11) % count
        if i == 7:
            goals = None
        rating = None if i % 4 == 0 else round(6.5 + i * 0.05, 2)
        rows.append(_PlayerRow(player_id=f"real::player:{i}", name=f"球员{i}", goals=goals, rating=rating))
    return rows


def _player_route_for_row(row: _PlayerRow) -> Route:
    return Route("player", player=row.player_id, season=2)


@unittest.skipUnless(HAS_PYSIDE6, '需要 PySide6（用 .venv-ui-v2/bin/python 运行）')
class _WidgetTestCase(unittest.TestCase):
    """需要 QApplication 的测试基类。"""

    def setUp(self) -> None:
        self.app = _ensure_qt_app()

    def _show(self, widget: QWidget, size: tuple = (900, 520)) -> None:
        widget.resize(*size)
        widget.show()
        self.app.processEvents()


# ---------------------------------------------------------------------------
# EntityLink
# ---------------------------------------------------------------------------


class EntityLinkTests(_WidgetTestCase):
    def _make_link(self, recorder: Optional[_NavRecorder] = None) -> tuple:
        recorder = recorder or _NavRecorder()
        route = Route("player", player="real::pedri", season=2)
        link = EntityLink("佩德里", route, recorder)
        link.adjustSize()
        return link, route, recorder

    def test_mouse_click_navigates(self) -> None:
        link, route, recorder = self._make_link()
        QTest.mouseClick(link, Qt.MouseButton.LeftButton)
        self.assertEqual(recorder.routes, [route])

    def test_enter_and_keypad_return_navigate_after_focus(self) -> None:
        link, route, recorder = self._make_link()
        link.setFocus()
        QTest.keyClick(link, Qt.Key.Key_Return)
        self.assertEqual(recorder.routes, [route])
        QTest.keyClick(link, Qt.Key.Key_Enter)  # 小键盘回车
        self.assertEqual(recorder.routes, [route, route])

    def test_focus_policy_is_strong_focus(self) -> None:
        link, _, _ = self._make_link()
        self.assertEqual(link.focusPolicy(), Qt.FocusPolicy.StrongFocus)

    def test_tooltip_and_accessible_name(self) -> None:
        link, _, _ = self._make_link()
        self.assertEqual(link.toolTip(), "佩德里")
        self.assertEqual(link.accessibleName(), "佩德里")
        link.setText("加维")
        self.assertEqual(link.toolTip(), "加维")
        self.assertEqual(link.accessibleName(), "加维")

    def test_set_route_updates_target(self) -> None:
        link, _, recorder = self._make_link()
        new_route = Route("team", team=3, season=2)
        link.set_route(new_route)
        self.assertEqual(link.route, new_route)
        QTest.mouseClick(link, Qt.MouseButton.LeftButton)
        self.assertEqual(recorder.routes, [new_route])

    def test_click_without_route_or_navigator_is_noop(self) -> None:
        link = EntityLink("占位", None, _NavRecorder())
        link.adjustSize()
        QTest.mouseClick(link, Qt.MouseButton.LeftButton)  # 无 route：不导航不报错
        route = Route("teams")
        link2 = EntityLink("占位", route, None)
        link2.adjustSize()
        QTest.mouseClick(link2, Qt.MouseButton.LeftButton)  # 无 navigator：不导航不报错
        self.assertIsNone(link.route)

    def test_hover_and_focus_change_underline_font(self) -> None:
        link, _, _ = self._make_link()
        self.assertFalse(link.font().underline())
        # PySide6 6.10 的 enterEvent 要求 QEnterEvent（QtGui），其余用 QEvent 即可
        link.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
        self.assertTrue(link.font().underline())
        link.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertFalse(link.font().underline())
        link.focusInEvent(QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
        self.assertTrue(link.font().underline())
        link.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
        self.assertFalse(link.font().underline())


# ---------------------------------------------------------------------------
# EntityTable
# ---------------------------------------------------------------------------


class EntityTableTests(_WidgetTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.recorder = _NavRecorder()
        self.table = EntityTable(_columns(), navigator=self.recorder)
        self.rows = _sample_rows()
        self.table.set_rows(self.rows, route_for_row=_player_route_for_row)
        self._container = QWidget()
        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        self._show(self._container)

    # -- 手势助手（Qt 6.10 offscreen 下的可靠投递路径） -------------------------

    def _double_click_row(self, row: int) -> None:
        """对指定行做双击：QTest 事件经窗口投递，位置映射到窗口坐标。"""
        view = self.table.view
        rect = view.visualRect(view.model().index(row, 0))
        self.assertTrue(rect.isValid())
        window_pos = view.viewport().mapTo(self._container, rect.center())
        QTest.mouseDClick(
            self._container.windowHandle(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            window_pos,
        )
        self.app.processEvents()

    def _click_header_section(self, column: int) -> None:
        """点击表头某一列的中央（靠近列边界会命中拖拽调宽手柄）。"""
        header: QHeaderView = self.table.view.horizontalHeader()
        center_x = header.sectionViewportPosition(column) + header.sectionSize(column) // 2
        QTest.mouseClick(
            header.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(center_x, header.height() // 2),
        )
        self.app.processEvents()

    def test_row_count_and_placeholder_for_none(self) -> None:
        view = self.table.view
        self.assertEqual(view.model().rowCount(), 25)
        self.assertEqual(view.objectName(), "entityTableView")
        # 第 7 行 goals=None → 显示 "—"
        self.assertEqual(view.model().index(7, 1).data(Qt.ItemDataRole.DisplayRole), "—")
        # 评分列同样处理 None
        self.assertEqual(view.model().index(0, 2).data(Qt.ItemDataRole.DisplayRole), "—")

    def test_header_click_sorts_numerically_and_toggles(self) -> None:
        view = self.table.view
        goals_column = 1

        def displayed_goals() -> List[str]:
            return [
                view.model().index(row, goals_column).data(Qt.ItemDataRole.DisplayRole)
                for row in range(view.model().rowCount())
            ]

        before_first = displayed_goals()[0]
        self._click_header_section(goals_column)
        ascending = displayed_goals()
        # 数值排序而非字典序（若按字典序，'2' 会排在 '11' 之后）
        self.assertEqual(
            [int(v) for v in ascending if v != "—"], [n for n in range(25) if n != 10]
        )
        self.assertEqual(ascending[-1], "—")  # None 值固定排在最后
        self.assertNotEqual(ascending[0], before_first)  # 点击表头后第一行变化
        self.assertEqual(
            self.table.view.horizontalHeader().sortIndicatorSection(), goals_column
        )

        self._click_header_section(goals_column)
        descending = displayed_goals()
        self.assertEqual(descending[0], "—")  # 降序时 None 值排最前
        self.assertEqual(
            [int(v) for v in descending if v != "—"],
            sorted([n for n in range(25) if n != 10], reverse=True),
        )

    def test_double_click_activates_route(self) -> None:
        self._double_click_row(0)
        # 双击会同时发出 doubleClicked 与 activated（目标一致），去重后断言
        self.assertGreaterEqual(len(self.recorder.routes), 1)
        self.assertEqual(
            {r.route_key for r in self.recorder.routes},
            {_player_route_for_row(self.rows[0]).route_key},
        )

    def test_keyboard_enter_activates_current_row(self) -> None:
        view = self.table.view
        view.setCurrentIndex(view.model().index(2, 0))
        view.setFocus()
        self.app.processEvents()
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(self.recorder.routes, [_player_route_for_row(self.rows[2])])

    def test_rows_without_route_do_not_navigate(self) -> None:
        self.table.set_rows(self.rows, route_for_row=lambda row: None)
        self._double_click_row(0)
        view = self.table.view
        view.setCurrentIndex(view.model().index(1, 0))
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(self.recorder.routes, [])

    def test_on_row_activated_signal_carries_row(self) -> None:
        received: List[Any] = []
        self.table.on_row_activated.connect(received.append)
        view = self.table.view
        view.setCurrentIndex(view.model().index(3, 0))
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(received, [self.rows[3]])
        self.assertEqual(self.recorder.routes, [_player_route_for_row(self.rows[3])])

    def test_table_is_the_only_scroll_surface_and_well_configured(self) -> None:
        view = self.table.view
        self.assertTrue(view.verticalHeader().isHidden())  # 纵向表头隐藏
        self.assertEqual(view.verticalScrollMode(), QTableView.ScrollMode.ScrollPerPixel)
        self.assertTrue(view.horizontalHeader().stretchLastSection())
        self.assertTrue(view.isSortingEnabled())
        self.assertEqual(self.table.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(self.table.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Expanding)
        # 组件自身不得包含 QScrollArea（QTableView 不是 QScrollArea 子类）
        self.assertEqual(self.table.findChildren(QScrollArea), [])

    def test_set_rows_keeps_current_sort(self) -> None:
        view = self.table.view
        view.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        self.assertEqual(view.model().index(0, 1).data(Qt.ItemDataRole.DisplayRole), "0")
        self.table.set_rows(list(reversed(self.rows)), route_for_row=_player_route_for_row)
        self.assertEqual(view.model().rowCount(), 25)
        self.assertEqual(view.model().index(0, 1).data(Qt.ItemDataRole.DisplayRole), "0")


class NoNestedScrollTests(_WidgetTestCase):
    """§8.2：中央区只放 EntityTable 时，全窗口只允许一个纵向滚动面。"""

    def test_main_window_with_entity_table_has_single_scroll_area(self) -> None:
        recorder = _NavRecorder()
        window = QMainWindow()
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        table = EntityTable(_columns(), navigator=recorder)
        table.set_rows(_sample_rows(), route_for_row=_player_route_for_row)
        layout.addWidget(table)
        window.setCentralWidget(central)
        self._show(window, size=(1200, 800))

        scroll_areas = [
            child
            for child in window.findChildren(QAbstractScrollArea)
            if not isinstance(child, QHeaderView)  # 表头属于表格自身部件，不是独立滚动面
        ]
        self.assertEqual(len(scroll_areas), 1)
        self.assertIs(scroll_areas[0], table.view)
        self.assertEqual(window.findChildren(QScrollArea), [])


# ---------------------------------------------------------------------------
# PageHeader
# ---------------------------------------------------------------------------


class PageHeaderTests(_WidgetTestCase):
    def test_breadcrumb_links_navigate_and_current_is_not_clickable(self) -> None:
        recorder = _NavRecorder()
        crumbs = breadcrumbs(
            Route("match", match="m1"),
            {"season": 3, "week": 5, "match_label": "曼联 2:1 利物浦"},
        )
        header = PageHeader("曼联 2:1 利物浦", breadcrumbs=crumbs, navigator=recorder)
        links = header.findChildren(EntityLink)
        self.assertEqual(len(links), 2)  # 比赛中心 + 第 5 周；当前页不可点

        QTest.mouseClick(links[0], Qt.MouseButton.LeftButton)
        self.assertEqual(recorder.last, Route("matches", season=3))
        QTest.mouseClick(links[1], Qt.MouseButton.LeftButton)
        self.assertEqual(recorder.last, Route("matches", season=3, week=5))

        current_labels = [
            label for label in header.findChildren(QLabel, "pageHeaderCrumbCurrent") if label.text() == "曼联 2:1 利物浦"
        ]
        self.assertEqual(len(current_labels), 1)
        self.assertFalse(any(isinstance(label, EntityLink) for label in current_labels))

    def test_breadcrumb_without_navigator_renders_labels(self) -> None:
        crumbs = breadcrumbs(Route("player", player="p", season=2))
        header = PageHeader("球员详情", breadcrumbs=crumbs, navigator=None)
        self.assertEqual(header.findChildren(EntityLink), [])
        labels = [label.text() for label in header.findChildren(QLabel)]
        self.assertIn("球员", labels)
        self.assertIn("球员详情", labels)

    def test_title_and_actions(self) -> None:
        header = PageHeader("球员详情")
        self.assertEqual(header.findChildren(QLabel)[0].text(), "球员详情")
        button = QPushButton("模拟下一周")
        header.add_action(button)
        self.assertIs(header.findChild(QPushButton), button)
        second = QPushButton("刷新")
        header.add_action(second)
        buttons = header.findChildren(QPushButton)
        self.assertEqual(buttons, [button, second])

    def test_empty_breadcrumbs_hides_row(self) -> None:
        header = PageHeader("主页", breadcrumbs=[])
        self.assertFalse(header.findChildren(EntityLink))


# ---------------------------------------------------------------------------
# FilterBar
# ---------------------------------------------------------------------------


class FilterBarTests(_WidgetTestCase):
    def _make_bar(self, callback=None) -> tuple:
        bar = FilterBar(on_search_changed=callback)
        search = bar.add_search("搜索球员或球队")
        combo = bar.add_combo("位置", ["全部位置", "GK", "DF", "MF", "FW"], "positionCombo")
        return bar, search, combo

    def test_state_and_restore_round_trip(self) -> None:
        bar, search, combo = self._make_bar()
        search.setText("佩德里")
        combo.setCurrentText("GK")
        state = bar.state()
        self.assertEqual(state, {"search": "佩德里", "positionCombo": "GK"})

        other, _, _ = self._make_bar()
        other.restore(state)
        self.assertEqual(other.state(), state)

    def test_restore_tolerates_missing_keys(self) -> None:
        bar, search, combo = self._make_bar()
        bar.restore({})  # 全缺失：保持默认
        self.assertEqual(bar.state(), {"search": "", "positionCombo": "全部位置"})
        bar.restore({"positionCombo": "MF"})  # 部分缺失：其余保持
        self.assertEqual(search.text(), "")
        self.assertEqual(combo.currentText(), "MF")
        bar.restore(None)
        self.assertEqual(combo.currentText(), "MF")
        bar.restore({"positionCombo": "不存在选项"})  # 未知选项：不变
        self.assertEqual(combo.currentText(), "MF")

    def test_search_debounce_emits_last_text(self) -> None:
        bar, search, _ = self._make_bar()
        emitted: List[str] = []
        bar.search_changed.connect(emitted.append)

        search.setText("曼")
        self.assertEqual(emitted, [])  # 未停顿 300ms 不触发
        QTest.qWait(SEARCH_DEBOUNCE_MS + 150)
        self.assertEqual(emitted, ["曼"])

        search.setText("曼联")
        search.setText("曼城中")  # 连续输入重启计时器
        QTest.qWait(SEARCH_DEBOUNCE_MS + 150)
        self.assertEqual(emitted, ["曼", "曼城中"])  # 防抖后只发最后一次

    def test_search_changed_callback(self) -> None:
        calls: List[str] = []
        bar = FilterBar(on_search_changed=calls.append)
        search = bar.add_search("搜索")
        search.setText("哈维")
        QTest.qWait(SEARCH_DEBOUNCE_MS + 150)
        self.assertEqual(calls, ["哈维"])
        self.assertEqual(search.text(), "哈维")


# ---------------------------------------------------------------------------
# EmptyState
# ---------------------------------------------------------------------------


class EmptyStateTests(_WidgetTestCase):
    def test_texts_and_layout(self) -> None:
        state = EmptyState("暂无数据", description="当前筛选条件下没有球员。", hint="试试清除筛选。")
        self._show(state, size=(600, 400))
        title = state.findChild(QLabel, "emptyStateTitle")
        description = state.findChild(QLabel, "emptyStateDescription")
        hint = state.findChild(QLabel, "emptyStateHint")
        self.assertIsNotNone(title)
        self.assertIsNotNone(description)
        self.assertIsNotNone(hint)
        assert title is not None and description is not None and hint is not None
        self.assertEqual(title.text(), "暂无数据")
        self.assertEqual(description.text(), "当前筛选条件下没有球员。")
        self.assertEqual(hint.text(), "试试清除筛选。")
        self.assertEqual(state.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(state.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(state.findChildren(QScrollArea), [])
        self.assertEqual(state.objectName(), "emptyState")

    def test_optional_texts_are_omitted(self) -> None:
        state = EmptyState("暂无数据")
        self.assertIsNone(state.findChild(QLabel, "emptyStateDescription"))
        self.assertIsNone(state.findChild(QLabel, "emptyStateHint"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
