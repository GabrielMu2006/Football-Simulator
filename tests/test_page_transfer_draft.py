"""转会中心 / 选秀中心页面测试（阶段 5，Agent F3）。

共享夹具（参照 tests/test_page_dashboard_weekly_season.py 的多存档模式，
固定随机源，独立临时存档根目录）：
- 存档 A（td_a）：推进到第 25 周（冬窗休赛周 → 转会待办，无转会历史）；
- 存档 B（td_b）：推进到第 49 周（自动处理沿途转会 → 能力审核 + 选秀待办；
  选秀待办保留，用于等待录入 UI / 只读回退 / 截图）；
- 存档 C（td_c）：完整跑完第 1 赛季并开启第 2 赛季推进 3 周
  （第 1 赛季转会历史 + 选秀日志归档；两个赛季 → 赛季选择器）；
- 存档 D（td_d）：推进到第 49 周（能力 + 选秀待办，专用于选秀真实提交）；
- 存档 S（td_s）：推进到第 25 周（转会待办，专用于转会真实提交）。

覆盖内容：
- 转会审核（§8.8 写流程）：审核卡明细（甲乙队/球员/能力/身价/总身价/差值）
  与 pending 数据一致；默认“批准”；逐笔选择后提交 → service 调用参数正确；
  真实提交后 pending 清空、历史增长、“玩家拒绝”状态、40×11 阵容完整性；
  service 为 None 时只读展示 + 提交禁用（数据回退 pending_actions，同源）；
- 转会历史（§8.7 链接合同）：行数与明细 == transfers 表该赛季数据；
  甲队/乙队/球员（逐名命中）链接路由正确；行激活不导航；状态徽标列；
- 选秀（§8.8 写流程）：等待录入说明与候选预览 == 配置候选池同语义重算；
  确认 → apply_draft(save, [])（配置候选池语义）；真实确认后 last_draft
  展示、新秀行数 == candidate_count、market_value == 30.0；已归档赛季结果
  读取自存档日志；球队/新秀链接路由；无记录空状态；
- 滚动硬规则（§8.2）：1440×860 与 1680×980 两种尺寸 × 关键状态——恰有一个
  QScrollArea 主滚动面，全展开表格纵向滚动条 AlwaysOff 且 maximum == 0
  （零嵌套滚动，不存在小框内滚动）；
- 截图输出到 Reviews/ui_audit/phase4/（transfers_* / draft_* × 两尺寸）。

运行：QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_page_transfer_draft -v
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractScrollArea,
        QApplication,
        QComboBox,
        QFrame,
        QLabel,
        QListView,
        QGraphicsView,
        QPushButton,
        QScrollArea,
        QTableView,
        QTableWidget,
        QTextEdit,
        QPlainTextEdit,
        QTreeView,
        QTreeWidget,
        QWidget,
    )

    HAS_PYSIDE6 = True
except ImportError:  # 系统 Python 无 PySide6：整模块跳过
    raise unittest.SkipTest("需要 PySide6（用 .venv-ui-v2/bin/python 运行）")

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.data import load_save_config, real_player_id
from football_simulator.ui_v2.components import EntityLink, EntityTable
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.draft_page import _DRAFT_COLUMNS, DraftPage, _column_index as _draft_column_index
from football_simulator.ui_v2.pages.entity_page_base import PageContext
from football_simulator.ui_v2.pages.transfers_page import (
    _TRANSFER_COLUMNS,
    TransfersPage,
    _column_index as _transfer_column_index,
)
from football_simulator.ui_v2.services import SimulatorUIService

from tests.support import (
    advance_week,
    create_save,
    load_snapshot,
    run_season,
    run_weeks,
    seeded_provider,
    state_path,
)

SAVE_A = "td_a"  # 第 25 周：转会待办（审核卡 + 只读回退 + 空历史）
SAVE_B = "td_b"  # 第 49 周：能力 + 选秀待办（等待录入 UI / 只读回退 / 截图）
SAVE_C = "td_c"  # 第 1 赛季归档 + 第 2 赛季 3 周（历史 / 结果 / 赛季选择器）
SAVE_D = "td_d"  # 第 49 周：选秀真实提交
SAVE_S = "td_s"  # 第 25 周：转会真实提交

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


def _shared_root() -> Path:
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_page_transfer_draft_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())
        create_save(SAVE_A)
        run_weeks(SAVE_A, 24)
        advance_week(SAVE_A)  # 第 25 周（冬窗休赛周）→ 转会待办
        create_save(SAVE_B)
        run_weeks(SAVE_B, 49)  # 第 49 周（赛季末结算）→ 能力审核 + 选秀待办
        create_save(SAVE_C)
        run_season(SAVE_C)  # 完整第 1 赛季（转会历史 + 选秀日志）
        create_save(SAVE_C)  # 开启第 2 赛季
        run_weeks(SAVE_C, 3)
        create_save(SAVE_D)
        run_weeks(SAVE_D, 49)  # 能力 + 选秀待办（选秀真实提交）
        create_save(SAVE_S)
        run_weeks(SAVE_S, 24)
        advance_week(SAVE_S)  # 第 25 周 → 转会待办（转会真实提交）
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


# -- 页面工厂与辅助 -----------------------------------------------------------


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


def _make_transfers(
    season: int,
    save_name: str = SAVE_A,
    service: Optional[SimulatorUIService] = None,
    apply_to_pages: bool = False,
) -> Tuple[_Harness, TransfersPage]:
    _shared_root()
    harness = _Harness(save_name, service=service, apply_to_pages=apply_to_pages)
    page = TransfersPage(harness.context())
    harness.attach(page)
    page.apply_route(Route("transfers", season=season))
    return harness, page


def _make_draft(
    season: int,
    save_name: str = SAVE_B,
    service: Optional[SimulatorUIService] = None,
    apply_to_pages: bool = False,
) -> Tuple[_Harness, DraftPage]:
    _shared_root()
    harness = _Harness(save_name, service=service, apply_to_pages=apply_to_pages)
    page = DraftPage(harness.context())
    harness.attach(page)
    page.apply_route(Route("draft", season=season))
    return harness, page


def _show_page(page: QWidget, size: Tuple[int, int] = (1440, 860)) -> None:
    page.resize(*size)
    page.show()
    QApplication.processEvents()


def _click_cell(view: QTableView, row: int, column: int, x_offset: Optional[int] = None) -> None:
    """在表格单元格上模拟一次左键单击（delegate editorEvent 路径）。"""
    rect = view.visualRect(view.model().index(row, column))
    pos = rect.center() if x_offset is None else QPoint(rect.left() + x_offset, rect.center().y())
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        pos,
    )


def _vertical_scroll_surfaces(widget: QWidget) -> List[QWidget]:
    """统计纵向滚动面；下拉框（QComboBox）的弹出视图是临时弹层，不计入。"""
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


def _assert_no_nested_scrolling(testcase: unittest.TestCase, page: QWidget, tables: Tuple[EntityTable, ...]) -> None:
    """§8.2 零嵌套滚动：恰有一个 QScrollArea 主滚动面；全展开表格不构成
    第二个纵向滚动面（滚动条策略 AlwaysOff 且 maximum == 0）；无只读文本框。"""
    scroll_areas = page.findChildren(QScrollArea)
    testcase.assertEqual(len(scroll_areas), 1)
    for table in tables:
        testcase.assertEqual(
            table.view.verticalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        testcase.assertEqual(
            table.view.verticalScrollBar().maximum(),
            0,
            "全展开表格存在内部纵向滚动（小框内滚动）",
        )
    # 滚动面总账 = 主滚动面 + 全展开表格（后者无内部滚动，非嵌套）。
    surfaces = _vertical_scroll_surfaces(page)
    testcase.assertEqual(len(surfaces), 1 + len(tables))
    testcase.assertEqual(page.findChildren(QTextEdit), [])
    testcase.assertEqual(page.findChildren(QPlainTextEdit), [])


def _assert_roster_integrity(testcase: unittest.TestCase, snapshot) -> None:
    """全部 40 队 × 11 人，位置 1/4/3/3，真实球员 ID 唯一。"""
    teams = snapshot.teams
    testcase.assertEqual(len(teams), 40)
    seen_ids = set()
    for team in teams:
        testcase.assertEqual(len(team.roster), 11, f"{team.name} 阵容人数异常")
        counts: Dict[str, int] = {}
        for player in team.roster:
            counts[player.position] = counts.get(player.position, 0) + 1
            if player.is_real:
                testcase.assertNotIn(player.player_id, seen_ids, f"真实球员 ID 重复：{player.player_id}")
                seen_ids.add(player.player_id)
        testcase.assertEqual(counts.get("GK"), 1, f"{team.name} 门将数量异常")
        testcase.assertEqual(counts.get("DF"), 4, f"{team.name} 后卫数量异常")
        testcase.assertEqual(counts.get("MF"), 3, f"{team.name} 中场数量异常")
        testcase.assertEqual(counts.get("FW"), 3, f"{team.name} 前锋数量异常")


def _db_transfer_rows(save_name: str, season: int) -> List[dict]:
    """直接读取存档 transfers 表（该赛季），用于与页面行对账。"""
    conn = sqlite3.connect(str(state_path(save_name)))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM transfers WHERE season_number = ? ORDER BY transfer_row_id",
            (season,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _db_draft_log(save_name: str, season: int) -> Optional[dict]:
    conn = sqlite3.connect(str(state_path(save_name)))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT log_json FROM drafts WHERE season_number = ? LIMIT 1", (season,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["log_json"]) if row is not None else None


def _db_team_ids(save_name: str) -> Dict[str, int]:
    conn = sqlite3.connect(str(state_path(save_name)))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT name, team_id FROM teams").fetchall()
    finally:
        conn.close()
    return {row["name"]: int(row["team_id"]) for row in rows}


def _expected_config_candidates(save_name: str, candidate_count: int) -> List[dict]:
    """独立重算配置候选预览（与引擎 _config_draft_candidates 同语义）。"""
    conn = sqlite3.connect(str(state_path(save_name)))
    conn.row_factory = sqlite3.Row
    try:
        meta = {
            row["key"]: json.loads(row["value_json"])
            for row in conn.execute("SELECT key, value_json FROM save_meta")
        }
        existing = {row["name"] for row in conn.execute("SELECT name FROM real_player_pool")}
    finally:
        conn.close()
    config = load_save_config(save_name)
    candidates: List[dict] = []
    index = int(meta.get("draft_pool_index", 0))
    while index < len(config.draft_players) and len(candidates) < candidate_count:
        template = config.draft_players[index]
        index += 1
        if template.name in existing:
            continue
        candidates.append({"name": template.name, "position": template.position})
    return candidates


def _trade_cards(page: QWidget) -> List[QFrame]:
    return [
        frame
        for frame in page.findChildren(QFrame)
        if str(frame.property("block_role") or "").startswith("transferCard_")
    ]


# -- 转会中心：审核卡与只读回退（存档 A） ---------------------------------------


class TransfersReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.service = SimulatorUIService()

    def _pending_of(self, save_name: str) -> List[dict]:
        return list(load_snapshot(save_name).pending_transfer_review)

    def test_review_cards_match_pending_data(self) -> None:
        pending = self._pending_of(SAVE_A)
        self.assertGreaterEqual(len(pending), 1)
        harness, page = _make_transfers(1, save_name=SAVE_A, service=self.service)
        self.assertEqual(len(page._pending_items), len(pending))
        self.assertEqual(sorted(page._decisions), sorted(item["trade_id"] for item in pending))

        cards = _trade_cards(page)
        self.assertEqual(len(cards), len(pending))
        team_ids = _db_team_ids(SAVE_A)
        for item, card in zip(page._pending_items, cards):
            texts = [label.text() for label in card.findChildren(QLabel)]
            joined = " ".join(texts)
            # 编号 / 双方总身价 / 差值明细。
            self.assertIn(str(item["trade_id"]), joined)
            self.assertIn(f"{float(item['team_a_total_value']):.2f}M", joined)
            self.assertIn(f"{float(item['team_b_total_value']):.2f}M", joined)
            self.assertIn(f"差值 {float(item['value_gap']):.2f}M", joined)
            # 球员明细（姓名链接 / 位置 / 能力 / 身价）。
            card_links = [link.text() for link in card.findChildren(EntityLink)]
            for player in [*item["team_a_players"], *item["team_b_players"]]:
                self.assertIn(player["name"], card_links)
                self.assertIn(str(player["ability"]), texts)
                self.assertIn(f"{float(player['market_value']):.2f}M", texts)
            # 双方球队名均为可点链接（指向稳定 team 路由）。
            for team_name in (item["team_a"], item["team_b"]):
                expected = Route("team", team=team_ids[team_name], season=1)
                self.assertIn(
                    expected,
                    [link.route for link in card.findChildren(EntityLink)],
                )
        # 默认批准；提交可用。
        for buttons in page._decisions.values():
            self.assertTrue(buttons["approve"].isChecked())
            self.assertFalse(buttons["reject"].isChecked())
        self.assertTrue(page._submit_button.isEnabled())

        # 安全约束：service 为 None 时审核卡只读展示（pending_actions 同源），
        # 决策与提交禁用。
        readonly_harness, readonly_page = _make_transfers(1, save_name=SAVE_A)
        self.assertEqual(len(readonly_page._pending_items), len(pending))
        self.assertTrue(readonly_page._pending_items)
        for buttons in readonly_page._decisions.values():
            self.assertFalse(buttons["approve"].isEnabled())
            self.assertFalse(buttons["reject"].isEnabled())
        self.assertFalse(readonly_page._submit_button.isEnabled())
        readonly_page.hide()

    def test_submit_mock_passes_exact_decisions(self) -> None:
        from unittest.mock import patch

        harness, page = _make_transfers(1, save_name=SAVE_A, service=self.service)
        first_id = page._pending_items[0]["trade_id"]
        page._decisions[first_id]["reject"].setChecked(True)
        page._decisions[first_id]["approve"].setChecked(False)
        # 只截获参数，不真正执行（避免污染共享存档）。
        fake_state = self.service.load_state(SAVE_A)
        with patch.object(self.service, "apply_transfer_review", return_value=fake_state) as spy:
            page._submit_button.click()
            QApplication.processEvents()
        self.assertEqual(spy.call_count, 1)
        save_arg, decisions_arg = spy.call_args.args
        self.assertEqual(save_arg, SAVE_A)
        self.assertEqual(
            decisions_arg,
            {item["trade_id"]: (item["trade_id"] != first_id) for item in page._pending_items},
        )

    def test_missing_season_shows_empty_state(self) -> None:
        harness, page = _make_transfers(99, save_name=SAVE_A, service=self.service)
        current = page._stack.currentWidget()
        self.assertIsNot(current, page._scroll)
        page.hide()


# -- 转会中心：历史表、链接合同与滚动（存档 A / C） ------------------------------


class TransfersHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.service = SimulatorUIService()

    def test_history_rows_match_database(self) -> None:
        db_rows = _db_transfer_rows(SAVE_C, 1)
        self.assertGreater(len(db_rows), 0)
        harness, page = _make_transfers(1, save_name=SAVE_C, service=self.service)
        table = page._history_table
        self.assertEqual(table.model.rowCount(), len(db_rows))
        for index, db_row in enumerate(db_rows):
            row = table.model.row_at(index)
            self.assertEqual(row.week, int(db_row["week_number"]))
            self.assertEqual(row.window, db_row["window"])
            self.assertEqual(row.team_a, db_row["team_a"])
            self.assertEqual(row.team_b, db_row["team_b"])
            self.assertEqual(row.status, db_row["status"])
            self.assertEqual(row.gap, float(db_row["value_gap"]))
            a_names = "、".join(player["name"] for player in json.loads(db_row["team_a_players_json"]))
            b_names = "、".join(player["name"] for player in json.loads(db_row["team_b_players_json"]))
            self.assertEqual(row.players, f"{a_names} ⇄ {b_names}")

    def test_history_team_and_player_links_route(self) -> None:
        harness, page = _make_transfers(1, save_name=SAVE_C, service=self.service)
        table = page._history_table
        team_a_column = _transfer_column_index(_TRANSFER_COLUMNS, "team_a")
        team_b_column = _transfer_column_index(_TRANSFER_COLUMNS, "team_b")
        players_column = _transfer_column_index(_TRANSFER_COLUMNS, "players")
        team_ids = _db_team_ids(SAVE_C)
        row0 = table.model.row_at(0)

        _show_page(page)
        before = len(harness.routes)
        # 甲队 / 乙队列单击 → 稳定 team 路由。
        _click_cell(table.view, 0, team_a_column)
        self.assertEqual(
            harness.routes[-1],
            Route("team", team=team_ids[row0.team_a], season=1),
        )
        _click_cell(table.view, 0, team_b_column)
        self.assertEqual(
            harness.routes[-1],
            Route("team", team=team_ids[row0.team_b], season=1),
        )
        self.assertEqual(len(harness.routes), before + 2)

        # 球员列逐名命中：单击第一个球员名（命中区间与 paint 排版同源）。
        metrics = QFontMetrics(table.view.font())
        first_name = row0.team_a_players[0]
        _click_cell(table.view, 0, players_column, x_offset=8 + metrics.horizontalAdvance(first_name) // 2)
        self.assertEqual(
            harness.routes[-1],
            Route("player", player=real_player_id(first_name), season=1),
        )

        # 行激活（双击 / Enter）不导航：审核页行不是导航入口。
        routes_before = list(harness.routes)
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(harness.routes, routes_before)
        page.hide()

    def test_season_selector_navigates_and_switches_history(self) -> None:
        harness, page = _make_transfers(2, save_name=SAVE_C, service=self.service, apply_to_pages=True)
        combo = page._season_combo
        self.assertIsNotNone(combo)
        self.assertEqual(combo.count(), 2)
        # 第 2 赛季尚无转会历史。
        self.assertEqual(page._history_table.model.rowCount(), 0)
        combo.setCurrentIndex(combo.findData(1))
        self.assertEqual(harness.routes[-1], Route("transfers", season=1))
        # 外壳应用新路由后页面按第 1 赛季刷新，历史 == transfers 表行数。
        self.assertEqual(page._season, 1)
        self.assertEqual(page._history_table.model.rowCount(), len(_db_transfer_rows(SAVE_C, 1)))
        self.assertFalse(page._history_hint.isVisibleTo(page))

    def test_history_empty_hint_on_save_without_history(self) -> None:
        harness, page = _make_transfers(1, save_name=SAVE_A, service=self.service)
        self.assertEqual(page._history_table.model.rowCount(), 0)
        self.assertTrue(page._history_hint.isVisibleTo(page))
        self.assertIn("该赛季暂无转会记录", page._history_hint.text())
        page.hide()

    def test_single_scroll_surface_both_sizes(self) -> None:
        # 有待审核卡（存档 A）与只有历史表（存档 C）两种关键状态。
        _, page_pending = _make_transfers(1, save_name=SAVE_A, service=self.service)
        _, page_history = _make_transfers(1, save_name=SAVE_C, service=self.service)
        for page in (page_pending, page_history):
            for size in WINDOW_SIZES:
                with self.subTest(size=size):
                    _show_page(page, size)
                    _assert_no_nested_scrolling(self, page, (page._history_table,))
            page.hide()


# -- 转会中心：真实提交（存档 S） ----------------------------------------------


class TransfersSubmitTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.service = SimulatorUIService()

    def test_submit_real_updates_state_history_and_status(self) -> None:
        pending = list(load_snapshot(SAVE_S).pending_transfer_review)
        self.assertGreaterEqual(len(pending), 1)
        history_before = len(_db_transfer_rows(SAVE_S, 1))
        harness, page = _make_transfers(1, save_name=SAVE_S, service=self.service)

        # 第一笔拒绝，其余维持默认批准。
        rejected_id = page._pending_items[0]["trade_id"]
        page._decisions[rejected_id]["reject"].setChecked(True)
        page._decisions[rejected_id]["approve"].setChecked(False)
        approved_count = len(pending) - 1

        _show_page(page)
        page._submit_button.click()
        QApplication.processEvents()

        # 提交后：pending 清空、历史增长、状态条反馈、拒绝路径状态“玩家拒绝”。
        snapshot = load_snapshot(SAVE_S)
        self.assertEqual(snapshot.pending_transfer_review, [])
        db_rows = _db_transfer_rows(SAVE_S, 1)
        self.assertEqual(len(db_rows), history_before + len(pending))
        self.assertEqual(len(page._pending_items), 0)
        self.assertEqual(page._history_table.model.rowCount(), len(db_rows))
        self.assertEqual(
            page._review_status_label.text(),
            f"已提交 {len(pending)} 笔（批准 {approved_count} · 拒绝 1 · 系统重算 0）",
        )
        rejected_rows = [row for row in db_rows if row["trade_id"] == rejected_id]
        self.assertEqual(len(rejected_rows), 1)
        self.assertFalse(bool(rejected_rows[0]["approved"]))
        self.assertEqual(rejected_rows[0]["status"], "玩家拒绝")

        # 阵容完整性不被写流程破坏：40 队 × 11 人、位置 1/4/3/3、真实 ID 唯一。
        _assert_roster_integrity(self, snapshot)
        page.hide()


# -- 选秀中心：等待录入与候选预览（存档 B / D） ----------------------------------


class DraftAwaitingTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.service = SimulatorUIService()

    def _resolve_ability_review(self, save_name: str) -> None:
        snapshot = load_snapshot(save_name)
        if snapshot.pending_ability_review:
            self.service.apply_ability_review(
                save_name, {item["name"]: True for item in snapshot.pending_ability_review}
            )

    def test_awaiting_state_matches_pending_and_readonly_fallback(self) -> None:
        self._resolve_ability_review(SAVE_B)  # 选秀要求其他待办先清空（UI 侧说明）
        pending = load_snapshot(SAVE_B).pending_draft
        self.assertEqual(pending.get("status"), "awaiting_input")
        candidate_count = int(pending["candidate_count"])

        harness, page = _make_draft(1, save_name=SAVE_B, service=self.service)
        self.assertIn("等待选秀录入", page._status_label.text())
        description = " ".join(label.text() for label in page._pending_slot.findChildren(QLabel))
        # 说明文案：候选人数 / 逆序轮询 / 位置配额。
        self.assertIn(f"本届计划选秀 {candidate_count} 人", description)
        self.assertIn("逆序轮询", description)
        self.assertIn("位置配额", description)
        self.assertIn("GK 1", description)
        # 候选预览（只读）== 配置候选池同语义重算。
        expected = _expected_config_candidates(SAVE_B, candidate_count)
        self.assertTrue(expected)
        self.assertEqual(page._candidate_preview, expected)
        self.assertTrue(page._confirm_button.isEnabled())

        # service 为 None：同一份 pending_actions 数据只读展示，确认禁用。
        readonly_harness, readonly_page = _make_draft(1, save_name=SAVE_B)
        self.assertEqual(readonly_page._pending.get("status"), "awaiting_input")
        self.assertEqual(readonly_page._candidate_preview, expected)
        self.assertFalse(readonly_page._confirm_button.isEnabled())
        readonly_page.hide()

    def test_confirm_mock_passes_empty_prospects(self) -> None:
        from unittest.mock import patch

        self._resolve_ability_review(SAVE_B)
        harness, page = _make_draft(1, save_name=SAVE_B, service=self.service)
        # 只截获参数，不真正执行（避免污染共享存档）。
        fake_state = self.service.load_state(SAVE_B)
        with patch.object(self.service, "apply_draft", return_value=fake_state) as spy:
            page._confirm_button.click()
            QApplication.processEvents()
        self.assertEqual(spy.call_count, 1)
        save_arg, prospects_arg = spy.call_args.args
        self.assertEqual(save_arg, SAVE_B)
        self.assertEqual(prospects_arg, [])

    def test_confirm_real_runs_draft_and_shows_results(self) -> None:
        self._resolve_ability_review(SAVE_D)
        candidate_count = int(load_snapshot(SAVE_D).pending_draft["candidate_count"])
        _assert_roster_integrity(self, load_snapshot(SAVE_D))

        harness, page = _make_draft(1, save_name=SAVE_D, service=self.service)
        _show_page(page)
        page._confirm_button.click()
        QApplication.processEvents()

        # 提交后：pending 清空，结果表行数 == candidate_count，身价全部 30.0。
        snapshot = load_snapshot(SAVE_D)
        self.assertEqual(snapshot.pending_draft, {})
        log = _db_draft_log(SAVE_D, 1)
        self.assertIsNotNone(log)
        self.assertEqual(len(log["results"]), candidate_count)
        table = page._results_table
        rows = [table.model.row_at(i) for i in range(table.model.rowCount())]
        self.assertEqual(len(rows), candidate_count)
        self.assertTrue(all(row.market_value == 30.0 for row in rows))
        self.assertTrue(all(row.round is not None and row.round >= 1 for row in rows))
        self.assertFalse(page._results_hint.isVisibleTo(page))

        # 球队 / 新秀链接路由。
        team_ids = _db_team_ids(SAVE_D)
        _click_cell(table.view, 0, _draft_column_index(_DRAFT_COLUMNS, "team_name"))
        self.assertEqual(
            harness.routes[-1],
            Route("team", team=team_ids[rows[0].team_name], season=1),
        )
        _click_cell(table.view, 0, _draft_column_index(_DRAFT_COLUMNS, "player_name"))
        self.assertEqual(
            harness.routes[-1],
            Route("player", player=real_player_id(rows[0].player_name), season=1),
        )

        # 选秀写入新秀后阵容完整性仍成立。
        _assert_roster_integrity(self, snapshot)
        page.hide()


# -- 选秀中心：结果展示 / 空状态 / 滚动（存档 A / C） ----------------------------


class DraftResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        _shared_root()
        self.service = SimulatorUIService()

    def test_archived_season_results_match_database_log(self) -> None:
        log = _db_draft_log(SAVE_C, 1)
        self.assertIsNotNone(log)
        self.assertGreater(len(log["results"]), 0)
        harness, page = _make_draft(1, save_name=SAVE_C, service=self.service)
        table = page._results_table
        self.assertEqual(table.model.rowCount(), len(log["results"]))
        team_ids = _db_team_ids(SAVE_C)
        for index, result in enumerate(log["results"]):
            row = table.model.row_at(index)
            self.assertEqual(row.team_name, result["team_name"])
            self.assertEqual(row.player_name, result["name"])
            self.assertEqual(row.position, result["position"])
            self.assertEqual(row.ability, int(result["ability"]))
            self.assertEqual(row.market_value, float(result["market_value"]))
            self.assertEqual(row.market_value_text, f"{float(result['market_value']):.2f}M")
        # 球队 / 新秀链接路由正确（首行）。
        row0 = table.model.row_at(0)
        self.assertEqual(page._team_route_for_row(row0), Route("team", team=team_ids[row0.team_name], season=1))
        self.assertEqual(
            page._player_route_for_row(row0),
            Route("player", player=real_player_id(row0.player_name), season=1),
        )
        # 行激活不导航。
        _show_page(page)
        routes_before = list(harness.routes)
        table.view.activated.emit(table.view.model().index(0, 0))
        self.assertEqual(harness.routes, routes_before)
        page.hide()

    def test_season_without_draft_shows_hint(self) -> None:
        harness, page = _make_draft(1, save_name=SAVE_A, service=self.service)
        self.assertIn("没有选秀记录", page._status_label.text())
        self.assertTrue(page._results_hint.isVisibleTo(page))
        self.assertIn("该赛季没有选秀记录", page._results_hint.text())
        self.assertFalse(page._results_table.isVisibleTo(page))
        page.hide()

    def test_season_selector_switches_results_and_hint(self) -> None:
        harness, page = _make_draft(2, save_name=SAVE_C, service=self.service, apply_to_pages=True)
        combo = page._season_combo
        self.assertIsNotNone(combo)
        self.assertEqual(combo.count(), 2)
        # 第 2 赛季尚未进行到选秀阶段 → 空状态。
        self.assertTrue(page._results_hint.isVisibleTo(page))
        combo.setCurrentIndex(combo.findData(1))
        self.assertEqual(harness.routes[-1], Route("draft", season=1))
        self.assertEqual(page._season, 1)
        self.assertEqual(
            page._results_table.model.rowCount(),
            len(_db_draft_log(SAVE_C, 1)["results"]),
        )
        self.assertFalse(page._results_hint.isVisibleTo(page))

    def test_single_scroll_surface_both_sizes(self) -> None:
        # 等待录入（存档 B）与结果展示（存档 C 第 1 赛季）两种关键状态。
        snapshot_b = load_snapshot(SAVE_B)
        if snapshot_b.pending_ability_review:
            self.service.apply_ability_review(
                SAVE_B, {item["name"]: True for item in snapshot_b.pending_ability_review}
            )
        _, page_awaiting = _make_draft(1, save_name=SAVE_B, service=self.service)
        _, page_results = _make_draft(1, save_name=SAVE_C, service=self.service)
        for page in (page_awaiting, page_results):
            for size in WINDOW_SIZES:
                with self.subTest(size=size):
                    _show_page(page, size)
                    _assert_no_nested_scrolling(self, page, (page._results_table,))
            page.hide()


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

    def test_capture_states_both_sizes(self) -> None:
        _app()
        _shared_root()
        service = SimulatorUIService()
        snapshot_b = load_snapshot(SAVE_B)
        if snapshot_b.pending_ability_review:
            service.apply_ability_review(
                SAVE_B, {item["name"]: True for item in snapshot_b.pending_ability_review}
            )
        harness, transfers_pending = _make_transfers(1, save_name=SAVE_A, service=service)
        harness, transfers_history = _make_transfers(1, save_name=SAVE_C, service=service)
        harness, draft_awaiting = _make_draft(1, save_name=SAVE_B, service=service)
        harness, draft_results = _make_draft(1, save_name=SAVE_C, service=service)
        produced: List[Path] = []
        for width, height in WINDOW_SIZES:
            produced.append(
                self._capture(transfers_pending, (width, height), f"transfers_pending_{width}x{height}.png")
            )
            produced.append(
                self._capture(transfers_history, (width, height), f"transfers_history_{width}x{height}.png")
            )
            produced.append(
                self._capture(draft_awaiting, (width, height), f"draft_awaiting_{width}x{height}.png")
            )
            produced.append(
                self._capture(draft_results, (width, height), f"draft_results_{width}x{height}.png")
            )

        for path in produced:
            self.assertTrue(path.exists(), f"缺少截图：{path}")
            self.assertGreater(path.stat().st_size, 0, f"截图为空：{path}")


if __name__ == "__main__":
    unittest.main()
