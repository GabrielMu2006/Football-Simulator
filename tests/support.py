"""阶段 0 规则冻结测试共用工具。

只使用标准库。测试隔离通过两个注入接口完成：
- ``runtime.set_data_root_override`` 把**整个数据根**（saves/、backups/、
  trash/、current_save.txt 与存档配置）重定向到独立临时目录；
- ``state.set_rng_provider`` 把随机源替换为固定种子的 ``random.Random``。

清理只使用夹具开始时保存下来的绝对临时路径，绝不在 override 撤销后重新
解析生产路径。生产默认行为不受影响；测试不触碰项目 ``saves/``、
``backups/``、``trash/``、``current_save.txt`` 与总配置。
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.data import _build_default_roster
from football_simulator.models import Team
from football_simulator.persistence.save_repository import SaveRepository
from football_simulator.state import (
    SAVE_DATABASE_FILE_NAME,
    apply_ability_review_decisions,
    apply_draft_prospects,
    apply_transfer_review_decisions,
    initialize_save_state,
    load_save_snapshot,
    simulate_next_week,
)

TEST_SEED = 20260828
DEFAULT_SAVE_NAME = "freeze"
DEFAULT_ABILITY = 50

STAT_FIELDS = (
    "goals",
    "assists",
    "chances_created",
    "successful_defenses",
    "successful_saves",
    "clean_sheets",
)


def seeded_provider(seed: int = TEST_SEED):
    return lambda: random.Random(seed)


# -- 隔离路径安全阀 ---------------------------------------------------------

_TEMP_PARENT = Path(tempfile.gettempdir()).resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def assert_cleanup_path_is_temporary(path: Path) -> Path:
    """清理目标必须是项目外、位于系统临时目录内的绝对路径。

    这是 T-000 的事故防护：任何删除操作只允许作用于夹具保留的绝对临时
    路径，防止再次出现“清理阶段重新解析生产路径”的误删。
    """
    resolved = Path(path).resolve()
    if _is_within(resolved, PROJECT_ROOT):
        raise RuntimeError(f"拒绝清理项目目录内的路径：{resolved}")
    if not _is_within(resolved, _TEMP_PARENT):
        raise RuntimeError(f"拒绝清理系统临时目录之外的路径：{resolved}")
    if resolved == _TEMP_PARENT:
        raise RuntimeError("拒绝清理系统临时目录本身。")
    return resolved


def assert_no_production_writes() -> None:
    """测试主动调用：确认当前数据根不是生产位置。"""
    if sim_runtime.data_root().resolve() == PROJECT_ROOT.resolve():
        raise RuntimeError("数据根未重定向，拒绝运行：测试可能写入项目目录。")


def install_test_data_root(tmp_dir: str) -> Path:
    """把数据根与总配置来源同时指向临时目录，并返回该绝对路径。"""
    root = Path(tmp_dir).resolve()
    assert_cleanup_path_is_temporary(root)
    sim_runtime.set_data_root_override(root)
    config_target = root / sim_runtime.SHARED_CONFIG_FILE_NAME
    config_target.parent.mkdir(parents=True, exist_ok=True)
    if not config_target.exists():
        shutil.copy2(PROJECT_ROOT / sim_runtime.SHARED_CONFIG_FILE_NAME, config_target)
    sim_runtime.set_config_path_override(config_target)
    assert_no_production_writes()
    return root


def reset_test_data_root() -> None:
    """撤销注入。调用方随后只应使用自己保存的临时路径做清理。"""
    sim_runtime.set_data_root_override(None)
    sim_runtime.set_config_path_override(None)
    sim_state.set_rng_provider(None)


# -- QApplication 生命周期 --------------------------------------------------

_QT_APP = None


def ensure_qapp():
    """进程内唯一的 QApplication（offscreen）。

    统一在测试进程全程持有同一个实例：反复构造/销毁 QApplication 会让
    已缓存的 Qt 对象指向失效的 application，是此前整套串跑 exit139 的
    高危来源之一。返回 None 表示当前 Python 没有 PySide6。
    """
    global _QT_APP
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return None
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
        _QT_APP.setQuitOnLastWindowClosed(False)
    return _QT_APP


# 所有测试模块共享同一个 QApplication。历史 exit139 发生在后续测试模块再次
# 构造 QApplication 时：前一实例已被回收，但模块级页面/控件仍然存活。
# 在 support 导入时创建并永久持有唯一实例，避免“应用换了、Qt 对象没换”。
_QT_APP = ensure_qapp()


def cleanup_qt_widgets() -> int:
    """关闭并删除本模块创建的全部顶层 Qt 控件。

    旧页面测试长期持有数十个页面（累计 2 万+ widget），Qt 的全局 widget
    列表在后续模块分配时可能失效并崩溃。每个测试模块结束时调用本函数，
    让控件在自己的模块内完成 close/deferred delete，而不是泄漏到整套串跑。
    """
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return 0
    app = QApplication.instance()
    if app is None:
        return 0
    app.processEvents()
    removed = 0
    for _ in range(3):
        widgets = [widget for widget in QApplication.topLevelWidgets()]
        if not widgets:
            break
        for widget in widgets:
            try:
                widget.close()
                widget.deleteLater()
                removed += 1
            except RuntimeError:
                continue
        app.sendPostedEvents(None, int(QEvent.DeferredDelete))
        app.processEvents()
        import gc

        gc.collect()
    app.sendPostedEvents(None, int(QEvent.DeferredDelete))
    app.processEvents()
    return removed


def make_teams(names: List[str], division: str = "一级联赛") -> List[Team]:
    return [
        Team(name=name, division=division, roster=tuple(_build_default_roster(name, DEFAULT_ABILITY)))
        for name in names
    ]


class FreezeTestCase(unittest.TestCase):
    """每个用例使用独立临时存档根目录与固定随机源。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="fs_freeze_")
        # macOS 上 /var 是 /private/var 的符号链接，resolve 与注入钩子保持一致。
        self._tmp_path = install_test_data_root(self._tmp_dir)
        sim_state.set_rng_provider(seeded_provider())
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        # 先撤销注入，再只用 setUp 时保存的绝对临时路径清理。
        reset_test_data_root()
        target = assert_cleanup_path_is_temporary(self._tmp_path)
        shutil.rmtree(target, ignore_errors=True)

    # -- 常用断言辅助 ---------------------------------------------------

    def assertRosterIntegrity(self, snapshot) -> None:
        """全部 40 队 × 11 人，位置 1/4/3/3，真实球员 ID 唯一。"""
        teams = snapshot.teams
        self.assertEqual(len(teams), 40)
        seen_ids = set()
        for team in teams:
            self.assertEqual(len(team.roster), 11, f"{team.name} 阵容人数异常")
            counts: Dict[str, int] = {}
            for player in team.roster:
                counts[player.position] = counts.get(player.position, 0) + 1
                if player.is_real:
                    self.assertNotIn(player.player_id, seen_ids, f"真实球员 ID 重复：{player.player_id}")
                    seen_ids.add(player.player_id)
            self.assertEqual(counts.get("GK"), 1, f"{team.name} 门将数量异常")
            self.assertEqual(counts.get("DF"), 4, f"{team.name} 后卫数量异常")
            self.assertEqual(counts.get("MF"), 3, f"{team.name} 中场数量异常")
            self.assertEqual(counts.get("FW"), 3, f"{team.name} 前锋数量异常")


# -- 存档流程辅助 -------------------------------------------------------


def assert_save_root_isolated() -> None:
    """防呆闸：指纹等批量写流程必须在重定向后的临时目录中运行。"""
    current = sim_runtime.save_root().resolve()
    project_saves = (PROJECT_ROOT / "saves").resolve()
    if current == project_saves or sim_runtime.data_root().resolve() == PROJECT_ROOT.resolve():
        raise RuntimeError(
            "数据根未被重定向，拒绝运行（防止写入项目 saves/ 与 current_save.txt）。"
            "请使用 FreezeTestCase 或 isolate_save_root 上下文。"
        )


class isolate_save_root:
    """上下文管理器：把存档根目录重定向到独立临时目录并注入固定随机源。"""

    def __init__(self, seed: int = TEST_SEED) -> None:
        self._seed = seed
        self._tmp_dir: Optional[str] = None

    def __enter__(self) -> Path:
        self._tmp_dir = tempfile.mkdtemp(prefix="fs_iso_")
        self._tmp_path = install_test_data_root(self._tmp_dir)
        sim_state.set_rng_provider(seeded_provider(self._seed))
        assert_save_root_isolated()
        return self._tmp_path

    def __exit__(self, exc_type, exc, tb) -> None:
        reset_test_data_root()
        if self._tmp_dir:
            target = assert_cleanup_path_is_temporary(Path(self._tmp_dir))
            shutil.rmtree(target, ignore_errors=True)


def create_save(save_name: str = DEFAULT_SAVE_NAME):
    """初始化（第 1 赛季）或开启新赛季。"""
    return initialize_save_state(save_name)


def load_snapshot(save_name: str = DEFAULT_SAVE_NAME):
    return load_save_snapshot(save_name)


def state_path(save_name: str = DEFAULT_SAVE_NAME) -> Path:
    """存档数据库文件路径（阶段 1 起为 save.sqlite3）。"""
    return sim_runtime.save_root() / save_name / SAVE_DATABASE_FILE_NAME


def load_state_json(save_name: str = DEFAULT_SAVE_NAME) -> dict:
    """读取存档的“工作字典”（与旧 state.json 同形），供测试刻画内部状态。"""
    repo = SaveRepository.open(state_path(save_name).parent, save_name)
    try:
        state = repo.load_state()
    finally:
        repo.close()
    if state is None:
        raise FileNotFoundError(f"存档 '{save_name}' 尚未初始化。")
    sim_state._normalize_rosters_and_registry(state)
    return state


def resolve_pending(save_name: str = DEFAULT_SAVE_NAME):
    """按“能力全部保留、转会全部批准、选秀使用配置候选”处理当前待办。"""
    snap = load_snapshot(save_name)
    if snap.pending_ability_review:
        decisions = {item["name"]: True for item in snap.pending_ability_review}
        apply_ability_review_decisions(save_name, decisions)
        snap = load_snapshot(save_name)
    if snap.pending_transfer_review:
        decisions = {item["trade_id"]: True for item in snap.pending_transfer_review}
        apply_transfer_review_decisions(save_name, decisions)
        snap = load_snapshot(save_name)
    if snap.pending_draft.get("status") == "awaiting_input":
        apply_draft_prospects(save_name, [])
        snap = load_snapshot(save_name)
    return snap


def advance_week(save_name: str = DEFAULT_SAVE_NAME):
    resolve_pending(save_name)
    return simulate_next_week(save_name)


def run_season(save_name: str = DEFAULT_SAVE_NAME, max_weeks: int = 60) -> int:
    """推进到当前赛季结束并处理赛季末残留待办，返回实际推进周数。

    第 52 周本身是夏窗周：模拟完成后会立即生成新的转会待办，因此赛季
    “完整结束”的标准流程是处理完这批待办后再初始化新赛季。
    """
    weeks_run = 0
    while True:
        snap = load_snapshot(save_name)
        if snap.season_complete:
            resolve_pending(save_name)
            return weeks_run
        advance_week(save_name)
        weeks_run += 1
        if weeks_run > max_weeks:
            raise AssertionError(f"赛季推进超过 {max_weeks} 周，疑似死循环。")


def run_weeks(save_name: str, count: int) -> None:
    for _ in range(count):
        advance_week(save_name)


# -- 基线指纹 -----------------------------------------------------------


def master_config_sha256() -> str:
    data = sim_runtime.shared_config_path().read_bytes()
    return hashlib.sha256(data).hexdigest()


def collect_season_fingerprint(save_name: str = DEFAULT_SAVE_NAME) -> dict:
    """赛季结束时刻采集逐周比分与结算摘要指纹。

    前提：当前存档刚跑完一个完整赛季（season_complete=True）。
    """
    snap = load_snapshot(save_name)
    state_json = load_state_json(save_name)

    weeks = []
    for simulated_week in state_json.get("simulated_weeks", []):
        matches = {}
        for key in ("premier_matchdays", "second_matchdays", "cup_matchdays", "playoff_matchdays"):
            entries = []
            for matchday in simulated_week.get(key, []):
                results = [
                    f'{result["home_team"]} {result["home_goals"]}-{result["away_goals"]} {result["away_team"]}'
                    for result in matchday.get("results", [])
                ]
                entries.append({"round": matchday.get("round_number"), "results": results})
            matches[key] = entries
        weeks.append({"week_number": simulated_week["week_number"], "kind": simulated_week["kind"], "matches": matches})

    awards = sim_state._build_season_awards(snap)
    competition_awards = {}
    for competition, values in awards.get("competitions", {}).items():
        competition_awards[competition] = {
            "top_scorer": values["top_scorer"]["label"] if values.get("top_scorer") else None,
            "assist_leader": values["assist_leader"]["label"] if values.get("assist_leader") else None,
            "mvp": values["mvp"]["label"] if values.get("mvp") else None,
        }

    return {
        "season_number": snap.season_number,
        "weeks": weeks,
        "final": {
            "premier_table": [
                [row.team.name, row.points, row.goals_for, row.goals_against] for row in snap.premier_table
            ],
            "second_table": [
                [row.team.name, row.points, row.goals_for, row.goals_against] for row in snap.second_table
            ],
            "cup_champions": snap.cup_champions,
            "last_transition": state_json.get("last_transition"),
            "top20": [
                [item["rank"], item["label"], item["team_name"], item["score"]] for item in awards.get("top20", [])
            ],
            "competition_awards": competition_awards,
            "next_premier_team_names": state_json.get("next_premier_team_names"),
            "next_second_team_names": state_json.get("next_second_team_names"),
        },
    }


def run_three_season_fingerprint(save_name: str = DEFAULT_SAVE_NAME, seasons: int = 3) -> List[dict]:
    fingerprints: List[dict] = []
    for _ in range(seasons):
        create_save(save_name)
        run_season(save_name)
        fingerprints.append(collect_season_fingerprint(save_name))
    return fingerprints


def baseline_path() -> Path:
    return Path(__file__).resolve().parent / "baseline" / "three_season_fingerprint.json"


def load_baseline() -> Optional[dict]:
    path = baseline_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def transfer_approval_controls(page):
    """The visible approval control; modern macOS uses explicit choice buttons."""
    from PySide6.QtWidgets import QCheckBox, QPushButton
    return page.findChildren(QPushButton, "TransferAccept") or page.findChildren(QCheckBox)


def choose_transfer(page, index, approved):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    buttons = page.findChildren(QPushButton, "TransferAccept" if approved else "TransferReject")
    if buttons:
        QTest.mouseClick(buttons[index], Qt.LeftButton)
    else:
        check = transfer_approval_controls(page)[index]
        if check.isChecked() != approved:
            QTest.mouseClick(check, Qt.LeftButton)
