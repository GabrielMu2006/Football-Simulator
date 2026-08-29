"""阶段 0 规则冻结测试共用工具。

只使用标准库。通过阶段 0 加入的最小注入接口：
- ``runtime.set_save_root_override`` 把存档根目录重定向到独立临时目录；
- ``state.set_rng_provider`` 把随机源替换为固定种子的 ``random.Random``。

生产默认行为不受影响；测试不触碰项目 ``saves/`` 目录。
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
        self._tmp_path = Path(self._tmp_dir).resolve()
        sim_runtime.set_save_root_override(self._tmp_path)
        sim_state.set_rng_provider(seeded_provider())
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        sim_state.set_rng_provider(None)
        sim_runtime.set_save_root_override(None)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

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
    if current == project_saves:
        raise RuntimeError(
            "存档根目录未被重定向，拒绝运行（防止写入项目 saves/）。"
            "请使用 FreezeTestCase 或 isolate_save_root 上下文。"
        )


class isolate_save_root:
    """上下文管理器：把存档根目录重定向到独立临时目录并注入固定随机源。"""

    def __init__(self, seed: int = TEST_SEED) -> None:
        self._seed = seed
        self._tmp_dir: Optional[str] = None

    def __enter__(self) -> Path:
        self._tmp_dir = tempfile.mkdtemp(prefix="fs_iso_")
        self._tmp_path = Path(self._tmp_dir).resolve()
        sim_runtime.set_save_root_override(self._tmp_path)
        sim_state.set_rng_provider(seeded_provider(self._seed))
        assert_save_root_isolated()
        return self._tmp_path

    def __exit__(self, exc_type, exc, tb) -> None:
        sim_state.set_rng_provider(None)
        sim_runtime.set_save_root_override(None)
        if self._tmp_dir:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)


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
