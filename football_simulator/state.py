import random
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from football_simulator.data import (
    REAL_PLAYER_ABILITY_MAX,
    REAL_PLAYER_ABILITY_MIN,
    RealPlayerProfile,
    build_real_player_pool,
    create_league_teams_from_save,
    load_save_config,
    real_player_id,
)
from football_simulator.domain.formulas import calculate_market_value as _calculate_market_value
from football_simulator.domain.formulas import calculate_player_rating as _calculate_player_rating
from football_simulator.domain.standings import apply_table_result as _apply_table_result
from football_simulator.domain.standings import group_by_metric as _group_by_metric
from football_simulator.domain.standings import head_to_head_tuple as _head_to_head_tuple
from football_simulator.domain.standings import rank_table_rows as _rank_table_rows
from football_simulator.match_engine import simulate_match
from football_simulator.models import (
    Fixture,
    FORMATION_RULES,
    MatchResult,
    MatchdayReport,
    Player,
    PlayerSeasonStats,
    PlayerStatDelta,
    POSITION_DEFENDER,
    POSITION_FORWARD,
    POSITION_GOALKEEPER,
    POSITION_MIDFIELDER,
    TableRow,
    Team,
    TeamSeasonStats,
    WeekScheduleEntry,
)
from football_simulator.persistence.save_repository import SaveRepository
from football_simulator.runtime import normalize_save_name, save_root
from football_simulator.schedule import CUP_EVENT_LABELS, SUMMER_BREAK_WEEKS, TOTAL_WEEKS, WINTER_BREAK_WEEKS, build_league_schedule, build_week_calendar


SAVE_DATABASE_FILE_NAME = "save.sqlite3"
PREMIER_DIVISION = "一级联赛"
SECOND_DIVISION = "次级联赛"
PLAYOFF_COMPETITION = "升级附加赛"
WINNERS_CUP = "优胜者杯"
CHALLENGE_CUP = "挑战杯"
SUPER_CUP = "超级杯"

PREMIER_HONOR_POINTS = {
    1: 120,
    2: 88,
    3: 72,
    4: 60,
    5: 52,
    6: 46,
    7: 41,
    8: 37,
    9: 33,
    10: 30,
    11: 27,
    12: 24,
    13: 21,
    14: 18,
    15: 15,
    16: 12,
    17: 9,
    18: 6,
    19: 3,
    20: 1,
}
WINNERS_CUP_HONOR_POINTS = {
    "冠军": 90,
    "亚军": 72,
    "四强": 56,
    "八强": 42,
    "小组第三": 24,
    "小组第四": 18,
}
CHALLENGE_CUP_HONOR_POINTS = {
    "冠军": 65,
    "亚军": 52,
    "四强": 40,
    "八强": 30,
    "十六强": 20,
    "三十二强": 12,
}
SUPER_CUP_HONOR_POINTS = {
    "冠军": 45,
    "亚军": 28,
    "四强": 16,
}
AWARD_COMPETITIONS = (PREMIER_DIVISION, WINNERS_CUP, CHALLENGE_CUP, SUPER_CUP)

WINTER_SETTLEMENT_WEEK = 24
FINAL_SETTLEMENT_WEEK = 49

# 次级联赛权重（用户确认 #3）：身价/Top20 计算时次级联赛权重要远低于一级。
# 该值通过 足球模拟器总配置.json 的 second_division_weight 可选覆盖。
SECOND_LEAGUE_WEIGHT = 0.35

# 随机源注入（测试专用接口）。生产默认仍是 random.SystemRandom，与历史行为
# 完全一致；测试通过 set_rng_provider 注入可复现随机源。注入只允许替换随机
# 源本身，不得改变公式或随机调用顺序。
_RNG_PROVIDER: Callable[[], random.Random] = random.SystemRandom


def _rng() -> random.Random:
    return _RNG_PROVIDER()


def _league_weight_for_team(team_name: str, second_team_names: Set[str]) -> float:
    """球队联赛权重：次级联赛 = SECOND_LEAGUE_WEIGHT，其余 = 1.0。"""
    return SECOND_LEAGUE_WEIGHT if team_name in second_team_names else 1.0


def _apply_cup_activation_to_weeks(
    weeks: List[WeekScheduleEntry],
    cup_state: dict,
) -> List[WeekScheduleEntry]:
    """按杯赛是否激活修饰赛历（用户确认 #11）。

    未激活的杯赛事件从周次中移除；移除后仍无联赛轮次则变为“无比赛周”，
    避免第 1 赛季出现“联赛周 + 优胜者杯小组赛第1轮”但实际没有杯赛的标签。
    """
    active_prefixes = set()
    if cup_state.get("winners_cup", {}).get("active"):
        active_prefixes.add("winners_cup_")
    if cup_state.get("challenge_cup", {}).get("active"):
        active_prefixes.add("challenge_cup_")
    if cup_state.get("super_cup", {}).get("active"):
        active_prefixes.add("super_cup_")

    def is_active(event_key: str) -> bool:
        return any(event_key.startswith(prefix) for prefix in active_prefixes)

    decorated: List[WeekScheduleEntry] = []
    for week in weeks:
        kept = tuple(event_key for event_key in week.cup_events if is_active(event_key))
        if week.kind == "league_week":
            label = "联赛周"
            if kept:
                cup_labels = " / ".join(CUP_EVENT_LABELS[key] for key in kept)
                label = f"联赛周 + {cup_labels}"
            decorated.append(
                WeekScheduleEntry(
                    week_number=week.week_number,
                    label=label,
                    kind="league_week",
                    premier_round_numbers=week.premier_round_numbers,
                    second_round_numbers=week.second_round_numbers,
                    cup_events=kept,
                )
            )
        elif week.kind == "cup_week":
            if kept:
                label = " / ".join(CUP_EVENT_LABELS[key] for key in kept)
                decorated.append(
                    WeekScheduleEntry(
                        week_number=week.week_number,
                        label=label,
                        kind="cup_week",
                        cup_events=kept,
                    )
                )
            else:
                decorated.append(
                    WeekScheduleEntry(
                        week_number=week.week_number,
                        label="无比赛周",
                        kind="open_week",
                    )
                )
        else:
            decorated.append(week)
    return decorated


def set_rng_provider(provider: Optional[Callable[[], random.Random]]) -> None:
    global _RNG_PROVIDER
    _RNG_PROVIDER = provider if provider is not None else random.SystemRandom


@dataclass
class SaveSnapshot:
    save_name: str
    season_number: int
    current_week: int
    season_complete: bool
    premier_teams: List[Team]
    second_teams: List[Team]
    weeks: List[WeekScheduleEntry]
    simulated_weeks: List[dict]
    premier_table: List[TableRow]
    second_table: List[TableRow]
    player_stats: List[PlayerSeasonStats]
    team_stats: List[TeamSeasonStats]
    history: List[dict]
    ranking_playoffs: Dict[str, list[dict]]
    cup_state: dict
    cup_champions: Dict[str, Optional[str]]
    next_premier_team_names: List[str]
    next_second_team_names: List[str]
    real_player_pool: List[RealPlayerProfile]
    pending_ability_review: List[dict]
    pending_transfer_review: List[dict]
    pending_draft: dict
    settlement_cache: dict
    transfer_history: List[dict]

    @property
    def teams(self) -> List[Team]:
        return [*self.premier_teams, *self.second_teams]

    @property
    def table(self) -> List[TableRow]:
        return self.premier_table


@dataclass
class WeekSimulationResult:
    snapshot: SaveSnapshot
    week: WeekScheduleEntry
    premier_matchdays: List[MatchdayReport]
    second_matchdays: List[MatchdayReport]
    cup_matchdays: List[MatchdayReport]
    playoff_matchdays: List[MatchdayReport]
    season_completed_now: bool

    @property
    def matchdays(self) -> List[MatchdayReport]:
        return self.premier_matchdays


def initialize_save_state(save_name: str) -> SaveSnapshot:
    with _state_transaction(save_name, create=True):
        rng = _rng()
        config = load_save_config(save_name, rng=rng)
        previous_state = _load_state_json_if_exists(save_name)

        history: List[dict] = []
        previous_season = 0
        if previous_state is not None:
            if previous_state.get("pending_ability_review"):
                raise ValueError("当前存档还有未处理的赛季末能力变动，请先完成审核。")
            if previous_state.get("pending_transfer_review"):
                raise ValueError("当前存档还有未处理的转会审核，请先完成审核。")
            if previous_state.get("pending_draft", {}).get("status") == "awaiting_input":
                raise ValueError("当前存档赛季已结束，请先完成选秀。")
            history = previous_state.get("history", [])
            previous_season = int(previous_state.get("season_number", 0))
            if not previous_state.get("season_complete"):
                # 旧 JSON 语义：未完成赛季被重新初始化时整体丢弃（不归档、
                # 直接进入下一赛季编号）。SQLite 下清空被放弃赛季的比赛、
                # 统计等派生数据，避免孤儿行残留。
                active_repo = _active_repo()
                if active_repo is not None:
                    active_repo.reset_season_data(previous_season)
            premier_team_names = previous_state.get("next_premier_team_names", previous_state.get("premier_team_names", config.premier_teams))
            second_team_names = previous_state.get("next_second_team_names", previous_state.get("second_team_names", config.second_division_teams))
            real_player_pool = _deserialize_real_player_pool(previous_state.get("real_player_pool", []))
            if not real_player_pool:
                real_player_pool = build_real_player_pool(config, rng)
            assigned_real_players = _extract_assigned_real_players(previous_state, real_player_pool)
        else:
            premier_team_names = config.premier_teams
            second_team_names = config.second_division_teams
            real_player_pool = build_real_player_pool(config, rng)
            assigned_real_players = None

        premier_teams, second_teams = create_league_teams_from_save(
            config,
            rng,
            list(premier_team_names),
            list(second_team_names),
            real_player_pool=real_player_pool,
            assigned_real_players=assigned_real_players,
        )
        weeks = build_week_calendar(build_league_schedule(premier_teams))
        previous_archive = history[-1] if history else None
        cup_state = _initialize_cup_state(
            season_number=previous_season + 1,
            previous_archive=previous_archive,
            premier_teams=premier_teams,
            second_teams=second_teams,
            rng=rng,
        )
        # 未激活的杯赛不进入赛历标签（用户确认 #11）。
        weeks = _apply_cup_activation_to_weeks(weeks, cup_state)

        state = {
            "save_name": save_name,
            "season_number": previous_season + 1,
            "current_week": 0,
            "season_complete": False,
            "premier_team_names": [team.name for team in premier_teams],
            "second_team_names": [team.name for team in second_teams],
            "next_premier_team_names": [team.name for team in premier_teams],
            "next_second_team_names": [team.name for team in second_teams],
            "premier_teams": [_serialize_team(team) for team in premier_teams],
            "second_teams": [_serialize_team(team) for team in second_teams],
            "weeks": [_serialize_week(week) for week in weeks],
            "simulated_weeks": [],
            "promotion_playoff": {},
            "ranking_playoffs": {},
            "cup_state": cup_state,
            "history": history,
            "real_player_pool": _serialize_real_player_pool(real_player_pool),
            "pending_ability_review": [],
            "pending_transfer_review": [],
            "pending_draft": {},
            "transfer_history": previous_state.get("transfer_history", []) if previous_state else [],
            "draft_pool_index": 0,
            "settlement_cache": {"winter": {}, "final": {}},
            "player_registry": _serialize_player_registry([*premier_teams, *second_teams]),
        }
        _write_state_json(save_name, state)
    return build_snapshot_from_state(state)


def simulate_next_week(save_name: str) -> WeekSimulationResult:
    with _state_transaction(save_name):
        state = _load_state_json(save_name)
        if state.get("season_complete"):
            raise ValueError("当前赛季已结束，请先初始化新赛季再继续。")
        if state.get("pending_ability_review"):
            raise ValueError("夏窗前还有未处理的真实球员能力变动，请先完成审核。")
        if state.get("pending_transfer_review"):
            raise ValueError("当前还有未处理的转会审核，请先完成审核。")
        if state.get("pending_draft", {}).get("status") == "awaiting_input":
            raise ValueError("夏窗前还有待处理选秀，请先完成选秀。")

        snapshot = build_snapshot_from_state(state)
        if snapshot.current_week >= len(snapshot.weeks):
            raise ValueError("当前赛季已经没有剩余周次。")

        week = snapshot.weeks[snapshot.current_week]
        rng = _rng()
        premier_schedule_by_round = {
            fixtures[0].round_number: fixtures for fixtures in build_league_schedule(snapshot.premier_teams)
        }
        second_schedule_by_round = {
            fixtures[0].round_number: fixtures for fixtures in build_league_schedule(snapshot.second_teams)
        }

        premier_matchdays: List[MatchdayReport] = []
        second_matchdays: List[MatchdayReport] = []
        cup_matchdays: List[MatchdayReport] = []
        playoff_matchdays: List[MatchdayReport] = []

        for round_number in week.premier_round_numbers:
            matchday = MatchdayReport(round_number=round_number, competition=PREMIER_DIVISION)
            for fixture in premier_schedule_by_round[round_number]:
                matchday.results.append(simulate_match(fixture, rng))
            premier_matchdays.append(matchday)

        for round_number in week.second_round_numbers:
            matchday = MatchdayReport(round_number=round_number, competition=SECOND_DIVISION)
            for fixture in second_schedule_by_round[round_number]:
                # 次级联赛完整模拟（用户确认 #3）：与顶级同引擎，产出六项统计与事件。
                matchday.results.append(simulate_match(fixture, rng))
            second_matchdays.append(matchday)

        if week.cup_events:
            cup_matchdays = _simulate_cup_events(state, snapshot, week, rng)

        if week.kind == "promotion_playoff":
            playoff_matchdays = _simulate_promotion_playoff_stage(state, snapshot, week, rng)

        state["simulated_weeks"].append(
            {
                "week_number": week.week_number,
                "label": week.label,
                "kind": week.kind,
                "premier_matchdays": [_serialize_matchday(matchday) for matchday in premier_matchdays],
                "second_matchdays": [_serialize_matchday(matchday) for matchday in second_matchdays],
                "cup_matchdays": [_serialize_matchday(matchday) for matchday in cup_matchdays],
                "playoff_matchdays": [_serialize_matchday(matchday) for matchday in playoff_matchdays],
            }
        )
        state["current_week"] = week.week_number
        if week.week_number == WINTER_SETTLEMENT_WEEK:
            _update_settlement_cache(state, build_snapshot_from_state(state), "winter")
        elif week.week_number == FINAL_SETTLEMENT_WEEK:
            _update_settlement_cache(state, build_snapshot_from_state(state), "final")

        if week.week_number == min(SUMMER_BREAK_WEEKS) - 1:
            config = load_save_config(save_name, rng=rng)
            _prepare_offseason_ability_review(state, config, rng)
            _prepare_pending_draft(state, rng)
        elif week.week_number in WINTER_BREAK_WEEKS or week.week_number in SUMMER_BREAK_WEEKS:
            transfer_snapshot = build_snapshot_from_state(state)
            _prepare_transfer_review(state, transfer_snapshot, week, rng)

        season_completed_now = False
        if week.week_number >= TOTAL_WEEKS:
            season_completed_now = True
            _finalize_season(state)

        _write_state_json(save_name, state)
        return WeekSimulationResult(
            snapshot=build_snapshot_from_state(state),
            week=week,
            premier_matchdays=premier_matchdays,
            second_matchdays=second_matchdays,
            cup_matchdays=cup_matchdays,
            playoff_matchdays=playoff_matchdays,
            season_completed_now=season_completed_now,
        )


def load_save_snapshot(save_name: str) -> SaveSnapshot:
    return build_snapshot_from_state(_load_state_json(save_name))


def load_last_draft_log(save_name: str) -> dict:
    return dict(_load_state_json(save_name).get("last_draft", {}))


def apply_ability_review_decisions(save_name: str, decisions: Dict[str, bool]) -> SaveSnapshot:
    with _state_transaction(save_name):
        state = _load_state_json(save_name)
        pending_review = list(state.get("pending_ability_review", []))
        if not pending_review:
            raise ValueError("当前没有待审核的真实球员能力变动。")

        player_pool = {
            profile.name: profile
            for profile in _deserialize_real_player_pool(state.get("real_player_pool", []))
        }
        processed_review = []
        for item in pending_review:
            approved = bool(decisions.get(item["name"], False))
            if approved and item["name"] in player_pool:
                profile = player_pool[item["name"]]
                player_pool[item["name"]] = RealPlayerProfile(
                    name=profile.name,
                    position=profile.position,
                    ability=int(item["new_ability"]),
                )
            item["approved"] = approved
            processed_review.append(item)

        state["real_player_pool"] = _serialize_real_player_pool(list(player_pool.values()))
        state["last_ability_review"] = processed_review
        state["pending_ability_review"] = []
        _write_state_json(save_name, state)
        return build_snapshot_from_state(state)


def apply_transfer_review_decisions(save_name: str, decisions: Dict[str, bool]) -> SaveSnapshot:
    with _state_transaction(save_name):
        state = _load_state_json(save_name)
        pending_review = list(state.get("pending_transfer_review", []))
        if not pending_review:
            raise ValueError("当前没有待审核的转会。")

        premier_teams = [_deserialize_team(data) for data in state.get("premier_teams", [])]
        second_teams = [_deserialize_team(data) for data in state.get("second_teams", [])]
        teams_by_name = {team.name: team for team in [*premier_teams, *second_teams]}
        _validate_roster_integrity(list(teams_by_name.values()))
        rng = _rng()

        for item in pending_review:
            item["approved"] = False
            item["status"] = "玩家拒绝"
            item["recalculated"] = False
            item["reason"] = ""
            if not bool(decisions.get(item["trade_id"], False)):
                continue

            apply_item = item
            if not _trade_can_apply(teams_by_name, apply_item):
                replacement = _regenerate_trade_for_current_teams(teams_by_name, item, pending_review, rng)
                if replacement is None:
                    item["status"] = "系统拒绝"
                    item["reason"] = "原交易已不适用于当前阵容，且同两队之间无法重算出合法替代交易。"
                    continue
                apply_item = replacement
                item.update(replacement)
                item["recalculated"] = True
                item["reason"] = "原交易已不适用于当前阵容，系统已基于当前阵容重算。"

            try:
                candidate_teams_by_name = _apply_trade_to_team_map(teams_by_name, apply_item)
                _validate_roster_integrity(list(candidate_teams_by_name.values()))
            except ValueError as exc:
                replacement = _regenerate_trade_for_current_teams(teams_by_name, item, pending_review, rng)
                if replacement is None:
                    item["status"] = "系统拒绝"
                    item["reason"] = f"交易校验失败，且无法重算：{exc}"
                    continue
                candidate_teams_by_name = _apply_trade_to_team_map(teams_by_name, replacement)
                _validate_roster_integrity(list(candidate_teams_by_name.values()))
                item.update(replacement)
                item["recalculated"] = True
                item["reason"] = f"原交易校验失败，系统已重算：{exc}"

            teams_by_name = candidate_teams_by_name
            item["approved"] = True
            item["status"] = "系统重算通过" if item.get("recalculated") else "玩家通过"

        updated_teams = list(teams_by_name.values())
        _validate_roster_integrity(updated_teams)
        state["premier_teams"] = [_serialize_team(team) for team in updated_teams if team.division == PREMIER_DIVISION]
        state["second_teams"] = [_serialize_team(team) for team in updated_teams if team.division == SECOND_DIVISION]
        state["player_registry"] = _serialize_player_registry(updated_teams)
        state.setdefault("transfer_history", []).extend(
            _build_transfer_history_rows(state, pending_review)
        )
        state["last_transfer_review"] = pending_review
        state["pending_transfer_review"] = []
        _write_state_json(save_name, state)
        return build_snapshot_from_state(state)


def apply_draft_prospects(save_name: str, prospects: List[dict]) -> SaveSnapshot:
    with _state_transaction(save_name):
        state = _load_state_json(save_name)
        pending_draft = dict(state.get("pending_draft", {}))
        if pending_draft.get("status") != "awaiting_input":
            raise ValueError("当前没有待处理的选秀。")
        if state.get("pending_ability_review") or state.get("pending_transfer_review"):
            raise ValueError("请先完成其他待审核事项，再进行选秀。")

        rng = _rng()
        config = load_save_config(save_name, rng=rng)
        existing_profiles = _deserialize_real_player_pool(state.get("real_player_pool", []))
        existing_names = {profile.name for profile in existing_profiles}

        new_profiles: List[RealPlayerProfile] = []
        target_count = int(pending_draft.get("candidate_count", 0)) or _rng().randint(6, 10)
        config_candidates, next_draft_pool_index = _config_draft_candidates(
            config,
            existing_names,
            int(state.get("draft_pool_index", 0)),
            target_count,
        )
        shortage = max(0, target_count - len(config_candidates))
        manual_items = list(prospects[:shortage]) if shortage else []
        prospect_items = [*config_candidates, *manual_items]
        for item in prospect_items:
            name = str(item["name"]).strip()
            position = str(item["position"]).strip().upper()
            if not name:
                continue
            if name in existing_names or any(profile.name == name for profile in new_profiles):
                raise ValueError(f"新秀姓名重复：{name}")
            if position not in FORMATION_RULES:
                raise ValueError(f"不支持的位置：{position}")
            new_profiles.append(
                RealPlayerProfile(
                    name=name,
                    position=position,
                    ability=rng.randint(
                        max(config.default_player_ability + 1, config.real_player_ability_min),
                        config.real_player_ability_max,
                    ),
                    initial_market_value=30.0,
                )
            )

        premier_teams = [_deserialize_team(data) for data in state.get("premier_teams", [])]
        second_teams = [_deserialize_team(data) for data in state.get("second_teams", [])]
        teams_by_name = {team.name: team for team in [*premier_teams, *second_teams]}
        snapshot = build_snapshot_from_state(state)
        # 用户确认 #6：两联赛统一倒序，全体 40 队参与——
        # 次级倒数第1 → … → 次级第1 → 一级倒数第1 → … → 一级第1。
        draft_order = (
            [row.team.name for row in reversed(snapshot.second_table)]
            + [row.team.name for row in reversed(snapshot.premier_table)]
        )

        prospects_remaining = new_profiles[:]
        rng.shuffle(prospects_remaining)
        prospects_remaining.sort(key=lambda profile: profile.ability, reverse=True)
        drafted_results: List[dict] = []

        while prospects_remaining:
            picked_in_round = False
            for team_name in draft_order:
                team = teams_by_name[team_name]
                counts = _real_position_counts(team)
                eligible = [
                    profile
                    for profile in prospects_remaining
                    if counts[profile.position] < FORMATION_RULES[profile.position]
                ]
                if not eligible:
                    continue
                best_profile = eligible[0]
                teams_by_name[team_name] = _draft_profile_to_team(team, best_profile)
                drafted_results.append(
                    {
                        "team_name": team_name,
                        "name": best_profile.name,
                        "position": best_profile.position,
                        "ability": best_profile.ability,
                        "market_value": best_profile.initial_market_value,
                    }
                )
                prospects_remaining.remove(best_profile)
                picked_in_round = True
                if not prospects_remaining:
                    break
            if not picked_in_round:
                break

        updated_teams = list(teams_by_name.values())
        _validate_roster_integrity(updated_teams)
        state["premier_teams"] = [_serialize_team(team) for team in updated_teams if team.division == PREMIER_DIVISION]
        state["second_teams"] = [_serialize_team(team) for team in updated_teams if team.division == SECOND_DIVISION]
        state["player_registry"] = _serialize_player_registry(updated_teams)
        state["real_player_pool"] = _serialize_real_player_pool([*existing_profiles, *new_profiles])
        state["draft_pool_index"] = next_draft_pool_index
        state["last_draft"] = {
            "season_number": int(state.get("season_number", 0)),
            "stage": "夏窗前选秀",
            "target_count": target_count,
            "config_candidates_used": len(config_candidates),
            "manual_candidates_used": len(manual_items),
            "prospects": [
                {
                    "name": profile.name,
                    "position": profile.position,
                    "ability": profile.ability,
                    "market_value": profile.initial_market_value,
                }
                for profile in new_profiles
            ],
            "results": drafted_results,
            "undrafted": [
                {
                    "name": profile.name,
                    "position": profile.position,
                    "ability": profile.ability,
                    "market_value": profile.initial_market_value,
                }
                for profile in prospects_remaining
            ],
        }
        state["pending_draft"] = {}
        _write_state_json(save_name, state)
        return build_snapshot_from_state(state)


def _config_draft_candidates(
    config,
    existing_names: set[str],
    start_index: int,
    count: int,
) -> tuple[List[dict], int]:
    return _config_draft_candidates_from_index(config, existing_names, start_index, count)


def _config_draft_candidates_from_index(
    config,
    existing_names: set[str],
    start_index: int,
    count: int,
) -> tuple[List[dict], int]:
    candidates: List[dict] = []
    index = max(0, start_index)
    while index < len(config.draft_players) and len(candidates) < count:
        template = config.draft_players[index]
        index += 1
        if template.name in existing_names:
            continue
        candidates.append({"name": template.name, "position": template.position})
    return candidates, index


def build_snapshot_from_state(state: dict) -> SaveSnapshot:
    _normalize_rosters_and_registry(state)
    premier_teams = [_deserialize_team(data) for data in state.get("premier_teams", [])]
    second_teams = [_deserialize_team(data) for data in state.get("second_teams", [])]
    second_team_names = {team.name for team in second_teams}
    weeks = [_deserialize_week(data) for data in state.get("weeks", [])]
    simulated_weeks = state.get("simulated_weeks", [])
    team_lookup = {team.name: team for team in [*premier_teams, *second_teams]}
    settlement_cache = state.get("settlement_cache", {})
    player_registry_data = _serialize_player_registry([*premier_teams, *second_teams])

    premier_table_map = {team.name: TableRow(team=team) for team in premier_teams}
    second_table_map = {team.name: TableRow(team=team) for team in second_teams}
    player_stats_map = {
        item["player_id"]: PlayerSeasonStats(
            player=_deserialize_player(item),
            team_name=item["team_name"],
            season_number=int(state.get("season_number", 1)),
        )
        for item in player_registry_data
    }

    premier_results: List[MatchResult] = []
    second_results: List[MatchResult] = []

    for simulated_week in simulated_weeks:
        for matchday_data in simulated_week.get("premier_matchdays", []):
            matchday = _deserialize_matchday(matchday_data, team_lookup)
            for result in matchday.results:
                premier_results.append(result)
                _apply_table_result(premier_table_map, result)
                for player_id, delta in result.player_stats.items():
                    if player_id in player_stats_map:
                        player_stats_map[player_id].apply_delta(delta)

        for matchday_data in simulated_week.get("second_matchdays", []):
            matchday = _deserialize_matchday(matchday_data, team_lookup)
            for result in matchday.results:
                second_results.append(result)
                _apply_table_result(second_table_map, result)
                for player_id, delta in result.player_stats.items():
                    if player_id in player_stats_map:
                        player_stats_map[player_id].apply_delta(delta)

        for matchday_data in simulated_week.get("cup_matchdays", []):
            matchday = _deserialize_matchday(matchday_data, team_lookup)
            for result in matchday.results:
                for player_id, delta in result.player_stats.items():
                    if player_id in player_stats_map:
                        player_stats_map[player_id].apply_delta(delta)

    team_match_counts = _build_team_match_counts(simulated_weeks)
    settlement_cutoff_week = _latest_settlement_week(int(state.get("current_week", 0)))
    settlement_player_stats, settlement_team_matches = _build_settlement_period_stats(
        simulated_weeks,
        team_lookup,
        player_stats_map,
        settlement_cutoff_week,
    )
    winter_settlement_player_stats, winter_settlement_team_matches = _build_settlement_period_stats(
        simulated_weeks,
        team_lookup,
        player_stats_map,
        WINTER_SETTLEMENT_WEEK if settlement_cutoff_week is not None and settlement_cutoff_week >= WINTER_SETTLEMENT_WEEK else None,
    )

    ranking_playoffs = state.get("ranking_playoffs", {})
    premier_table = _rank_table_rows(
        list(premier_table_map.values()),
        premier_results,
        ranking_playoffs.get(PREMIER_DIVISION, []),
    )
    second_table = _rank_table_rows(
        list(second_table_map.values()),
        second_results,
        ranking_playoffs.get(SECOND_DIVISION, []),
    )

    team_stats_map = {
        team.name: TeamSeasonStats(
            team_name=team.name,
            division=team.division,
            season_number=int(state.get("season_number", 1)),
        )
        for team in [*premier_teams, *second_teams]
    }
    for row in [*premier_table_map.values(), *second_table_map.values()]:
        team_stats = team_stats_map[row.team.name]
        team_stats.played = row.played
        team_stats.wins = row.wins
        team_stats.draws = row.draws
        team_stats.losses = row.losses
        team_stats.goals_for = row.goals_for
        team_stats.goals_against = row.goals_against
        team_stats.points = row.points

    for player_stats in player_stats_map.values():
        player_stats.appearances = team_match_counts.get(player_stats.team_name, 0)
        team_stats = team_stats_map[player_stats.team_name]
        team_stats.goals += player_stats.goals
        team_stats.assists += player_stats.assists
        team_stats.chances_created += player_stats.chances_created
        team_stats.successful_defenses += player_stats.successful_defenses
        team_stats.successful_saves += player_stats.successful_saves
        team_stats.clean_sheets += player_stats.clean_sheets

        settled_row = settlement_player_stats.get(player_stats.player.player_id)
        settled_matches = settlement_team_matches.get(player_stats.team_name, 0)
        if settled_row is not None and settled_matches > 0:
            if player_stats.player.is_real:
                player_stats.season_rating = _calculate_player_rating(settled_row, settled_matches)
                league_weight = _league_weight_for_team(player_stats.team_name, second_team_names)
                player_stats.market_value = round(
                    _calculate_market_value(player_stats.player, player_stats.season_rating) * league_weight,
                    2,
                )
            else:
                # 默认球员只统计六项数据：评分与身价仍仅真实球员。
                player_stats.season_rating = None
                player_stats.market_value = None
        elif player_stats.player.is_real:
            cached_rating, cached_value = _cached_settlement_values(
                settlement_cache,
                player_stats,
                settlement_cutoff_week,
            )
            player_stats.season_rating = cached_rating
            player_stats.market_value = (
                cached_value
                if cached_value is not None
                else player_stats.player.initial_market_value
            )

    for team_name, team_stats in team_stats_map.items():
        team_stats.total_market_value = round(
            sum(
                player_stats.market_value or 0.0
                for player_stats in player_stats_map.values()
                if player_stats.team_name == team_name and player_stats.player.is_real
            ),
            2,
        )
        if settlement_cutoff_week is None:
            team_stats.total_market_value = None

    player_stats = sorted(
        player_stats_map.values(),
        key=lambda row: (
            row.goals,
            row.assists,
            row.chances_created,
            row.successful_defenses,
            row.successful_saves,
            row.clean_sheets,
            row.player.ability,
        ),
        reverse=True,
    )
    team_stats = sorted(
        team_stats_map.values(),
        key=lambda row: (
            1 if row.division == PREMIER_DIVISION else 0,
            row.points,
            row.goal_diff,
            row.goals_for,
        ),
        reverse=True,
    )

    return SaveSnapshot(
        save_name=state["save_name"],
        season_number=int(state["season_number"]),
        current_week=int(state["current_week"]),
        season_complete=bool(state.get("season_complete")),
        premier_teams=premier_teams,
        second_teams=second_teams,
        weeks=weeks,
        simulated_weeks=simulated_weeks,
        premier_table=premier_table,
        second_table=second_table,
        player_stats=player_stats,
        team_stats=team_stats,
        history=state.get("history", []),
        ranking_playoffs=ranking_playoffs,
        cup_state=state.get("cup_state", {}),
        cup_champions=_extract_cup_champions(state.get("cup_state", {})),
        next_premier_team_names=list(state.get("next_premier_team_names", [team.name for team in premier_teams])),
        next_second_team_names=list(state.get("next_second_team_names", [team.name for team in second_teams])),
        real_player_pool=_deserialize_real_player_pool(state.get("real_player_pool", [])),
        pending_ability_review=list(state.get("pending_ability_review", [])),
        pending_transfer_review=list(state.get("pending_transfer_review", [])),
        pending_draft=dict(state.get("pending_draft", {})),
        settlement_cache=dict(state.get("settlement_cache", {})),
        transfer_history=list(state.get("transfer_history", [])),
    )


def get_player_history_totals(snapshot: SaveSnapshot) -> List[dict]:
    totals: Dict[str, dict] = {}
    for season in _season_archives(snapshot):
        for row in season["player_stats"]:
            history_key = _player_history_key_from_row(row)
            entry = totals.setdefault(
                history_key,
                {
                    "player_id": history_key,
                    "history_key": history_key,
                    "label": _normalize_player_label(row["label"]),
                    "position": row["position"],
                    "team_name": row["team_name"],
                    "division": row.get("division", PREMIER_DIVISION),
                    "latest_season": 0,
                    "seasons": 0,
                    "goals": 0,
                    "assists": 0,
                    "chances_created": 0,
                    "successful_defenses": 0,
                    "successful_saves": 0,
                    "clean_sheets": 0,
                    "honor_points": 0,
                    "premier_titles": 0,
                    "second_titles": 0,
                    "winners_cup_titles": 0,
                    "challenge_cup_titles": 0,
                    "super_cup_titles": 0,
                    "total_titles": 0,
                    "top20_finishes": 0,
                    "top20_best_rank": None,
                    "top_scorer_awards": 0,
                    "assist_leader_awards": 0,
                    "mvp_awards": 0,
                    "award_labels": [],
                },
            )
            if row.get("season_number", 0) >= entry["latest_season"]:
                entry["latest_season"] = row.get("season_number", 0)
                entry["team_name"] = row["team_name"]
                entry["division"] = row.get("division", PREMIER_DIVISION)
            entry["seasons"] += 1
            for field_name in (
                "goals",
                "assists",
                "chances_created",
                "successful_defenses",
                "successful_saves",
                "clean_sheets",
                "honor_points",
                "premier_titles",
                "second_titles",
                "winners_cup_titles",
                "challenge_cup_titles",
                "super_cup_titles",
                "total_titles",
                "top20_finishes",
                "top_scorer_awards",
                "assist_leader_awards",
                "mvp_awards",
            ):
                entry[field_name] += row.get(field_name, 0)
            if row.get("top20_rank") is not None:
                current_best = entry.get("top20_best_rank")
                entry["top20_best_rank"] = (
                    row["top20_rank"]
                    if current_best is None
                    else min(current_best, row["top20_rank"])
                )
            entry["award_labels"].extend(row.get("award_labels", []))
    for row in totals.values():
        row.pop("latest_season", None)
    return list(totals.values())


def get_player_single_season_records(snapshot: SaveSnapshot) -> List[dict]:
    records: List[dict] = []
    for season in _season_archives(snapshot):
        for row in season["player_stats"]:
            normalized_row = dict(row)
            normalized_row["label"] = _normalize_player_label(row["label"])
            normalized_row.setdefault("season_team_names", [normalized_row.get("team_name", "-")])
            normalized_row.setdefault("team_display_name", normalized_row.get("team_name", "-"))
            records.append(normalized_row)
    return records


def get_team_history_totals(snapshot: SaveSnapshot) -> List[dict]:
    totals: Dict[str, dict] = {}
    for season in _season_archives(snapshot):
        for row in season["team_stats"]:
            entry = totals.setdefault(
                row["team_name"],
                {
                    "team_name": row["team_name"],
                    "division": row.get("division", PREMIER_DIVISION),
                    "seasons": 0,
                    "played": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "goals_for": 0,
                    "goals_against": 0,
                    "points": 0,
                    "goals": 0,
                    "assists": 0,
                    "chances_created": 0,
                    "successful_defenses": 0,
                    "successful_saves": 0,
                    "clean_sheets": 0,
                    "honor_points": 0,
                    "premier_titles": 0,
                    "second_titles": 0,
                    "winners_cup_titles": 0,
                    "challenge_cup_titles": 0,
                    "super_cup_titles": 0,
                    "total_titles": 0,
                },
            )
            entry["seasons"] += 1
            for field_name in (
                "played",
                "wins",
                "draws",
                "losses",
                "goals_for",
                "goals_against",
                "points",
                "goals",
                "assists",
                "chances_created",
                "successful_defenses",
                "successful_saves",
                "clean_sheets",
                "honor_points",
                "premier_titles",
                "second_titles",
                "winners_cup_titles",
                "challenge_cup_titles",
                "super_cup_titles",
                "total_titles",
            ):
                entry[field_name] += row.get(field_name, 0)
    for row in totals.values():
        row["goal_diff"] = row["goals_for"] - row["goals_against"]
    return list(totals.values())


def get_team_single_season_records(snapshot: SaveSnapshot) -> List[dict]:
    records: List[dict] = []
    for season in _season_archives(snapshot):
        for row in season["team_stats"]:
            record = dict(row)
            record["goal_diff"] = row["goals_for"] - row["goals_against"]
            records.append(record)
    return records


def find_player_rows(snapshot: SaveSnapshot, query: str) -> List[dict]:
    lowered_query = query.lower()
    return [row for row in _snapshot_to_archive(snapshot)["player_stats"] if lowered_query in row["label"].lower()]


def find_team_rows(snapshot: SaveSnapshot, query: str) -> List[dict]:
    lowered_query = query.lower()
    return [row for row in _snapshot_to_archive(snapshot)["team_stats"] if lowered_query in row["team_name"].lower()]


def get_team_honor_leaders(snapshot: SaveSnapshot) -> List[dict]:
    return sorted(
        get_team_history_totals(snapshot),
        key=lambda row: (
            row.get("honor_points", 0),
            row.get("total_titles", 0),
            row.get("premier_titles", 0),
            row.get("winners_cup_titles", 0),
            row.get("challenge_cup_titles", 0),
            row.get("super_cup_titles", 0),
            row["team_name"],
        ),
        reverse=True,
    )


def get_player_honor_leaders(snapshot: SaveSnapshot) -> List[dict]:
    return sorted(
        get_player_history_totals(snapshot),
        key=lambda row: (
            row.get("honor_points", 0),
            row.get("total_titles", 0),
            row.get("premier_titles", 0),
            row.get("winners_cup_titles", 0),
            row.get("challenge_cup_titles", 0),
            row.get("super_cup_titles", 0),
            row["label"],
        ),
        reverse=True,
    )


def _latest_settlement_week(current_week: int) -> Optional[int]:
    if current_week >= FINAL_SETTLEMENT_WEEK:
        return FINAL_SETTLEMENT_WEEK
    if current_week >= WINTER_SETTLEMENT_WEEK:
        return WINTER_SETTLEMENT_WEEK
    return None


def _build_team_match_counts(simulated_weeks: List[dict]) -> Dict[str, int]:
    team_match_counts: Dict[str, int] = {}
    for simulated_week in simulated_weeks:
        for matchday_key in ("premier_matchdays", "second_matchdays", "cup_matchdays"):
            for matchday_data in simulated_week.get(matchday_key, []):
                for result_data in matchday_data.get("results", []):
                    home_name = result_data["home_team"]
                    away_name = result_data["away_team"]
                    team_match_counts[home_name] = team_match_counts.get(home_name, 0) + 1
                    team_match_counts[away_name] = team_match_counts.get(away_name, 0) + 1
    return team_match_counts


def _build_settlement_period_stats(
    simulated_weeks: List[dict],
    team_lookup: Dict[str, Team],
    player_stats_map: Dict[str, PlayerSeasonStats],
    cutoff_week: Optional[int],
) -> tuple[Dict[str, PlayerSeasonStats], Dict[str, int]]:
    if cutoff_week is None:
        return {}, {}

    period_player_stats = {
        player_id: PlayerSeasonStats(
            player=row.player,
            team_name=row.team_name,
            season_number=row.season_number,
        )
        for player_id, row in player_stats_map.items()
    }
    period_team_matches: Dict[str, int] = {}

    for simulated_week in simulated_weeks:
        if int(simulated_week.get("week_number", 0)) > cutoff_week:
            continue
        for matchday_key in ("premier_matchdays", "second_matchdays", "cup_matchdays"):
            for matchday_data in simulated_week.get(matchday_key, []):
                competition = matchday_data.get("competition", PREMIER_DIVISION)
                round_number = int(matchday_data["round_number"])
                for result_data in matchday_data.get("results", []):
                    result = _deserialize_match_result(result_data, team_lookup, round_number, competition)
                    period_team_matches[result.home_team.name] = period_team_matches.get(result.home_team.name, 0) + 1
                    period_team_matches[result.away_team.name] = period_team_matches.get(result.away_team.name, 0) + 1
                    for player_id, delta in result.player_stats.items():
                        if player_id in period_player_stats:
                            period_player_stats[player_id].apply_delta(delta)

    return period_player_stats, period_team_matches


def _update_settlement_cache(state: dict, snapshot: SaveSnapshot, cache_name: str) -> None:
    cache = state.setdefault("settlement_cache", {})
    cache[cache_name] = {
        _settlement_cache_key(row): {
            "season_rating": row.season_rating,
            "market_value": row.market_value,
        }
        for row in snapshot.player_stats
        if row.player.is_real and row.season_rating is not None and row.market_value is not None
    }


def _cached_settlement_values(
    settlement_cache: dict,
    player_stats: PlayerSeasonStats,
    settlement_cutoff_week: Optional[int],
) -> tuple[Optional[float], Optional[float]]:
    if settlement_cutoff_week is None:
        return None, None

    cache_names: List[str] = []
    if settlement_cutoff_week >= FINAL_SETTLEMENT_WEEK:
        cache_names.append("final")
    if settlement_cutoff_week >= WINTER_SETTLEMENT_WEEK:
        cache_names.append("winter")

    player_key = _settlement_cache_key(player_stats)
    for cache_name in cache_names:
        item = settlement_cache.get(cache_name, {}).get(player_key)
        if item is not None:
            return item.get("season_rating"), item.get("market_value")
    return None, None


def _settlement_cache_key(player_stats: PlayerSeasonStats) -> str:
    return player_stats.player.name or player_stats.player.player_id


def _prepare_transfer_review(
    state: dict,
    snapshot: SaveSnapshot,
    week: WeekScheduleEntry,
    rng: random.Random,
) -> None:
    if state.get("pending_transfer_review"):
        return

    min_trades = 1 if week.week_number in WINTER_BREAK_WEEKS else 3
    max_trades = 3 if week.week_number in WINTER_BREAK_WEEKS else 5
    target_trades = rng.randint(min_trades, max_trades)
    proposals = _generate_trade_proposals(snapshot, target_trades, rng)
    state["pending_transfer_review"] = proposals


def _prepare_pending_draft(state: dict, rng: random.Random) -> None:
    pending = state.get("pending_draft", {})
    if pending.get("status") == "awaiting_input":
        return
    season_number = int(state.get("season_number", 0))
    last_draft = state.get("last_draft", {})
    if int(last_draft.get("season_number", -1)) == season_number:
        return
    state["pending_draft"] = {
        "status": "awaiting_input",
        "season_number": season_number,
        "candidate_count": rng.randint(6, 10),
        "stage": "夏窗前选秀",
        "results": [],
        "undrafted": [],
    }


def _generate_trade_proposals(
    snapshot: SaveSnapshot,
    target_trades: int,
    rng: random.Random,
) -> List[dict]:
    market_values = {
        row.player.player_id: _effective_market_value(row)
        for row in snapshot.player_stats
        if row.player.is_real
    }
    current_teams = {
        team.name: team
        for team in snapshot.premier_teams
    }
    used_player_ids = set()
    proposals: List[dict] = []
    for trade_number in range(1, target_trades + 1):
        team_list = list(current_teams.values())
        all_pairs = [(first.name, second.name) for index, first in enumerate(team_list) for second in team_list[index + 1 :]]
        rng.shuffle(all_pairs)
        proposal = None
        for team_a_name, team_b_name in all_pairs:
            proposal = _generate_trade_for_pair(
                current_teams[team_a_name],
                current_teams[team_b_name],
                market_values,
                used_player_ids,
                rng,
                trade_number,
            )
            if proposal is not None:
                break
        if proposal is None:
            break
        proposals.append(proposal)
        used_player_ids.update(item["player_id"] for item in proposal["team_a_players"])
        used_player_ids.update(item["player_id"] for item in proposal["team_b_players"])
        current_teams = _apply_trade_to_team_map(current_teams, proposal)

    return proposals


def _generate_trade_for_pair(
    team_a: Team,
    team_b: Team,
    market_values: Dict[str, float],
    used_player_ids: set,
    rng: random.Random,
    trade_number: int,
) -> Optional[dict]:
    team_a_players = [(player, market_values.get(player.player_id, 0.0)) for player in team_a.roster if player.is_real and player.player_id not in used_player_ids]
    team_b_players = [(player, market_values.get(player.player_id, 0.0)) for player in team_b.roster if player.is_real and player.player_id not in used_player_ids]
    if not team_a_players or not team_b_players:
        return None

    team_a_packages = _candidate_trade_packages(team_a_players)
    team_b_packages = _candidate_trade_packages(team_b_players)
    rng.shuffle(team_a_packages)
    rng.shuffle(team_b_packages)

    for outgoing_a in team_a_packages:
        for outgoing_b in team_b_packages:
            if _trade_value_gap(outgoing_a, outgoing_b) > 10.0:
                continue
            if not _trade_respects_position_limits(team_a, team_b, outgoing_a, outgoing_b):
                continue
            return _build_trade_review_item(team_a, team_b, outgoing_a, outgoing_b, trade_number)
    return None


def _trade_can_apply(teams_by_name: Dict[str, Team], trade_item: dict) -> bool:
    team_a = teams_by_name.get(trade_item.get("team_a"))
    team_b = teams_by_name.get(trade_item.get("team_b"))
    if team_a is None or team_b is None:
        return False
    team_a_ids = {player.player_id for player in team_a.roster}
    team_b_ids = {player.player_id for player in team_b.roster}
    outgoing_a_ids = {item["player_id"] for item in trade_item.get("team_a_players", [])}
    outgoing_b_ids = {item["player_id"] for item in trade_item.get("team_b_players", [])}
    return outgoing_a_ids <= team_a_ids and outgoing_b_ids <= team_b_ids


def _regenerate_trade_for_current_teams(
    teams_by_name: Dict[str, Team],
    original_item: dict,
    review_items: List[dict],
    rng: random.Random,
) -> Optional[dict]:
    team_a = teams_by_name.get(original_item.get("team_a"))
    team_b = teams_by_name.get(original_item.get("team_b"))
    if team_a is None or team_b is None:
        return None
    market_values = _market_values_from_trade_context(teams_by_name, review_items)
    replacement = _generate_trade_for_pair(
        team_a,
        team_b,
        market_values,
        set(),
        rng,
        _trade_number_from_id(str(original_item.get("trade_id", "trade_0"))),
    )
    if replacement is None:
        return None
    replacement["trade_id"] = original_item.get("trade_id", replacement["trade_id"])
    return replacement


def _market_values_from_trade_context(teams_by_name: Dict[str, Team], review_items: List[dict]) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for item in review_items:
        for player in [*item.get("team_a_players", []), *item.get("team_b_players", [])]:
            player_id = player.get("player_id")
            if player_id:
                values[player_id] = round(float(player.get("market_value", 0.0)), 2)
    for team in teams_by_name.values():
        for player in team.roster:
            if not player.is_real or player.player_id in values:
                continue
            if player.initial_market_value is not None:
                values[player.player_id] = round(player.initial_market_value, 2)
            else:
                fallback_rating = max(6.0, min(8.5, player.ability / 10))
                values[player.player_id] = _calculate_market_value(player, fallback_rating)
    return values


def _trade_number_from_id(trade_id: str) -> int:
    try:
        return int(trade_id.rsplit("_", 1)[-1])
    except ValueError:
        return 0


def _candidate_trade_packages(player_value_pairs: List[tuple[Player, float]]) -> List[List[tuple[Player, float]]]:
    packages: List[List[tuple[Player, float]]] = []
    for size in (1, 2):
        packages.extend([list(combo) for combo in combinations(player_value_pairs, size) if combo])
    return packages


def _trade_value_gap(
    outgoing_a: List[tuple[Player, float]],
    outgoing_b: List[tuple[Player, float]],
) -> float:
    return abs(
        sum(value for _, value in outgoing_a)
        - sum(value for _, value in outgoing_b)
    )


def _trade_respects_position_limits(
    team_a: Team,
    team_b: Team,
    outgoing_a: List[tuple[Player, float]],
    outgoing_b: List[tuple[Player, float]],
) -> bool:
    counts_a = _real_position_counts(team_a)
    counts_b = _real_position_counts(team_b)

    for player, _ in outgoing_a:
        counts_a[player.position] -= 1
        counts_b[player.position] += 1
    for player, _ in outgoing_b:
        counts_b[player.position] -= 1
        counts_a[player.position] += 1

    for position, limit in {
        POSITION_GOALKEEPER: 1,
        POSITION_DEFENDER: 4,
        POSITION_MIDFIELDER: 3,
        POSITION_FORWARD: 3,
    }.items():
        if counts_a[position] < 0 or counts_b[position] < 0:
            return False
        if counts_a[position] > limit or counts_b[position] > limit:
            return False
    return True


def _real_position_counts(team: Team) -> Dict[str, int]:
    counts = {
        POSITION_GOALKEEPER: 0,
        POSITION_DEFENDER: 0,
        POSITION_MIDFIELDER: 0,
        POSITION_FORWARD: 0,
    }
    for player in team.roster:
        if player.is_real:
            counts[player.position] += 1
    return counts


def _build_trade_review_item(
    team_a: Team,
    team_b: Team,
    outgoing_a: List[tuple[Player, float]],
    outgoing_b: List[tuple[Player, float]],
    trade_number: int,
) -> dict:
    value_a = round(sum(value for _, value in outgoing_a), 2)
    value_b = round(sum(value for _, value in outgoing_b), 2)
    return {
        "trade_id": f"trade_{trade_number}",
        "team_a": team_a.name,
        "team_b": team_b.name,
        "team_a_players": [
            {
                "player_id": player.player_id,
                "name": player.label,
                "position": player.position,
                "ability": player.ability,
                "market_value": round(value, 2),
            }
            for player, value in outgoing_a
        ],
        "team_b_players": [
            {
                "player_id": player.player_id,
                "name": player.label,
                "position": player.position,
                "ability": player.ability,
                "market_value": round(value, 2),
            }
            for player, value in outgoing_b
        ],
        "team_a_total_value": value_a,
        "team_b_total_value": value_b,
        "value_gap": round(abs(value_a - value_b), 2),
    }


def _player_market_value(snapshot: SaveSnapshot, player_id: str) -> float:
    player_row = next((row for row in snapshot.player_stats if row.player.player_id == player_id), None)
    return _effective_market_value(player_row) if player_row else 0.0


def _effective_market_value(player_row: Optional[PlayerSeasonStats]) -> float:
    if player_row is None or not player_row.player.is_real:
        return 0.0
    if player_row.market_value is not None:
        return round(player_row.market_value, 2)
    if player_row.player.initial_market_value is not None:
        return round(player_row.player.initial_market_value, 2)
    fallback_rating = max(6.0, min(8.5, player_row.player.ability / 10))
    return _calculate_market_value(player_row.player, fallback_rating)


def _apply_trade_to_team_map(teams_by_name: Dict[str, Team], trade_item: dict) -> Dict[str, Team]:
    team_a = teams_by_name[trade_item["team_a"]]
    team_b = teams_by_name[trade_item["team_b"]]

    outgoing_a_ids = {item["player_id"] for item in trade_item["team_a_players"]}
    outgoing_b_ids = {item["player_id"] for item in trade_item["team_b_players"]}
    incoming_for_a = [player for player in team_b.roster if player.player_id in outgoing_b_ids]
    incoming_for_b = [player for player in team_a.roster if player.player_id in outgoing_a_ids]

    updated_team_a = _rebuild_team_after_trade(team_a, outgoing_a_ids, incoming_for_a)
    updated_team_b = _rebuild_team_after_trade(team_b, outgoing_b_ids, incoming_for_b)

    updated_map = dict(teams_by_name)
    updated_map[updated_team_a.name] = updated_team_a
    updated_map[updated_team_b.name] = updated_team_b
    return updated_map


def _rebuild_team_after_trade(
    team: Team,
    outgoing_player_ids: set,
    incoming_players: List[Player],
) -> Team:
    roster = list(team.roster)
    default_ability = int(team.baseline_ability)
    for index, player in enumerate(roster):
        if player.player_id in outgoing_player_ids:
            roster[index] = _build_default_slot_player(team.name, player.position, player.slot_number, default_ability)

    for incoming_player in incoming_players:
        replacement_index = next(
            (
                index
                for index, player in enumerate(roster)
                if not player.is_real and player.position == incoming_player.position
            ),
            None,
        )
        if replacement_index is None:
            raise ValueError(f"{team.name} 没有可供 {incoming_player.label} 替换的 {incoming_player.position} 默认位置。")
        slot_number = roster[replacement_index].slot_number
        roster[replacement_index] = Player(
            player_id=real_player_id(incoming_player.name or incoming_player.player_id),
            name=incoming_player.name,
            position=incoming_player.position,
            ability=incoming_player.ability,
            is_real=True,
            slot_number=slot_number,
            initial_market_value=incoming_player.initial_market_value,
        )

    return Team(name=team.name, roster=tuple(roster), division=team.division)


def _build_default_slot_player(team_name: str, position: str, slot_number: int, default_ability: int) -> Player:
    team_slug = _slugify_team_name(team_name)
    return Player(
        player_id=f"{team_slug}-{position.lower()}-{slot_number}-default",
        name=None,
        position=position,
        ability=default_ability,
        is_real=False,
        slot_number=slot_number,
    )


def _validate_roster_integrity(teams: List[Team]) -> None:
    if len(teams) != 40:
        raise ValueError(f"阵容校验失败：当前共有 {len(teams)} 支球队，应为 40 支。")
    real_player_ids: set[str] = set()
    duplicated_real_ids: set[str] = set()
    for team in teams:
        if len(team.roster) != sum(FORMATION_RULES.values()):
            raise ValueError(f"阵容校验失败：{team.name} 不是 11 人阵容。")
        position_counts = {position: 0 for position in FORMATION_RULES}
        real_counts = {position: 0 for position in FORMATION_RULES}
        for player in team.roster:
            if player.position not in position_counts:
                raise ValueError(f"阵容校验失败：{team.name} 出现未知位置 {player.position}。")
            position_counts[player.position] += 1
            if player.is_real:
                if player.player_id in real_player_ids:
                    duplicated_real_ids.add(player.player_id)
                real_player_ids.add(player.player_id)
                real_counts[player.position] += 1
        if position_counts != FORMATION_RULES:
            raise ValueError(f"阵容校验失败：{team.name} 位置人数不正确。")
        for position, limit in FORMATION_RULES.items():
            if real_counts[position] > limit:
                raise ValueError(f"阵容校验失败：{team.name} 的 {position} 真实球员超过上限。")
    if duplicated_real_ids:
        raise ValueError("阵容校验失败：存在重复真实球员 ID：" + "、".join(sorted(duplicated_real_ids)[:5]))


def _build_transfer_history_rows(state: dict, review_items: List[dict]) -> List[dict]:
    week_number = int(state.get("current_week", 0))
    window = _transfer_window_label(week_number)
    rows: List[dict] = []
    for item in review_items:
        rows.append(
            {
                "season_number": int(state.get("season_number", 0)),
                "week_number": week_number,
                "window": window,
                "trade_id": item.get("trade_id"),
                "team_a": item.get("team_a"),
                "team_b": item.get("team_b"),
                "team_a_players": list(item.get("team_a_players", [])),
                "team_b_players": list(item.get("team_b_players", [])),
                "team_a_total_value": float(item.get("team_a_total_value", 0.0)),
                "team_b_total_value": float(item.get("team_b_total_value", 0.0)),
                "value_gap": float(item.get("value_gap", 0.0)),
                "approved": bool(item.get("approved")),
                "status": item.get("status") or ("玩家通过" if item.get("approved") else "玩家拒绝"),
                "recalculated": bool(item.get("recalculated")),
                "reason": item.get("reason", ""),
            }
        )
    return rows


def _transfer_window_label(week_number: int) -> str:
    if week_number in WINTER_BREAK_WEEKS:
        return "冬窗"
    if week_number in SUMMER_BREAK_WEEKS:
        return "夏窗"
    return "转会窗口"


def _slugify_team_name(team_name: str) -> str:
    return "-".join("".join(character.lower() if character.isalnum() else " " for character in team_name).split())


def _slugify_player_name(player_name: str) -> str:
    return "-".join("".join(character.lower() if character.isalnum() else " " for character in player_name).split())


def normalize_rosters_and_registry(state: dict) -> dict:
    _normalize_rosters_and_registry(state)
    return state


def _normalize_rosters_and_registry(state: dict) -> bool:
    changed = False
    id_remap: Dict[str, str] = {}
    seen_real_ids: set[str] = set()
    real_name_by_old_id: Dict[str, str] = {}

    for item in state.get("player_registry", []):
        name = _clean_player_name(item.get("name"))
        if item.get("is_real") and name:
            real_name_by_old_id.setdefault(str(item.get("player_id", "")), name)

    for league_key in ("premier_teams", "second_teams"):
        for team_data in state.get(league_key, []):
            team_name = str(team_data.get("name", ""))
            default_ability = _default_ability_from_team_data(team_data)
            normalized_roster = []
            for player_data in team_data.get("roster", []):
                original_id = str(player_data.get("player_id", ""))
                name = _clean_player_name(player_data.get("name"))
                position = str(player_data.get("position", "")).upper()
                slot_number = int(player_data.get("slot_number", 1))
                ability = int(player_data.get("ability", default_ability))

                if name:
                    normalized_id = real_player_id(name)
                    if normalized_id in seen_real_ids:
                        normalized_player = _default_player_data(team_name, position, slot_number, default_ability)
                    else:
                        normalized_player = {
                            "player_id": normalized_id,
                            "name": name,
                            "position": position,
                            "ability": ability,
                            "is_real": True,
                            "slot_number": slot_number,
                            "initial_market_value": player_data.get("initial_market_value"),
                        }
                        seen_real_ids.add(normalized_id)
                        if original_id and original_id != normalized_id:
                            id_remap.setdefault(original_id, normalized_id)
                            real_name_by_old_id.setdefault(original_id, name)
                else:
                    normalized_player = _default_player_data(team_name, position, slot_number, default_ability)

                if normalized_player != player_data:
                    changed = True
                normalized_roster.append(normalized_player)
            if normalized_roster != team_data.get("roster", []):
                team_data["roster"] = normalized_roster
                changed = True

    changed = _normalize_match_player_stat_ids(state, id_remap, real_name_by_old_id) or changed
    changed = _normalize_review_player_ids(state) or changed

    teams = [
        _deserialize_team(team_data)
        for team_data in [*state.get("premier_teams", []), *state.get("second_teams", [])]
    ]
    registry = _serialize_player_registry(teams)
    if state.get("player_registry") != registry:
        state["player_registry"] = registry
        changed = True
    state["roster_fix_summary"] = _build_roster_fix_summary(state)
    return changed


def _clean_player_name(value: object) -> Optional[str]:
    if value is None:
        return None
    name = str(value).strip()
    if not name or name.startswith("默认 "):
        return None
    return name


def _default_ability_from_team_data(team_data: dict) -> int:
    abilities = [
        int(player.get("ability", 50))
        for player in team_data.get("roster", [])
        if not player.get("is_real")
    ]
    if abilities:
        return min(abilities)
    all_abilities = [int(player.get("ability", 50)) for player in team_data.get("roster", [])]
    return min(all_abilities) if all_abilities else 50


def _default_player_data(team_name: str, position: str, slot_number: int, ability: int) -> dict:
    team_slug = _slugify_team_name(team_name)
    return {
        "player_id": f"{team_slug}-{position.lower()}-{slot_number}-default",
        "name": None,
        "position": position,
        "ability": ability,
        "is_real": False,
        "slot_number": slot_number,
        "initial_market_value": None,
    }


def _normalize_match_player_stat_ids(
    state: dict,
    id_remap: Dict[str, str],
    real_name_by_old_id: Dict[str, str],
) -> bool:
    changed = False
    for week_data in state.get("simulated_weeks", []):
        for matchday_key in ("premier_matchdays", "second_matchdays", "cup_matchdays", "playoff_matchdays"):
            for matchday in week_data.get(matchday_key, []):
                for result in matchday.get("results", []):
                    stats = result.get("player_stats", {})
                    if not isinstance(stats, dict):
                        continue
                    normalized_stats = {}
                    for player_id, delta in stats.items():
                        target_id = id_remap.get(player_id)
                        if target_id is None and player_id in real_name_by_old_id:
                            target_id = real_player_id(real_name_by_old_id[player_id])
                        if target_id is None and str(player_id).startswith("real::"):
                            target_id = player_id
                        if target_id is None:
                            changed = True
                            continue
                        if target_id in normalized_stats:
                            normalized_stats[target_id] = _merge_stat_delta_dicts(normalized_stats[target_id], delta)
                        else:
                            normalized_stats[target_id] = delta
                        if target_id != player_id:
                            changed = True
                    if normalized_stats != stats:
                        result["player_stats"] = normalized_stats
                        changed = True
    return changed


def _merge_stat_delta_dicts(first: dict, second: dict) -> dict:
    merged = dict(first)
    for field in ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets"):
        merged[field] = int(merged.get(field, 0)) + int(second.get(field, 0))
    return merged


def _normalize_review_player_ids(state: dict) -> bool:
    changed = False
    for review_key in ("pending_transfer_review", "last_transfer_review", "transfer_history"):
        for item in state.get(review_key, []):
            for side_key in ("team_a_players", "team_b_players"):
                for player in item.get(side_key, []):
                    name = _clean_player_name(player.get("name"))
                    if name:
                        normalized_id = real_player_id(name)
                        if player.get("player_id") != normalized_id:
                            player["player_id"] = normalized_id
                            changed = True
    return changed


def _build_roster_fix_summary(state: dict) -> dict:
    real_count = 0
    default_count = 0
    for team_data in [*state.get("premier_teams", []), *state.get("second_teams", [])]:
        for player in team_data.get("roster", []):
            if player.get("is_real"):
                real_count += 1
            else:
                default_count += 1
    return {
        "real_players": real_count,
        "default_players": default_count,
        "registry_players": len(state.get("player_registry", [])),
    }


def _draft_profile_to_team(team: Team, profile: RealPlayerProfile) -> Team:
    roster = list(team.roster)
    replacement_index = next(
        (
            index
            for index, player in enumerate(roster)
            if not player.is_real and player.position == profile.position
        ),
        None,
    )
    if replacement_index is None:
        raise ValueError(f"{team.name} 没有可供新秀替换的 {profile.position} 默认位置。")
    slot_number = roster[replacement_index].slot_number
    roster[replacement_index] = Player(
        player_id=real_player_id(profile.name),
        name=profile.name,
        position=profile.position,
        ability=profile.ability,
        is_real=True,
        slot_number=slot_number,
        initial_market_value=profile.initial_market_value,
    )
    return Team(name=team.name, roster=tuple(roster), division=team.division)


def _finalize_season(state: dict) -> None:
    season_number = int(state["season_number"])
    state["ranking_playoffs"] = _generate_ranking_playoffs(state)
    snapshot = build_snapshot_from_state(state)
    playoff_winner = _determine_promotion_playoff_winner(state, snapshot)
    cup_champions = _extract_cup_champions(state.get("cup_state", {}))

    relegated = [row.team.name for row in snapshot.premier_table[-3:]]
    promoted_direct = [row.team.name for row in snapshot.second_table[:2]]
    promoted = [*promoted_direct, playoff_winner]

    current_premier = [team.name for team in snapshot.premier_teams]
    current_second = [team.name for team in snapshot.second_teams]
    state["next_premier_team_names"] = [team_name for team_name in current_premier if team_name not in relegated] + promoted
    state["next_second_team_names"] = [team_name for team_name in current_second if team_name not in promoted] + relegated
    state["last_transition"] = {
        "season_number": season_number,
        "relegated": relegated,
        "promoted_direct": promoted_direct,
        "playoff_winner": playoff_winner,
        "cup_champions": cup_champions,
    }
    state["season_complete"] = True
    _archive_current_season(state)


def _generate_ranking_playoffs(state: dict) -> Dict[str, list[dict]]:
    premier_teams = [_deserialize_team(data) for data in state.get("premier_teams", [])]
    second_teams = [_deserialize_team(data) for data in state.get("second_teams", [])]
    team_lookup = {team.name: team for team in [*premier_teams, *second_teams]}
    premier_table_map = {team.name: TableRow(team=team) for team in premier_teams}
    second_table_map = {team.name: TableRow(team=team) for team in second_teams}
    premier_results: List[MatchResult] = []
    second_results: List[MatchResult] = []

    for simulated_week in state.get("simulated_weeks", []):
        for matchday_data in simulated_week.get("premier_matchdays", []):
            matchday = _deserialize_matchday(matchday_data, team_lookup)
            for result in matchday.results:
                premier_results.append(result)
                _apply_table_result(premier_table_map, result)
        for matchday_data in simulated_week.get("second_matchdays", []):
            matchday = _deserialize_matchday(matchday_data, team_lookup)
            for result in matchday.results:
                second_results.append(result)
                _apply_table_result(second_table_map, result)

    rng = _rng()
    return {
        PREMIER_DIVISION: _build_ranking_playoff_resolutions(list(premier_table_map.values()), premier_results, rng),
        SECOND_DIVISION: _build_ranking_playoff_resolutions(list(second_table_map.values()), second_results, rng),
    }


def _build_ranking_playoff_resolutions(
    rows: List[TableRow],
    results: List[MatchResult],
    rng: random.Random,
) -> list[dict]:
    resolutions: list[dict] = []
    grouped = _group_by_metric(rows, lambda row: (row.points, row.goals_for - row.goals_against, row.goals_for))
    for group in grouped:
        if len(group) <= 1:
            continue
        head_to_head_groups = _group_by_metric(
            group,
            lambda row: _head_to_head_tuple(row.team.name, group, results),
        )
        for tied_group in head_to_head_groups:
            if len(tied_group) <= 1:
                continue
            names = sorted(row.team.name for row in tied_group)
            order = names[:]
            rng.shuffle(order)
            resolutions.append({"teams": names, "order": order})
    return resolutions


def _initialize_cup_state(
    season_number: int,
    previous_archive: Optional[dict],
    premier_teams: List[Team],
    second_teams: List[Team],
    rng: random.Random,
) -> dict:
    previous_premier_order = _archive_team_order(previous_archive, PREMIER_DIVISION)
    previous_second_order = _archive_team_order(previous_archive, SECOND_DIVISION)
    previous_champions = previous_archive.get("cup_champions", {}) if previous_archive else {}

    winners_cup = {"active": False, "results": {}, "champion": None}
    challenge_cup = {"active": False, "results": {}, "champion": None}
    super_cup = {"active": False, "results": {}, "champion": None}

    if season_number >= 2 and len(previous_premier_order) >= 16:
        groups = _draw_winners_cup_groups(previous_premier_order[:16], rng)
        winners_cup = {
            "active": True,
            "groups": groups,
            "group_fixtures": _build_winners_cup_group_fixtures(groups),
            "knockout_pairs": {},
            "results": {},
            "champion": None,
        }

        challenge_participants = previous_premier_order[:20] + previous_second_order[:12]
        challenge_seeds = {team_name: index + 1 for index, team_name in enumerate(challenge_participants)}
        challenge_cup = {
            "active": True,
            "seeds": challenge_seeds,
            "participants": challenge_participants,
            "pairings": {},
            "results": {},
            "champion": None,
        }

    if season_number >= 3 and len(previous_premier_order) >= 4:
        super_participants = _build_super_cup_participants(previous_premier_order, previous_champions)
        shuffled = super_participants[:]
        rng.shuffle(shuffled)
        super_cup = {
            "active": True,
            "participants": super_participants,
            "semifinals": [(shuffled[0], shuffled[1]), (shuffled[2], shuffled[3])],
            "results": {},
            "champion": None,
        }

    return {
        "winners_cup": winners_cup,
        "challenge_cup": challenge_cup,
        "super_cup": super_cup,
    }


def _archive_team_order(previous_archive: Optional[dict], division: str) -> List[str]:
    if not previous_archive:
        return []
    key = "premier_order" if division == PREMIER_DIVISION else "second_order"
    if previous_archive.get(key):
        return list(previous_archive[key])
    records = [row for row in previous_archive.get("team_stats", []) if row.get("division", PREMIER_DIVISION) == division]
    records.sort(key=lambda row: (row.get("points", 0), row.get("goals_for", 0) - row.get("goals_against", 0), row.get("goals_for", 0)), reverse=True)
    return [row["team_name"] for row in records]


def _draw_winners_cup_groups(participants: List[str], rng: random.Random) -> Dict[str, List[str]]:
    group_names = ["A", "B", "C", "D"]
    groups = {group_name: [] for group_name in group_names}
    for pot_index in range(4):
        pot = participants[pot_index * 4 : (pot_index + 1) * 4]
        shuffled_pot = pot[:]
        rng.shuffle(shuffled_pot)
        for group_name, team_name in zip(group_names, shuffled_pot):
            groups[group_name].append(team_name)
    return groups


def _build_winners_cup_group_fixtures(groups: Dict[str, List[str]]) -> Dict[str, List[dict]]:
    schedule_templates = [
        ((0, 3), (1, 2)),
        ((3, 1), (2, 0)),
        ((0, 1), (2, 3)),
        ((3, 0), (2, 1)),
        ((1, 3), (0, 2)),
        ((1, 0), (3, 2)),
    ]
    fixtures: Dict[str, List[dict]] = {}
    for index, pairings in enumerate(schedule_templates, start=1):
        matchday_key = f"winners_cup_group_{index}"
        fixtures[matchday_key] = []
        for group_name, teams in groups.items():
            for home_index, away_index in pairings:
                fixtures[matchday_key].append(
                    {
                        "group": group_name,
                        "home": teams[home_index],
                        "away": teams[away_index],
                    }
                )
    return fixtures


def _build_super_cup_participants(previous_premier_order: List[str], previous_champions: dict) -> List[str]:
    participants: List[str] = []
    candidates = [
        *previous_premier_order[:2],
        previous_champions.get(CHALLENGE_CUP),
        previous_champions.get(WINNERS_CUP),
        *previous_premier_order[2:4],
    ]
    for team_name in candidates:
        if team_name and team_name not in participants:
            participants.append(team_name)
        if len(participants) == 4:
            break
    if len(participants) < 4:
        raise ValueError("超级杯参赛队不足 4 支。")
    return participants


def _extract_cup_champions(cup_state: dict) -> Dict[str, Optional[str]]:
    return {
        WINNERS_CUP: cup_state.get("winners_cup", {}).get("champion"),
        CHALLENGE_CUP: cup_state.get("challenge_cup", {}).get("champion"),
        SUPER_CUP: cup_state.get("super_cup", {}).get("champion"),
    }


def _simulate_cup_events(
    state: dict,
    snapshot: SaveSnapshot,
    week: WeekScheduleEntry,
    rng: random.Random,
) -> List[MatchdayReport]:
    reports: List[MatchdayReport] = []
    for event_key in week.cup_events:
        if event_key.startswith("winners_cup_"):
            report = _simulate_winners_cup_event(state, snapshot, event_key, rng)
        elif event_key.startswith("challenge_cup_"):
            report = _simulate_challenge_cup_event(state, snapshot, event_key, rng)
        elif event_key.startswith("super_cup_"):
            report = _simulate_super_cup_event(state, snapshot, event_key, rng)
        else:
            report = None
        if report is not None:
            reports.append(report)
    return reports


def _simulate_winners_cup_event(
    state: dict,
    snapshot: SaveSnapshot,
    event_key: str,
    rng: random.Random,
) -> Optional[MatchdayReport]:
    cup = state.get("cup_state", {}).get("winners_cup", {})
    if not cup.get("active"):
        return None

    fixtures: List[dict]
    if event_key.startswith("winners_cup_group_"):
        fixtures = cup["group_fixtures"].get(event_key, [])
    else:
        fixtures = _ensure_winners_cup_knockout_pairs(state, snapshot, event_key, rng)

    team_lookup = {team.name: team for team in snapshot.premier_teams}
    round_number = _cup_round_number(event_key)
    report = MatchdayReport(round_number=round_number, competition=WINNERS_CUP)
    for fixture_data in fixtures:
        fixture = Fixture(
            round_number=round_number,
            home_team=team_lookup[fixture_data["home"]],
            away_team=team_lookup[fixture_data["away"]],
            competition=WINNERS_CUP,
        )
        report.results.append(simulate_match(fixture, rng))

    cup.setdefault("results", {})[event_key] = [_serialize_match_result(result) for result in report.results]
    if event_key == "winners_cup_final_leg_2":
        pairings = cup.get("knockout_pairs", {}).get("winners_cup_final_leg_1", [])
        if pairings:
            cup["champion"] = _resolve_two_leg_stage_winner(
                cup["results"].get("winners_cup_final_leg_1", []),
                cup["results"].get("winners_cup_final_leg_2", []),
                pairings[0]["home"],
                pairings[0]["away"],
                snapshot,
                rng,
                WINNERS_CUP,
            )
    return report


def _ensure_winners_cup_knockout_pairs(
    state: dict,
    snapshot: SaveSnapshot,
    event_key: str,
    rng: random.Random,
) -> List[dict]:
    cup = state["cup_state"]["winners_cup"]
    knockout_pairs = cup.setdefault("knockout_pairs", {})
    if event_key in knockout_pairs:
        return knockout_pairs[event_key]

    if event_key.startswith("winners_cup_quarterfinal"):
        standings = _winners_cup_group_standings(state, snapshot)
        first_leg_pairs = [
            {"home": standings["B"][1], "away": standings["A"][0]},
            {"home": standings["A"][1], "away": standings["B"][0]},
            {"home": standings["D"][1], "away": standings["C"][0]},
            {"home": standings["C"][1], "away": standings["D"][0]},
        ]
        second_leg_pairs = [{"home": pair["away"], "away": pair["home"]} for pair in first_leg_pairs]
        knockout_pairs["winners_cup_quarterfinal_leg_1"] = first_leg_pairs
        knockout_pairs["winners_cup_quarterfinal_leg_2"] = second_leg_pairs

    elif event_key.startswith("winners_cup_semifinal"):
        qf_pairs = knockout_pairs["winners_cup_quarterfinal_leg_1"]
        winners = [
            _resolve_two_leg_stage_winner(
                cup["results"]["winners_cup_quarterfinal_leg_1"],
                cup["results"]["winners_cup_quarterfinal_leg_2"],
                pair["away"],
                pair["home"],
                snapshot,
                rng,
                WINNERS_CUP,
            )
            for pair in qf_pairs
        ]
        first_leg_pairs = [
            {"home": winners[2], "away": winners[0]},
            {"home": winners[3], "away": winners[1]},
        ]
        second_leg_pairs = [{"home": pair["away"], "away": pair["home"]} for pair in first_leg_pairs]
        knockout_pairs["winners_cup_semifinal_leg_1"] = first_leg_pairs
        knockout_pairs["winners_cup_semifinal_leg_2"] = second_leg_pairs

    elif event_key.startswith("winners_cup_final"):
        semi_pairs = knockout_pairs["winners_cup_semifinal_leg_1"]
        winners = [
            _resolve_two_leg_stage_winner(
                cup["results"]["winners_cup_semifinal_leg_1"],
                cup["results"]["winners_cup_semifinal_leg_2"],
                pair["away"],
                pair["home"],
                snapshot,
                rng,
                WINNERS_CUP,
            )
            for pair in semi_pairs
        ]
        if rng.random() < 0.5:
            first_leg = [{"home": winners[0], "away": winners[1]}]
        else:
            first_leg = [{"home": winners[1], "away": winners[0]}]
        second_leg = [{"home": first_leg[0]["away"], "away": first_leg[0]["home"]}]
        knockout_pairs["winners_cup_final_leg_1"] = first_leg
        knockout_pairs["winners_cup_final_leg_2"] = second_leg

    return knockout_pairs[event_key]


def _winners_cup_group_standings(state: dict, snapshot: SaveSnapshot) -> Dict[str, List[str]]:
    cup = state["cup_state"]["winners_cup"]
    team_lookup = {team.name: team for team in snapshot.premier_teams}
    standings: Dict[str, List[str]] = {}
    rng = _rng()
    for group_name, teams in cup["groups"].items():
        rows = {team_name: TableRow(team=team_lookup[team_name]) for team_name in teams}
        results: List[MatchResult] = []
        for event_key in sorted(cup["results"]):
            if not event_key.startswith("winners_cup_group_"):
                continue
            for result_data in cup["results"][event_key]:
                if result_data["home_team"] not in teams:
                    continue
                result = _deserialize_match_result(result_data, team_lookup, _cup_round_number(event_key), WINNERS_CUP)
                results.append(result)
                _apply_table_result(rows, result)
        ordered = _rank_table_rows(list(rows.values()), results, _build_ranking_playoff_resolutions(list(rows.values()), results, rng))
        standings[group_name] = [row.team.name for row in ordered]
    return standings


def _simulate_challenge_cup_event(
    state: dict,
    snapshot: SaveSnapshot,
    event_key: str,
    rng: random.Random,
) -> Optional[MatchdayReport]:
    cup = state.get("cup_state", {}).get("challenge_cup", {})
    if not cup.get("active"):
        return None

    pairings = _ensure_challenge_cup_pairings(state, event_key, rng)
    team_lookup = {team.name: team for team in snapshot.teams}
    report = MatchdayReport(round_number=_cup_round_number(event_key), competition=CHALLENGE_CUP)
    winners: List[str] = []
    for pairing in pairings:
        fixture = Fixture(
            round_number=report.round_number,
            home_team=team_lookup[pairing["home"]],
            away_team=team_lookup[pairing["away"]],
            competition=CHALLENGE_CUP,
        )
        result = simulate_match(fixture, rng)
        winner = _resolve_single_match_winner(result, pairing["home"], pairing["away"], team_lookup, cup["seeds"], rng)
        winners.append(winner)
        report.results.append(result)
    cup.setdefault("results", {})[event_key] = [_serialize_match_result(result) for result in report.results]
    cup.setdefault("winners", {})[event_key] = winners
    if event_key == "challenge_cup_final":
        cup["champion"] = winners[0]
    return report


def _ensure_challenge_cup_pairings(state: dict, event_key: str, rng: random.Random) -> List[dict]:
    cup = state["cup_state"]["challenge_cup"]
    pairings = cup.setdefault("pairings", {})
    if event_key in pairings:
        return pairings[event_key]

    if event_key == "challenge_cup_r32":
        teams = cup["participants"][:]
        rng.shuffle(teams)
    elif event_key == "challenge_cup_r16":
        teams = cup["winners"]["challenge_cup_r32"][:]
        rng.shuffle(teams)
    elif event_key == "challenge_cup_quarterfinal":
        teams = cup["winners"]["challenge_cup_r16"][:]
        rng.shuffle(teams)
    elif event_key == "challenge_cup_semifinal":
        teams = cup["winners"]["challenge_cup_quarterfinal"][:]
        rng.shuffle(teams)
    elif event_key == "challenge_cup_final":
        teams = cup["winners"]["challenge_cup_semifinal"][:]
    else:
        teams = []

    stage_pairs = []
    for index in range(0, len(teams), 2):
        first_team = teams[index]
        second_team = teams[index + 1]
        higher, lower = _sort_by_seed(first_team, second_team, cup["seeds"])
        stage_pairs.append({"home": higher, "away": lower})
    pairings[event_key] = stage_pairs
    return stage_pairs


def _simulate_super_cup_event(
    state: dict,
    snapshot: SaveSnapshot,
    event_key: str,
    rng: random.Random,
) -> Optional[MatchdayReport]:
    cup = state.get("cup_state", {}).get("super_cup", {})
    if not cup.get("active"):
        return None

    team_lookup = {team.name: team for team in snapshot.teams}
    report = MatchdayReport(round_number=_cup_round_number(event_key), competition=SUPER_CUP)
    if event_key == "super_cup_semifinal":
        winners: List[str] = []
        for home_name, away_name in cup["semifinals"]:
            if rng.random() < 0.5:
                home_name, away_name = away_name, home_name
            fixture = Fixture(report.round_number, team_lookup[home_name], team_lookup[away_name], SUPER_CUP)
            result = simulate_match(fixture, rng)
            winners.append(_resolve_single_match_winner(result, home_name, away_name, team_lookup, None, rng))
            report.results.append(result)
        cup["results"][event_key] = [_serialize_match_result(result) for result in report.results]
        cup["finalists"] = winners
    else:
        home_name, away_name = cup["finalists"]
        if rng.random() < 0.5:
            home_name, away_name = away_name, home_name
        fixture = Fixture(report.round_number, team_lookup[home_name], team_lookup[away_name], SUPER_CUP)
        result = simulate_match(fixture, rng)
        cup["champion"] = _resolve_single_match_winner(result, home_name, away_name, team_lookup, None, rng)
        report.results.append(result)
        cup["results"][event_key] = [_serialize_match_result(result) for result in report.results]
    return report


def _resolve_single_match_winner(
    result: MatchResult,
    home_name: str,
    away_name: str,
    team_lookup: Dict[str, Team],
    seeds: Optional[Dict[str, int]],
    rng: random.Random,
) -> str:
    if result.home_goals > result.away_goals:
        return home_name
    if result.away_goals > result.home_goals:
        return away_name

    if seeds is not None:
        preferred = home_name if seeds[home_name] < seeds[away_name] else away_name
        other = away_name if preferred == home_name else home_name
    else:
        preferred = home_name if team_lookup[home_name].rating >= team_lookup[away_name].rating else away_name
        other = away_name if preferred == home_name else home_name
    winner = preferred if rng.random() < 0.6 else other
    result.key_events.append(f"常规时间战平，{winner} 通过点球大战晋级。")
    return winner


def _sort_by_seed(first_team: str, second_team: str, seeds: Dict[str, int]) -> tuple[str, str]:
    if seeds[first_team] < seeds[second_team]:
        return first_team, second_team
    return second_team, first_team


def _resolve_two_leg_stage_winner(
    first_leg_results: List[dict],
    second_leg_results: List[dict],
    higher_seed: str,
    lower_seed: str,
    snapshot: SaveSnapshot,
    rng: random.Random,
    competition: str,
) -> str:
    team_lookup = {team.name: team for team in snapshot.teams}
    all_results = []
    for result_data in [*first_leg_results, *second_leg_results]:
        teams = {result_data["home_team"], result_data["away_team"]}
        if teams != {higher_seed, lower_seed}:
            continue
        round_number = 1 if result_data in first_leg_results else 2
        all_results.append(_deserialize_match_result(result_data, team_lookup, round_number, competition))

    aggregate = {higher_seed: 0, lower_seed: 0}
    away_goals = {higher_seed: 0, lower_seed: 0}
    second_leg_result: Optional[MatchResult] = None
    for result in all_results:
        aggregate[result.home_team.name] += result.home_goals
        aggregate[result.away_team.name] += result.away_goals
        away_goals[result.away_team.name] += result.away_goals
        second_leg_result = result

    if aggregate[higher_seed] != aggregate[lower_seed]:
        return higher_seed if aggregate[higher_seed] > aggregate[lower_seed] else lower_seed
    if away_goals[higher_seed] != away_goals[lower_seed]:
        return higher_seed if away_goals[higher_seed] > away_goals[lower_seed] else lower_seed

    winner = higher_seed if rng.random() < 0.5 else lower_seed
    if second_leg_result is not None:
        second_leg_result.key_events.append(f"两回合总比分与客场进球都相同，{winner} 通过点球大战晋级。")
    return winner


def _cup_round_number(event_key: str) -> int:
    order = {
        "winners_cup_group_1": 1,
        "winners_cup_group_2": 2,
        "winners_cup_group_3": 3,
        "winners_cup_group_4": 4,
        "winners_cup_group_5": 5,
        "winners_cup_group_6": 6,
        "winners_cup_quarterfinal_leg_1": 7,
        "winners_cup_quarterfinal_leg_2": 8,
        "winners_cup_semifinal_leg_1": 9,
        "winners_cup_semifinal_leg_2": 10,
        "winners_cup_final_leg_1": 11,
        "winners_cup_final_leg_2": 12,
        "challenge_cup_r32": 1,
        "challenge_cup_r16": 2,
        "challenge_cup_quarterfinal": 3,
        "challenge_cup_semifinal": 4,
        "challenge_cup_final": 5,
        "super_cup_semifinal": 1,
        "super_cup_final": 2,
    }
    return order[event_key]


def _simulate_promotion_playoff_stage(
    state: dict,
    snapshot: SaveSnapshot,
    week: WeekScheduleEntry,
    rng: random.Random,
) -> List[MatchdayReport]:
    playoff_state = state.setdefault("promotion_playoff", {})
    if not playoff_state:
        seeds = [row.team.name for row in snapshot.second_table[2:6]]
        playoff_state.update(
            {
                "semifinals": [
                    {"higher_seed": seeds[0], "lower_seed": seeds[3]},
                    {"higher_seed": seeds[1], "lower_seed": seeds[2]},
                ]
            }
        )

    reports: List[MatchdayReport] = []
    stage = week.promotion_playoff_stage
    team_lookup = {team.name: team for team in snapshot.second_teams}

    if stage == "promotion_playoff_semi_leg_1":
        report = MatchdayReport(round_number=1, competition=PLAYOFF_COMPETITION)
        for pairing in playoff_state["semifinals"]:
            fixture = Fixture(
                round_number=1,
                home_team=team_lookup[pairing["lower_seed"]],
                away_team=team_lookup[pairing["higher_seed"]],
                competition=PLAYOFF_COMPETITION,
            )
            report.results.append(_simulate_quick_match(fixture, rng, with_events=False))
        reports.append(report)

    elif stage == "promotion_playoff_semi_leg_2":
        report = MatchdayReport(round_number=2, competition=PLAYOFF_COMPETITION)
        for pairing in playoff_state["semifinals"]:
            fixture = Fixture(
                round_number=2,
                home_team=team_lookup[pairing["higher_seed"]],
                away_team=team_lookup[pairing["lower_seed"]],
                competition=PLAYOFF_COMPETITION,
            )
            report.results.append(_simulate_quick_match(fixture, rng, with_events=False))
        reports.append(report)

    else:
        finalists = _determine_semifinal_winners(state, snapshot)
        higher_seed, lower_seed = _sort_second_division_seed(snapshot, finalists[0], finalists[1])
        playoff_state["final"] = {"higher_seed": higher_seed, "lower_seed": lower_seed}
        fixture = Fixture(
            round_number=3 if stage == "promotion_playoff_final_leg_1" else 4,
            home_team=team_lookup[lower_seed if stage == "promotion_playoff_final_leg_1" else higher_seed],
            away_team=team_lookup[higher_seed if stage == "promotion_playoff_final_leg_1" else lower_seed],
            competition=PLAYOFF_COMPETITION,
        )
        report = MatchdayReport(round_number=fixture.round_number, competition=PLAYOFF_COMPETITION)
        report.results.append(_simulate_quick_match(fixture, rng, with_events=False))
        reports.append(report)

    return reports


def _determine_semifinal_winners(state: dict, snapshot: SaveSnapshot) -> List[str]:
    playoff_state = state.get("promotion_playoff", {})
    semifinal_results = [
        matchday
        for week_data in state.get("simulated_weeks", [])
        for matchday in week_data.get("playoff_matchdays", [])
        if matchday.get("round_number") in {1, 2}
    ]
    team_lookup = {team.name: team for team in snapshot.second_teams}
    winners: List[str] = []
    for pairing in playoff_state.get("semifinals", []):
        related = []
        for matchday_data in semifinal_results:
            for result_data in matchday_data.get("results", []):
                teams = {result_data["home_team"], result_data["away_team"]}
                if teams == {pairing["higher_seed"], pairing["lower_seed"]}:
                    related.append(
                        _deserialize_match_result(
                            result_data,
                            team_lookup,
                            int(matchday_data["round_number"]),
                            PLAYOFF_COMPETITION,
                        )
                    )
        winners.append(_determine_two_leg_winner(related, pairing["higher_seed"], pairing["lower_seed"]))
    return winners


def _determine_promotion_playoff_winner(state: dict, snapshot: SaveSnapshot) -> str:
    final_pairing = state.get("promotion_playoff", {}).get("final")
    if not final_pairing:
        finalists = _determine_semifinal_winners(state, snapshot)
        higher_seed, lower_seed = _sort_second_division_seed(snapshot, finalists[0], finalists[1])
        final_pairing = {"higher_seed": higher_seed, "lower_seed": lower_seed}

    final_results = [
        matchday
        for week_data in state.get("simulated_weeks", [])
        for matchday in week_data.get("playoff_matchdays", [])
        if matchday.get("round_number") in {3, 4}
    ]
    team_lookup = {team.name: team for team in snapshot.second_teams}
    results = [
        _deserialize_match_result(
            result_data,
            team_lookup,
            int(matchday_data["round_number"]),
            PLAYOFF_COMPETITION,
        )
        for matchday_data in final_results
        for result_data in matchday_data.get("results", [])
        if {result_data["home_team"], result_data["away_team"]} == {final_pairing["higher_seed"], final_pairing["lower_seed"]}
    ]
    return _determine_two_leg_winner(results, final_pairing["higher_seed"], final_pairing["lower_seed"])


def _determine_two_leg_winner(results: List[MatchResult], higher_seed: str, lower_seed: str) -> str:
    aggregate = {higher_seed: 0, lower_seed: 0}
    away_goals = {higher_seed: 0, lower_seed: 0}
    for result in results:
        home_name = result.home_team.name
        away_name = result.away_team.name
        aggregate[home_name] += result.home_goals
        aggregate[away_name] += result.away_goals
        away_goals[away_name] += result.away_goals

    if aggregate[higher_seed] != aggregate[lower_seed]:
        return higher_seed if aggregate[higher_seed] > aggregate[lower_seed] else lower_seed
    if away_goals[higher_seed] != away_goals[lower_seed]:
        return higher_seed if away_goals[higher_seed] > away_goals[lower_seed] else lower_seed
    return higher_seed


def _sort_second_division_seed(snapshot: SaveSnapshot, first_team: str, second_team: str) -> tuple[str, str]:
    order = [row.team.name for row in snapshot.second_table]
    if order.index(first_team) < order.index(second_team):
        return first_team, second_team
    return second_team, first_team



def _simulate_quick_match(
    fixture: Fixture,
    rng: random.Random,
    with_events: bool = True,
) -> MatchResult:
    home_goals = _random_goals(
        rng,
        home_bias=0.20,
        real_player_count=fixture.home_team.real_player_count,
        opponent_real_player_count=fixture.away_team.real_player_count,
    )
    away_goals = _random_goals(
        rng,
        home_bias=0.0,
        real_player_count=fixture.away_team.real_player_count,
        opponent_real_player_count=fixture.home_team.real_player_count,
    )
    key_events = []
    if with_events:
        key_events.append(f"{fixture.competition} 采用快速模拟，本场直接结算出 {home_goals}-{away_goals}。")
    return MatchResult(
        fixture=fixture,
        home_goals=home_goals,
        away_goals=away_goals,
        key_events=key_events,
        player_stats={},
    )


def _random_goals(
    rng: random.Random,
    home_bias: float,
    real_player_count: int,
    opponent_real_player_count: int,
) -> int:
    real_player_bonus = min(0.30, real_player_count * 0.05)
    matchup_bonus = min(0.12, max(0, real_player_count - opponent_real_player_count) * 0.03)
    opponent_drag = min(0.08, opponent_real_player_count * 0.015)
    roll = rng.random() + home_bias + real_player_bonus + matchup_bonus - opponent_drag
    if roll < 0.22:
        return 0
    if roll < 0.54:
        return 1
    if roll < 0.80:
        return 2
    if roll < 0.94:
        return 3
    if roll < 1.00:
        return 4
    if roll < 1.08:
        return 5
    return 6


def _archive_current_season(state: dict) -> None:
    snapshot = build_snapshot_from_state(state)
    archive = _snapshot_to_archive(snapshot)
    if "last_transition" in state:
        archive["last_transition"] = state["last_transition"]
    history = state.setdefault("history", [])
    history = [season for season in history if int(season["season_number"]) != snapshot.season_number]
    history.append(archive)
    history.sort(key=lambda row: int(row["season_number"]))
    state["history"] = history


def _snapshot_to_archive(snapshot: SaveSnapshot) -> dict:
    team_enrichment = _build_team_season_enrichment(snapshot)
    season_awards = _build_season_awards(snapshot) if snapshot.season_complete else _empty_season_awards()
    player_awards = _build_player_award_enrichment(snapshot.season_number, season_awards)
    player_settlement_points = _build_player_settlement_points(snapshot)
    team_stats = []
    for row in snapshot.team_stats:
        enriched_row = _serialize_team_season_stats(row)
        enriched_row.update(team_enrichment.get(row.team_name, {}))
        team_stats.append(enriched_row)

    player_stats = []
    for row in snapshot.player_stats:
        enriched_row = _serialize_player_season_stats(row)
        enriched_row.update(_player_season_team_display(snapshot, row))
        team_context = team_enrichment.get(row.team_name, {})
        enriched_row.update(
            {
                "division": team_context.get("division", PREMIER_DIVISION),
                "league_rank": team_context.get("league_rank"),
                "league_result": team_context.get("league_result", "未知"),
                "winners_cup_result": team_context.get("winners_cup_result", "未参赛"),
                "challenge_cup_result": team_context.get("challenge_cup_result", "未参赛"),
                "super_cup_result": team_context.get("super_cup_result", "未参赛"),
                "honor_points": team_context.get("honor_points", 0),
                "premier_titles": team_context.get("premier_titles", 0),
                "second_titles": team_context.get("second_titles", 0),
                "winners_cup_titles": team_context.get("winners_cup_titles", 0),
                "challenge_cup_titles": team_context.get("challenge_cup_titles", 0),
                "super_cup_titles": team_context.get("super_cup_titles", 0),
                "total_titles": team_context.get("total_titles", 0),
            }
        )
        enriched_row.update(player_awards.get(_player_history_key_from_row(enriched_row), _empty_player_awards()))
        player_stats.append(enriched_row)

    return {
        "season_number": snapshot.season_number,
        "player_stats": player_stats,
        "team_stats": team_stats,
        "premier_order": [row.team.name for row in snapshot.premier_table],
        "second_order": [row.team.name for row in snapshot.second_table],
        "cup_champions": snapshot.cup_champions,
        "ranking_playoffs": snapshot.ranking_playoffs,
        "season_awards": season_awards,
        "player_settlement_points": player_settlement_points,
        "cup_state": snapshot.cup_state,
        "simulated_weeks": snapshot.simulated_weeks,
    }


def _season_archives(snapshot: SaveSnapshot) -> List[dict]:
    history = list(snapshot.history)
    archived_season_numbers = {int(season["season_number"]) for season in history}
    if snapshot.season_number not in archived_season_numbers:
        history.append(_snapshot_to_archive(snapshot))
    return history


def _build_player_settlement_points(snapshot: SaveSnapshot) -> List[dict]:
    real_rows_by_key = {
        _settlement_cache_key(row): row
        for row in snapshot.player_stats
        if row.player.is_real
    }
    points: List[dict] = []
    seen = set()
    stage_configs = (
        ("winter", "冬窗", WINTER_SETTLEMENT_WEEK),
        ("final", "赛季末", FINAL_SETTLEMENT_WEEK),
    )
    for cache_name, stage_label, week_number in stage_configs:
        for cache_key, values in snapshot.settlement_cache.get(cache_name, {}).items():
            row = real_rows_by_key.get(cache_key)
            if row is None:
                continue
            rating = values.get("season_rating")
            market_value = values.get("market_value")
            if rating is None or market_value is None:
                continue
            point_key = (_player_history_key_for_player(row.player), stage_label)
            if point_key in seen:
                continue
            seen.add(point_key)
            points.append(
                _build_player_settlement_point(
                    row,
                    stage_label=stage_label,
                    week_number=week_number,
                    season_rating=rating,
                    market_value=market_value,
                    team_display_name=_player_season_team_display(snapshot, row)["team_display_name"],
                )
            )

    if snapshot.current_week >= FINAL_SETTLEMENT_WEEK or snapshot.season_complete:
        for row in snapshot.player_stats:
            if not row.player.is_real or row.season_rating is None or row.market_value is None:
                continue
            point_key = (_player_history_key_for_player(row.player), "赛季末")
            if point_key in seen:
                continue
            seen.add(point_key)
            points.append(
                _build_player_settlement_point(
                    row,
                    stage_label="赛季末",
                    week_number=FINAL_SETTLEMENT_WEEK,
                    season_rating=row.season_rating,
                    market_value=row.market_value,
                    team_display_name=_player_season_team_display(snapshot, row)["team_display_name"],
                )
            )

    points.sort(key=lambda item: (item["label"], item["week_number"]))
    return points


def _build_player_settlement_point(
    row: PlayerSeasonStats,
    stage_label: str,
    week_number: int,
    season_rating: float,
    market_value: float,
    team_display_name: Optional[str] = None,
) -> dict:
    return {
        "player_id": _player_history_key_for_player(row.player),
        "label": row.player.label,
        "position": row.player.position,
        "team_name": row.team_name,
        "team_display_name": team_display_name or row.team_name,
        "season_number": row.season_number,
        "stage": stage_label,
        "week_number": week_number,
        "season_rating": season_rating,
        "market_value": market_value,
    }


def get_season_awards(snapshot: SaveSnapshot) -> List[dict]:
    seasons = _season_archives(snapshot)
    seasons.sort(key=lambda item: int(item["season_number"]))
    award_rows = []
    for season in seasons:
        awards = season.get("season_awards")
        if not awards:
            continue
        if not awards.get("top20") and not awards.get("competitions"):
            continue
        award_rows.append(
            {
                "season_number": int(season["season_number"]),
                **awards,
            }
        )
    return award_rows


def get_competition_archive_rows(snapshot: SaveSnapshot) -> List[dict]:
    seasons = _season_archives(snapshot)
    rows: List[dict] = []
    current_competition_awards = _build_competition_awards(snapshot)
    for season in sorted(seasons, key=lambda item: int(item["season_number"])):
        season_number = int(season["season_number"])
        results_by_competition = _collect_competition_results_from_weeks(season.get("simulated_weeks", []))
        awards = season.get("season_awards", _empty_season_awards())
        competition_awards = dict(awards.get("competitions", {}))
        if season_number == snapshot.season_number and not snapshot.season_complete:
            competition_awards.update(current_competition_awards)

        competition_names = sorted(
            set(results_by_competition)
            | set(competition_awards)
            | {PREMIER_DIVISION, SECOND_DIVISION, WINNERS_CUP, CHALLENGE_CUP, SUPER_CUP},
            key=_competition_sort_key,
        )
        competitions = []
        for competition in competition_names:
            competitions.append(
                {
                    "name": competition,
                    "results": results_by_competition.get(competition, []),
                    "awards": competition_awards.get(competition, {}),
                }
            )
        rows.append(
            {
                "season_number": season_number,
                "competitions": competitions,
                "cup_champions": season.get("cup_champions", {}),
                "premier_order": season.get("premier_order", []),
                "second_order": season.get("second_order", []),
            }
        )
    return rows


def get_player_trend_points(snapshot: SaveSnapshot, player_id: Optional[str], label: Optional[str]) -> List[dict]:
    history_key = _player_lookup_key(player_id, label)
    points: List[dict] = []
    seen_keys = set()
    for season in _season_archives(snapshot):
        season_number = int(season["season_number"])
        for point in season.get("player_settlement_points", []):
            if not _matches_player_lookup(point, history_key, label):
                continue
            dedupe_key = (season_number, point.get("stage"), point.get("week_number"))
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            points.append(dict(point))

        if any(point.get("season_number") == season_number and point.get("stage") == "赛季末" for point in points):
            continue
        for row in season.get("player_stats", []):
            if not _matches_player_lookup(row, history_key, label):
                continue
            if row.get("season_rating") is None or row.get("market_value") is None:
                continue
            points.append(
                {
                    "player_id": _player_history_key_from_row(row),
                    "label": _normalize_player_label(row["label"]),
                    "position": row["position"],
                    "team_name": row["team_name"],
                    "team_display_name": row.get("team_display_name") or row["team_name"],
                    "season_number": season_number,
                    "stage": "赛季末",
                    "week_number": FINAL_SETTLEMENT_WEEK,
                    "season_rating": row.get("season_rating"),
                    "market_value": row.get("market_value"),
                }
            )
            break
    points.sort(key=lambda item: (int(item["season_number"]), int(item.get("week_number", 0))))
    return points


def get_trade_detail_rows(snapshot: SaveSnapshot) -> List[dict]:
    team_lookup = {team.name: team for team in snapshot.premier_teams}
    rows: List[dict] = []
    for item in snapshot.pending_transfer_review:
        team_a = team_lookup.get(item["team_a"])
        team_b = team_lookup.get(item["team_b"])
        if team_a is None or team_b is None:
            continue
        outgoing_a = item.get("team_a_players", [])
        outgoing_b = item.get("team_b_players", [])
        before_a = _real_position_counts(team_a)
        before_b = _real_position_counts(team_b)
        after_a = _position_counts_after_trade(before_a, outgoing_a, outgoing_b)
        after_b = _position_counts_after_trade(before_b, outgoing_b, outgoing_a)
        rows.append(
            {
                **item,
                "team_a_value_delta": round(item["team_b_total_value"] - item["team_a_total_value"], 2),
                "team_b_value_delta": round(item["team_a_total_value"] - item["team_b_total_value"], 2),
                "team_a_ability_delta": _ability_delta(outgoing_b, outgoing_a),
                "team_b_ability_delta": _ability_delta(outgoing_a, outgoing_b),
                "team_a_positions_before": before_a,
                "team_b_positions_before": before_b,
                "team_a_positions_after": after_a,
                "team_b_positions_after": after_b,
                "team_a_positions_valid": _position_counts_valid(after_a),
                "team_b_positions_valid": _position_counts_valid(after_b),
            }
        )
    return rows


def get_transfer_history_rows(snapshot: SaveSnapshot) -> List[dict]:
    rows: List[dict] = []
    for item in snapshot.transfer_history:
        team_a_players = _format_trade_player_names(item.get("team_a_players", []))
        team_b_players = _format_trade_player_names(item.get("team_b_players", []))
        rows.append(
            {
                "season_number": int(item.get("season_number", 0)),
                "week_number": int(item.get("week_number", 0)),
                "window": item.get("window", "-"),
                "team_a": item.get("team_a", "-"),
                "team_b": item.get("team_b", "-"),
                "team_a_players": team_a_players,
                "team_b_players": team_b_players,
                "team_a_total_value": float(item.get("team_a_total_value", 0.0)),
                "team_b_total_value": float(item.get("team_b_total_value", 0.0)),
                "value_gap": float(item.get("value_gap", 0.0)),
                "approved": bool(item.get("approved")),
                "status": item.get("status") or ("玩家通过" if item.get("approved") else "玩家拒绝"),
                "recalculated": bool(item.get("recalculated")),
                "reason": item.get("reason", ""),
            }
        )
    rows.sort(key=lambda row: (row["season_number"], row["week_number"]), reverse=True)
    return rows


def _player_season_team_display(snapshot: SaveSnapshot, row: PlayerSeasonStats) -> dict:
    team_names = _player_season_team_names(snapshot, row)
    return {
        "season_team_names": team_names,
        "team_display_name": " / ".join(team_names),
    }


def _player_season_team_names(snapshot: SaveSnapshot, row: PlayerSeasonStats) -> List[str]:
    winter_teams: List[str] = []
    summer_origin: Optional[str] = None

    for move in _player_transfer_moves_for_season(snapshot, row):
        window = move["window"]
        if window == "冬窗":
            _append_unique(winter_teams, move["source_team"])
            _append_unique(winter_teams, move["destination_team"])
        elif window == "夏窗" and summer_origin is None:
            summer_origin = move["source_team"]

    if winter_teams:
        return winter_teams
    if summer_origin:
        return [summer_origin]
    return [row.team_name]


def _player_transfer_moves_for_season(snapshot: SaveSnapshot, row: PlayerSeasonStats) -> List[dict]:
    moves: List[dict] = []
    for order_index, item in enumerate(snapshot.transfer_history):
        if int(item.get("season_number", 0)) != row.season_number:
            continue
        if not bool(item.get("approved")):
            continue
        team_a = item.get("team_a")
        team_b = item.get("team_b")
        for player in item.get("team_a_players", []):
            if _transfer_player_matches_row(player, row):
                moves.append(
                    {
                        "order_index": order_index,
                        "week_number": int(item.get("week_number", 0)),
                        "window": item.get("window", ""),
                        "source_team": team_a or row.team_name,
                        "destination_team": team_b or row.team_name,
                    }
                )
        for player in item.get("team_b_players", []):
            if _transfer_player_matches_row(player, row):
                moves.append(
                    {
                        "order_index": order_index,
                        "week_number": int(item.get("week_number", 0)),
                        "window": item.get("window", ""),
                        "source_team": team_b or row.team_name,
                        "destination_team": team_a or row.team_name,
                    }
                )
    moves.sort(key=lambda item: (item["week_number"], item["order_index"]))
    return moves


def _transfer_player_matches_row(player: dict, row: PlayerSeasonStats) -> bool:
    player_id = player.get("player_id")
    if player_id and player_id == row.player.player_id:
        return True
    name = _clean_player_name(player.get("name"))
    return bool(name and _normalize_player_label(name) == _normalize_player_label(row.player.label))


def _append_unique(items: List[str], value: Optional[str]) -> None:
    if value and value not in items:
        items.append(value)


def get_season_archive_rows(snapshot: SaveSnapshot) -> List[dict]:
    rows: List[dict] = []
    for season in _season_archives(snapshot):
        season_number = int(season["season_number"])
        rows.append(
            {
                "season_number": season_number,
                "premier_table": _archive_league_table_rows(season, PREMIER_DIVISION),
                "second_table": _archive_league_table_rows(season, SECOND_DIVISION),
                "cup_rows": _archive_cup_tree_rows(season),
                "cup_champions": dict(season.get("cup_champions", {})),
            }
        )
    rows.sort(key=lambda row: row["season_number"], reverse=True)
    return rows


def _format_trade_player_names(players: List[dict]) -> str:
    if not players:
        return "-"
    return "、".join(
        f"{player.get('name', '-')}/{player.get('position', '-')}/{float(player.get('market_value', 0.0)):.2f}M"
        for player in players
    )


def _archive_league_table_rows(season: dict, division: str) -> List[dict]:
    rows = [
        dict(row)
        for row in season.get("team_stats", [])
        if row.get("division") == division
    ]
    rows.sort(
        key=lambda row: (
            int(row.get("points", 0)),
            int(row.get("goal_diff", 0)),
            int(row.get("goals_for", 0)),
        ),
        reverse=True,
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def _archive_cup_tree_rows(season: dict) -> List[dict]:
    cup_state = season.get("cup_state", {})
    rows: List[dict] = []
    rows.extend(_winners_cup_archive_rows(cup_state.get("winners_cup", {})))
    rows.extend(_challenge_cup_archive_rows(cup_state.get("challenge_cup", {})))
    rows.extend(_super_cup_archive_rows(cup_state.get("super_cup", {})))
    return rows


def _winners_cup_archive_rows(cup: dict) -> List[dict]:
    if not cup.get("active"):
        return [{"competition": WINNERS_CUP, "stage": "未启用", "node": "-", "summary": "本赛季未启用"}]
    rows: List[dict] = []
    for group_name, teams in sorted(cup.get("groups", {}).items()):
        rows.append(
            {
                "competition": WINNERS_CUP,
                "stage": "小组阶段",
                "node": group_name,
                "summary": "、".join(teams),
            }
        )
    for stage_key, pairs in sorted(cup.get("knockout_pairs", {}).items()):
        rows.append(
            {
                "competition": WINNERS_CUP,
                "stage": "淘汰阶段",
                "node": _cup_stage_label(stage_key),
                "summary": _format_pair_rows(pairs),
            }
        )
    if cup.get("champion"):
        rows.append({"competition": WINNERS_CUP, "stage": "冠军", "node": "-", "summary": cup["champion"]})
    return rows


def _challenge_cup_archive_rows(cup: dict) -> List[dict]:
    if not cup.get("active"):
        return [{"competition": CHALLENGE_CUP, "stage": "未启用", "node": "-", "summary": "本赛季未启用"}]
    rows: List[dict] = []
    participants = cup.get("participants", [])
    if participants:
        rows.append({"competition": CHALLENGE_CUP, "stage": "参赛队", "node": "32队", "summary": f"{len(participants)} 支球队"})
    for stage_key, winners in sorted(cup.get("winners", {}).items()):
        rows.append(
            {
                "competition": CHALLENGE_CUP,
                "stage": "淘汰树",
                "node": _cup_stage_label(stage_key),
                "summary": "、".join(winners) if winners else "-",
            }
        )
    if cup.get("champion"):
        rows.append({"competition": CHALLENGE_CUP, "stage": "冠军", "node": "-", "summary": cup["champion"]})
    return rows


def _super_cup_archive_rows(cup: dict) -> List[dict]:
    if not cup.get("active"):
        return [{"competition": SUPER_CUP, "stage": "未启用", "node": "-", "summary": "本赛季未启用"}]
    rows = [
        {
            "competition": SUPER_CUP,
            "stage": "参赛队",
            "node": "四强",
            "summary": "、".join(cup.get("participants", [])) or "-",
        }
    ]
    if cup.get("finalists"):
        rows.append(
            {
                "competition": SUPER_CUP,
                "stage": "决赛",
                "node": "-",
                "summary": "、".join(cup.get("finalists", [])),
            }
        )
    if cup.get("champion"):
        rows.append({"competition": SUPER_CUP, "stage": "冠军", "node": "-", "summary": cup["champion"]})
    return rows


def _cup_stage_label(stage_key: str) -> str:
    labels = {
        "winners_cup_quarterfinal_leg_1": "八强首回合",
        "winners_cup_quarterfinal_leg_2": "八强次回合",
        "winners_cup_semifinal_leg_1": "半决赛首回合",
        "winners_cup_semifinal_leg_2": "半决赛次回合",
        "winners_cup_final_leg_1": "决赛首回合",
        "winners_cup_final_leg_2": "决赛次回合",
        "challenge_cup_r32": "三十二强",
        "challenge_cup_r16": "十六强",
        "challenge_cup_quarterfinal": "八强",
        "challenge_cup_semifinal": "半决赛",
        "challenge_cup_final": "决赛",
    }
    return labels.get(stage_key, stage_key)


def _format_pair_rows(pairs: List[dict]) -> str:
    if not pairs:
        return "-"
    return " | ".join(f"{pair.get('home', '-')} vs {pair.get('away', '-')}" for pair in pairs)


def _player_lookup_key(player_id: Optional[str], label: Optional[str]) -> Optional[str]:
    if player_id and str(player_id).startswith("real::"):
        return str(player_id)
    if label:
        return f"real::{_normalize_player_label(label)}"
    return player_id


def _matches_player_lookup(row: dict, history_key: Optional[str], label: Optional[str]) -> bool:
    row_key = row.get("player_id")
    if row.get("is_real"):
        row_key = _player_history_key_from_row(row)
    if row_key == history_key:
        return True
    return bool(label and _normalize_player_label(row.get("label", "")) == _normalize_player_label(label))


def _position_counts_after_trade(before: Dict[str, int], outgoing: List[dict], incoming: List[dict]) -> Dict[str, int]:
    counts = dict(before)
    for player in outgoing:
        counts[player["position"]] = counts.get(player["position"], 0) - 1
    for player in incoming:
        counts[player["position"]] = counts.get(player["position"], 0) + 1
    return counts


def _ability_delta(incoming: List[dict], outgoing: List[dict]) -> int:
    return sum(int(player.get("ability", 0)) for player in incoming) - sum(int(player.get("ability", 0)) for player in outgoing)


def _position_counts_valid(counts: Dict[str, int]) -> bool:
    for position, limit in FORMATION_RULES.items():
        value = counts.get(position, 0)
        if value < 0 or value > limit:
            return False
    return True


def _empty_season_awards() -> dict:
    return {
        "top20": [],
        "competitions": {},
    }


def _empty_player_awards() -> dict:
    return {
        "top20_rank": None,
        "top20_score": None,
        "top20_finishes": 0,
        "top_scorer_awards": 0,
        "assist_leader_awards": 0,
        "mvp_awards": 0,
        "award_labels": [],
    }


def _build_season_awards(snapshot: SaveSnapshot) -> dict:
    player_rows = [row for row in snapshot.player_stats if row.player.is_real]
    if not player_rows:
        return _empty_season_awards()

    team_enrichment = _build_team_season_enrichment(snapshot)
    second_team_names = {team.name for team in snapshot.second_teams}
    top20_candidates = []
    for row in player_rows:
        rating = row.season_rating if row.season_rating is not None else _calculate_player_rating(row, max(1, row.appearances))
        team_context = team_enrichment.get(row.team_name, {})
        league_weight = _league_weight_for_team(row.team_name, second_team_names)
        award_score = _calculate_top20_score(
            row,
            rating,
            team_context.get("honor_points", 0),
            league_weight=league_weight,
        )
        top20_candidates.append(
            {
                "player_id": _player_history_key_for_player(row.player),
                "label": row.player.label,
                "position": row.player.position,
                "team_name": row.team_name,
                "ability": row.player.ability,
                "rating": rating,
                "market_value": row.market_value,
                "score": award_score,
                "goals": row.goals,
                "assists": row.assists,
                "chances_created": row.chances_created,
                "successful_defenses": row.successful_defenses,
                "successful_saves": row.successful_saves,
                "clean_sheets": row.clean_sheets,
            }
        )

    top20_candidates.sort(
        key=lambda item: (
            item["score"],
            item["rating"],
            item["goals"] + item["assists"],
            item["ability"],
        ),
        reverse=True,
    )
    top20 = []
    for rank, item in enumerate(top20_candidates[:20], start=1):
        top20.append({"rank": rank, **item})

    return {
        "top20": top20,
        "competitions": _build_competition_awards(snapshot),
    }


def _calculate_top20_score(
    row: PlayerSeasonStats,
    rating: float,
    team_honor_points: int,
    league_weight: float = 1.0,
) -> float:
    if row.player.position == POSITION_GOALKEEPER:
        production = 0.08 * row.successful_saves + 1.35 * row.clean_sheets
    else:
        production = (
            2.8 * row.goals
            + 2.2 * row.assists
            + 0.24 * row.chances_created
            + 0.36 * row.successful_defenses
        )
    score = 74.0 * rating + 1.15 * row.player.ability + production + 0.18 * team_honor_points
    return round(score * league_weight, 2)


def _build_competition_player_stats(snapshot: SaveSnapshot) -> Dict[str, List[dict]]:
    team_lookup = {team.name: team for team in snapshot.teams}
    player_registry = {
        row.player.player_id: row
        for row in snapshot.player_stats
        if row.player.is_real
    }
    stats_by_competition: Dict[str, Dict[str, PlayerSeasonStats]] = {}
    matches_by_competition: Dict[str, Dict[str, int]] = {}

    for simulated_week in snapshot.simulated_weeks:
        for matchday_key in ("premier_matchdays", "second_matchdays", "cup_matchdays"):
            for matchday_data in simulated_week.get(matchday_key, []):
                competition = matchday_data.get("competition", PREMIER_DIVISION)
                if competition not in AWARD_COMPETITIONS and competition != SECOND_DIVISION:
                    continue
                round_number = int(matchday_data["round_number"])
                player_stats_map = stats_by_competition.setdefault(competition, {})
                team_matches = matches_by_competition.setdefault(competition, {})
                for result_data in matchday_data.get("results", []):
                    result = _deserialize_match_result(result_data, team_lookup, round_number, competition)
                    team_matches[result.home_team.name] = team_matches.get(result.home_team.name, 0) + 1
                    team_matches[result.away_team.name] = team_matches.get(result.away_team.name, 0) + 1
                    for player_id, delta in result.player_stats.items():
                        registry_row = player_registry.get(player_id)
                        if registry_row is None:
                            continue
                        competition_row = player_stats_map.setdefault(
                            player_id,
                            PlayerSeasonStats(
                                player=registry_row.player,
                                team_name=registry_row.team_name,
                                season_number=snapshot.season_number,
                            ),
                        )
                        competition_row.apply_delta(delta)

    competition_rows: Dict[str, List[dict]] = {}
    for competition, player_stats_map in stats_by_competition.items():
        rows = []
        team_matches = matches_by_competition.get(competition, {})
        for row in player_stats_map.values():
            matches_played = max(1, team_matches.get(row.team_name, 0))
            rating = _calculate_player_rating(row, matches_played)
            rows.append(
                {
                    "player_id": _player_history_key_for_player(row.player),
                    "label": row.player.label,
                    "position": row.player.position,
                    "team_name": row.team_name,
                    "ability": row.player.ability,
                    "rating": rating,
                    "goals": row.goals,
                    "assists": row.assists,
                    "chances_created": row.chances_created,
                    "successful_defenses": row.successful_defenses,
                    "successful_saves": row.successful_saves,
                    "clean_sheets": row.clean_sheets,
                }
            )
        competition_rows[competition] = rows
    return competition_rows


def _build_competition_awards(snapshot: SaveSnapshot) -> Dict[str, dict]:
    competition_stats = _build_competition_player_stats(snapshot)
    competition_awards = {}
    for competition in AWARD_COMPETITIONS:
        rows = competition_stats.get(competition, [])
        if not rows:
            continue
        top_scorer = max(
            rows,
            key=lambda item: (
                item["goals"],
                item["assists"],
                item["rating"],
                item["ability"],
            ),
        )
        assist_leader = max(
            rows,
            key=lambda item: (
                item["assists"],
                item["goals"],
                item["rating"],
                item["ability"],
            ),
        )
        mvp = max(
            rows,
            key=lambda item: (
                item["rating"],
                item["goals"] + item["assists"],
                item["ability"],
            ),
        )
        competition_awards[competition] = {
            "top_scorer": top_scorer if top_scorer["goals"] > 0 else None,
            "assist_leader": assist_leader if assist_leader["assists"] > 0 else None,
            "mvp": mvp,
            "leaderboards": _build_competition_leaderboards(rows),
        }
    return competition_awards


def _build_competition_leaderboards(rows: List[dict]) -> dict:
    return {
        "goals": sorted(
            rows,
            key=lambda item: (item["goals"], item["assists"], item["rating"], item["ability"]),
            reverse=True,
        )[:20],
        "assists": sorted(
            rows,
            key=lambda item: (item["assists"], item["goals"], item["rating"], item["ability"]),
            reverse=True,
        )[:20],
        "ratings": sorted(
            rows,
            key=lambda item: (item["rating"], item["goals"] + item["assists"], item["ability"]),
            reverse=True,
        )[:20],
    }


def _collect_competition_results_from_weeks(simulated_weeks: List[dict]) -> Dict[str, List[dict]]:
    results_by_competition: Dict[str, List[dict]] = {}
    for week in simulated_weeks:
        week_number = int(week.get("week_number", 0))
        for matchday_key in ("premier_matchdays", "second_matchdays", "cup_matchdays", "playoff_matchdays"):
            for matchday in week.get(matchday_key, []):
                competition = matchday.get("competition", PREMIER_DIVISION)
                competition_results = results_by_competition.setdefault(competition, [])
                round_number = matchday.get("round_number", "-")
                for result in matchday.get("results", []):
                    competition_results.append(
                        {
                            "week_number": week_number,
                            "round_number": round_number,
                            "home_team": result["home_team"],
                            "away_team": result["away_team"],
                            "home_goals": result["home_goals"],
                            "away_goals": result["away_goals"],
                            "event_count": len(result.get("key_events", [])),
                        }
                    )
    return results_by_competition


def _competition_sort_key(competition: str) -> int:
    order = {
        PREMIER_DIVISION: 0,
        SECOND_DIVISION: 1,
        WINNERS_CUP: 2,
        CHALLENGE_CUP: 3,
        SUPER_CUP: 4,
        PLAYOFF_COMPETITION: 5,
    }
    return order.get(competition, 99)


def _build_player_award_enrichment(season_number: int, season_awards: dict) -> Dict[str, dict]:
    enrichment: Dict[str, dict] = {}

    for item in season_awards.get("top20", []):
        player_id = item["player_id"]
        entry = enrichment.setdefault(player_id, _empty_player_awards())
        entry["top20_rank"] = item["rank"]
        entry["top20_score"] = item["score"]
        entry["top20_finishes"] = 1
        entry["award_labels"].append(f"S{season_number} 年度Top20 第 {item['rank']} 名")

    for competition, awards in season_awards.get("competitions", {}).items():
        for award_key, field_name, label in (
            ("top_scorer", "top_scorer_awards", "射手王"),
            ("assist_leader", "assist_leader_awards", "助攻王"),
            ("mvp", "mvp_awards", "MVP"),
        ):
            item = awards.get(award_key)
            if not item:
                continue
            player_id = item["player_id"]
            entry = enrichment.setdefault(player_id, _empty_player_awards())
            entry[field_name] += 1
            entry["award_labels"].append(f"S{season_number} {competition}{label}")

    return enrichment


def _build_team_season_enrichment(snapshot: SaveSnapshot) -> Dict[str, dict]:
    premier_ranks = {row.team.name: index + 1 for index, row in enumerate(snapshot.premier_table)}
    second_ranks = {row.team.name: index + 1 for index, row in enumerate(snapshot.second_table)}
    winners_results = _build_winners_cup_team_results(snapshot)
    challenge_results = _build_challenge_cup_team_results(snapshot)
    super_results = _build_super_cup_team_results(snapshot)

    enrichment: Dict[str, dict] = {}
    for team in snapshot.teams:
        if team.division == PREMIER_DIVISION:
            league_rank = premier_ranks.get(team.name)
        else:
            league_rank = second_ranks.get(team.name)

        winners_result = winners_results.get(team.name, "未参赛")
        challenge_result = challenge_results.get(team.name, "未参赛")
        super_result = super_results.get(team.name, "未参赛")

        premier_titles = 1 if team.division == PREMIER_DIVISION and league_rank == 1 else 0
        second_titles = 1 if team.division == SECOND_DIVISION and league_rank == 1 else 0
        winners_titles = 1 if winners_result == "冠军" else 0
        challenge_titles = 1 if challenge_result == "冠军" else 0
        super_titles = 1 if super_result == "冠军" else 0

        honor_points = 0
        if team.division == PREMIER_DIVISION and league_rank is not None:
            honor_points += PREMIER_HONOR_POINTS.get(league_rank, 0)
        honor_points += WINNERS_CUP_HONOR_POINTS.get(winners_result, 0)
        honor_points += CHALLENGE_CUP_HONOR_POINTS.get(challenge_result, 0)
        honor_points += SUPER_CUP_HONOR_POINTS.get(super_result, 0)

        enrichment[team.name] = {
            "division": team.division,
            "league_rank": league_rank,
            "league_result": f"第 {league_rank} 名" if league_rank is not None else "未定",
            "winners_cup_result": winners_result,
            "challenge_cup_result": challenge_result,
            "super_cup_result": super_result,
            "honor_points": honor_points,
            "premier_titles": premier_titles,
            "second_titles": second_titles,
            "winners_cup_titles": winners_titles,
            "challenge_cup_titles": challenge_titles,
            "super_cup_titles": super_titles,
            "total_titles": premier_titles + second_titles + winners_titles + challenge_titles + super_titles,
        }
    return enrichment


def _build_winners_cup_team_results(snapshot: SaveSnapshot) -> Dict[str, str]:
    cup = snapshot.cup_state.get("winners_cup", {})
    if not cup.get("active"):
        return {}

    results: Dict[str, str] = {}
    for teams in cup.get("groups", {}).values():
        for team_name in teams:
            results[team_name] = "小组赛中"

    group_phase_complete = all(
        event_key in cup.get("results", {})
        for event_key in [f"winners_cup_group_{index}" for index in range(1, 7)]
    )
    if group_phase_complete:
        standings = _winners_cup_group_standings_from_snapshot(snapshot)
        for group_names in standings.values():
            if len(group_names) >= 3:
                results[group_names[2]] = "小组第三"
            if len(group_names) >= 4:
                results[group_names[3]] = "小组第四"
            for team_name in group_names[:2]:
                results[team_name] = "晋级八强"

    knockout_pairs = cup.get("knockout_pairs", {})
    for pair in knockout_pairs.get("winners_cup_quarterfinal_leg_1", []):
        results[pair["home"]] = "八强"
        results[pair["away"]] = "八强"
    for pair in knockout_pairs.get("winners_cup_semifinal_leg_1", []):
        results[pair["home"]] = "四强"
        results[pair["away"]] = "四强"

    final_pairs = knockout_pairs.get("winners_cup_final_leg_1", [])
    champion = cup.get("champion")
    if final_pairs:
        finalists = {final_pairs[0]["home"], final_pairs[0]["away"]}
        for team_name in finalists:
            results[team_name] = "决赛中"
        if champion in finalists:
            runner_up = next(team_name for team_name in finalists if team_name != champion)
            results[champion] = "冠军"
            results[runner_up] = "亚军"
    return results


def _build_challenge_cup_team_results(snapshot: SaveSnapshot) -> Dict[str, str]:
    cup = snapshot.cup_state.get("challenge_cup", {})
    if not cup.get("active"):
        return {}

    results = {team_name: "三十二强" for team_name in cup.get("participants", [])}
    winners = cup.get("winners", {})
    for team_name in winners.get("challenge_cup_r32", []):
        results[team_name] = "十六强"
    for team_name in winners.get("challenge_cup_r16", []):
        results[team_name] = "八强"
    for team_name in winners.get("challenge_cup_quarterfinal", []):
        results[team_name] = "四强"

    finalists = winners.get("challenge_cup_semifinal", [])
    if len(finalists) == 2:
        for team_name in finalists:
            results[team_name] = "决赛中"

    champion = cup.get("champion")
    if champion and len(finalists) == 2:
        runner_up = finalists[0] if finalists[1] == champion else finalists[1]
        results[champion] = "冠军"
        results[runner_up] = "亚军"
    return results


def _build_super_cup_team_results(snapshot: SaveSnapshot) -> Dict[str, str]:
    cup = snapshot.cup_state.get("super_cup", {})
    if not cup.get("active"):
        return {}

    results = {team_name: "四强" for team_name in cup.get("participants", [])}
    finalists = cup.get("finalists", [])
    if len(finalists) == 2:
        for team_name in finalists:
            results[team_name] = "决赛中"

    champion = cup.get("champion")
    if champion and len(finalists) == 2:
        runner_up = finalists[0] if finalists[1] == champion else finalists[1]
        results[champion] = "冠军"
        results[runner_up] = "亚军"
    return results


def _winners_cup_group_standings_from_snapshot(snapshot: SaveSnapshot) -> Dict[str, List[str]]:
    cup = snapshot.cup_state.get("winners_cup", {})
    if not cup.get("active"):
        return {}

    team_lookup = {team.name: team for team in snapshot.premier_teams}
    standings: Dict[str, List[str]] = {}
    rng = _rng()
    for group_name, teams in cup.get("groups", {}).items():
        rows = {team_name: TableRow(team=team_lookup[team_name]) for team_name in teams}
        results: List[MatchResult] = []
        for event_key, result_list in cup.get("results", {}).items():
            if not event_key.startswith("winners_cup_group_"):
                continue
            for result_data in result_list:
                if result_data["home_team"] not in teams:
                    continue
                result = _deserialize_match_result(result_data, team_lookup, _cup_round_number(event_key), WINNERS_CUP)
                results.append(result)
                _apply_table_result(rows, result)
        ordered = _rank_table_rows(
            list(rows.values()),
            results,
            _build_ranking_playoff_resolutions(list(rows.values()), results, rng),
        )
        standings[group_name] = [row.team.name for row in ordered]
    return standings


def _player_history_key_from_row(row: dict) -> str:
    if row.get("is_real"):
        return f"real::{row['label']}"
    return row["player_id"]


def _player_history_key_for_player(player: Player) -> str:
    if player.is_real:
        return f"real::{player.label}"
    return player.player_id


def _prepare_offseason_ability_review(state: dict, config, rng: random.Random) -> None:
    current_pool = _deserialize_real_player_pool(state.get("real_player_pool", []))
    if not current_pool:
        return

    review_count = max(1, int(len(current_pool) * 0.4))
    selected_profiles = rng.sample(current_pool, review_count)
    review_items = []
    real_min = max(config.default_player_ability + 1, REAL_PLAYER_ABILITY_MIN, config.real_player_ability_min)
    real_max = min(99, REAL_PLAYER_ABILITY_MAX, config.real_player_ability_max)
    for profile in selected_profiles:
        new_ability = _roll_adjusted_ability(profile.ability, real_min, real_max, rng)
        review_items.append(
            {
                "name": profile.name,
                "position": profile.position,
                "old_ability": profile.ability,
                "new_ability": new_ability,
                "delta": new_ability - profile.ability,
            }
        )
    review_items.sort(key=lambda item: (item["position"], item["name"]))
    state["pending_ability_review"] = review_items


def _roll_adjusted_ability(current_ability: int, minimum: int, maximum: int, rng: random.Random) -> int:
    candidates = [
        value
        for value in range(max(minimum, current_ability - 10), min(maximum, current_ability + 10) + 1)
        if value != current_ability
    ]
    if not candidates:
        return current_ability
    return rng.choice(candidates)


def _extract_assigned_real_players(state: dict, real_player_pool: List[RealPlayerProfile]) -> Dict[str, List[Player]]:
    profile_by_name = {profile.name: profile for profile in real_player_pool}
    assignments: Dict[str, List[Player]] = {}
    for team_data in [*state.get("premier_teams", []), *state.get("second_teams", [])]:
        team_name = team_data["name"]
        real_players: List[Player] = []
        for player_data in team_data.get("roster", []):
            if not player_data.get("is_real"):
                continue
            player = _deserialize_player(player_data)
            profile = profile_by_name.get(player.name or "")
            if profile is not None:
                player = Player(
                    player_id=real_player_id(player.name or profile.name),
                    name=player.name,
                    position=player.position,
                    ability=profile.ability,
                    is_real=True,
                    slot_number=player.slot_number,
                    initial_market_value=profile.initial_market_value if profile.initial_market_value is not None else player.initial_market_value,
                )
            real_players.append(player)
        assignments[team_name] = real_players
    return assignments


def _serialize_player_registry(teams: List[Team]) -> List[dict]:
    registry_items: List[dict] = []
    for team in teams:
        for player in team.roster:
            if not player.is_real:
                continue
            item = _serialize_player(player)
            item["team_name"] = team.name
            item["division"] = team.division
            registry_items.append(item)
    return registry_items


def _merge_player_registry(existing_registry_data: List[dict], teams: List[Team]) -> List[dict]:
    registry_by_id = {
        item["player_id"]: dict(item)
        for item in existing_registry_data
    }
    for item in _serialize_player_registry(teams):
        registry_by_id[item["player_id"]] = item
    return sorted(registry_by_id.values(), key=lambda item: item["player_id"])


def _save_dir(save_name: str) -> Path:
    return save_root() / normalize_save_name(save_name)


def _open_repo(save_name: str, *, create: bool = False) -> SaveRepository:
    if create:
        return SaveRepository.create(_save_dir(save_name), save_name)
    return SaveRepository.open(_save_dir(save_name), save_name)


# 数据安全（用户确认 #2）：写事务改“线程本地句柄 + 按存档名进程内锁”，
# 消除单例 _ACTIVE_REPO 被跨线程/跨存档覆盖、以及读线程误用写连接的隐患。
_SAVE_LOCKS: Dict[str, threading.RLock] = {}
_SAVE_LOCKS_GUARD = threading.Lock()
_thread_state = threading.local()


def _lock_for_save(save_name: str) -> "threading.RLock":
    with _SAVE_LOCKS_GUARD:
        lock = _SAVE_LOCKS.get(save_name)
        if lock is None:
            lock = threading.RLock()
            _SAVE_LOCKS[save_name] = lock
        return lock


def _active_repo() -> Optional[SaveRepository]:
    return getattr(_thread_state, "repo", None)


@contextmanager
def _state_transaction(save_name: str, *, create: bool = False):
    """存档读-改-写事务。

    ``BEGIN IMMEDIATE`` 从物化开始就持有写锁，直到 COMMIT：并发写入要么
    串行成功（受 busy_timeout 约束），要么清晰报错，不会静默覆盖。
    事务内任何异常（包括 COMMIT 本身失败）都会整体回滚，不产生半提交状态，
    并保证连接关闭、事务句柄清理。
    """
    with _lock_for_save(save_name):
        repo = _open_repo(save_name, create=create)
        try:
            repo.begin()
            _thread_state.repo = repo
            try:
                yield repo
                repo.commit()
            except BaseException:
                repo.rollback()
                raise
        finally:
            if _active_repo() is repo:
                _thread_state.repo = None
            repo.close()


def _load_state_json(save_name: str) -> dict:
    state = _load_state_json_if_exists(save_name)
    if state is None:
        raise FileNotFoundError(f"未找到存档 '{save_name}' 的存档数据库，请先初始化赛季。")
    return state


def _load_state_json_if_exists(save_name: str) -> Optional[dict]:
    active = _active_repo()
    if active is not None:
        if active.save_name != save_name:
            raise RuntimeError(f"存档事务指向 '{active.save_name}'，不能读取 '{save_name}'。")
        state = active.load_state()
    else:
        # 读路径使用独立只读连接，避免与写事务共享线程绑定的连接。
        repo = SaveRepository.open_readonly(_save_dir(save_name), save_name)
        try:
            state = repo.load_state()
        finally:
            repo.close()
    if state is not None:
        _normalize_rosters_and_registry(state)
    return state


def _write_state_json(save_name: str, state: dict) -> None:
    _normalize_rosters_and_registry(state)
    active = _active_repo()
    if active is not None:
        if active.save_name != save_name:
            raise RuntimeError(f"存档事务指向 '{active.save_name}'，不能写入 '{save_name}'。")
        active.persist_state(state)
        return
    repo = _open_repo(save_name)
    try:
        repo.begin()
        repo.persist_state(state)
        repo.commit()
    except BaseException:
        repo.rollback()
        raise
    finally:
        repo.close()


def _serialize_team(team: Team) -> dict:
    return {
        "name": team.name,
        "division": team.division,
        "roster": [_serialize_player(player) for player in team.roster],
    }


def _deserialize_team(data: dict) -> Team:
    return Team(
        name=data["name"],
        division=data.get("division", PREMIER_DIVISION),
        roster=tuple(_deserialize_player(player_data) for player_data in data["roster"]),
    )


def _serialize_player(player: Player) -> dict:
    return {
        "player_id": player.player_id,
        "name": player.name,
        "position": player.position,
        "ability": player.ability,
        "is_real": player.is_real,
        "slot_number": player.slot_number,
        "initial_market_value": player.initial_market_value,
    }


def _serialize_real_player_pool(real_player_pool: List[RealPlayerProfile]) -> List[dict]:
    return [
        {
            "name": profile.name,
            "position": profile.position,
            "ability": profile.ability,
            "initial_market_value": profile.initial_market_value,
        }
        for profile in real_player_pool
    ]


def _deserialize_real_player_pool(data: List[dict]) -> List[RealPlayerProfile]:
    return [
        RealPlayerProfile(
            name=item["name"],
            position=item["position"],
            ability=int(item["ability"]),
            initial_market_value=float(item["initial_market_value"]) if item.get("initial_market_value") is not None else None,
        )
        for item in data
    ]


def _deserialize_player(data: dict) -> Player:
    name = data.get("name")
    is_real = bool(data["is_real"]) and bool(_clean_player_name(name))
    return Player(
        player_id=real_player_id(str(name)) if is_real else data["player_id"],
        name=name if is_real else None,
        position=data["position"],
        ability=int(data["ability"]),
        is_real=is_real,
        slot_number=int(data["slot_number"]),
        initial_market_value=float(data["initial_market_value"]) if data.get("initial_market_value") is not None else None,
    )


def _serialize_week(week: WeekScheduleEntry) -> dict:
    return {
        "week_number": week.week_number,
        "label": week.label,
        "kind": week.kind,
        "premier_round_numbers": list(week.premier_round_numbers),
        "second_round_numbers": list(week.second_round_numbers),
        "cup_events": list(week.cup_events),
        "promotion_playoff_stage": week.promotion_playoff_stage,
    }


def _deserialize_week(data: dict) -> WeekScheduleEntry:
    return WeekScheduleEntry(
        week_number=int(data["week_number"]),
        label=_normalize_week_label(data["label"]),
        kind=data["kind"],
        premier_round_numbers=tuple(int(number) for number in data.get("premier_round_numbers", data.get("round_numbers", []))),
        second_round_numbers=tuple(int(number) for number in data.get("second_round_numbers", data.get("round_numbers", []))),
        cup_events=tuple(data.get("cup_events", [])),
        promotion_playoff_stage=data.get("promotion_playoff_stage"),
    )


def _normalize_player_label(label: str) -> str:
    if label.startswith("Default "):
        return label.replace("Default ", "默认 ", 1)
    return label


def _normalize_week_label(label: str) -> str:
    return {
        "Long Break": "长休赛期",
        "Short Break": "短休赛期",
        "Open Week": "无比赛周",
        "Double Match Week": "双赛周",
        "League Week": "联赛周",
        "升级附加赛半决赛首回合": "升级附加赛半决赛首回合",
        "升级附加赛半决赛次回合": "升级附加赛半决赛次回合",
        "升级附加赛决赛首回合": "升级附加赛决赛首回合",
        "升级附加赛决赛次回合": "升级附加赛决赛次回合",
    }.get(label, label)


def _serialize_matchday(matchday: MatchdayReport) -> dict:
    return {
        "round_number": matchday.round_number,
        "competition": matchday.competition,
        "results": [_serialize_match_result(result) for result in matchday.results],
    }


def _deserialize_matchday(data: dict, team_lookup: Dict[str, Team]) -> MatchdayReport:
    return MatchdayReport(
        round_number=int(data["round_number"]),
        competition=data.get("competition", PREMIER_DIVISION),
        results=[
            _deserialize_match_result(result_data, team_lookup, int(data["round_number"]), data.get("competition", PREMIER_DIVISION))
            for result_data in data.get("results", [])
        ],
    )


def _serialize_match_result(result: MatchResult) -> dict:
    return {
        "home_team": result.home_team.name,
        "away_team": result.away_team.name,
        "home_goals": result.home_goals,
        "away_goals": result.away_goals,
        "key_events": list(result.key_events),
        "competition": result.fixture.competition,
        "player_stats": {
            player_id: _serialize_player_stat_delta(delta)
            for player_id, delta in result.player_stats.items()
        },
    }


def _deserialize_match_result(
    data: dict,
    team_lookup: Dict[str, Team],
    round_number: int,
    competition: str,
) -> MatchResult:
    fixture = Fixture(
        round_number=round_number,
        home_team=team_lookup[data["home_team"]],
        away_team=team_lookup[data["away_team"]],
        competition=data.get("competition", competition),
    )
    return MatchResult(
        fixture=fixture,
        home_goals=int(data["home_goals"]),
        away_goals=int(data["away_goals"]),
        key_events=list(data.get("key_events", [])),
        player_stats={
            player_id: _deserialize_player_stat_delta(delta)
            for player_id, delta in data.get("player_stats", {}).items()
        },
    )


def _serialize_player_stat_delta(delta: PlayerStatDelta) -> dict:
    return {
        "goals": delta.goals,
        "assists": delta.assists,
        "chances_created": delta.chances_created,
        "successful_defenses": delta.successful_defenses,
        "successful_saves": delta.successful_saves,
        "clean_sheets": delta.clean_sheets,
    }


def _deserialize_player_stat_delta(data: dict) -> PlayerStatDelta:
    return PlayerStatDelta(
        goals=int(data.get("goals", 0)),
        assists=int(data.get("assists", 0)),
        chances_created=int(data.get("chances_created", 0)),
        successful_defenses=int(data.get("successful_defenses", 0)),
        successful_saves=int(data.get("successful_saves", 0)),
        clean_sheets=int(data.get("clean_sheets", 0)),
    )


def _serialize_player_season_stats(row: PlayerSeasonStats) -> dict:
    return {
        "season_number": row.season_number,
        "player_id": row.player.player_id,
        "label": row.player.label,
        "position": row.player.position,
        "is_real": row.player.is_real,
        "team_name": row.team_name,
        "season_team_names": [row.team_name],
        "team_display_name": row.team_name,
        "appearances": row.appearances,
        "goals": row.goals,
        "assists": row.assists,
        "chances_created": row.chances_created,
        "successful_defenses": row.successful_defenses,
        "successful_saves": row.successful_saves,
        "clean_sheets": row.clean_sheets,
        "season_rating": row.season_rating,
        "market_value": row.market_value,
    }


def _serialize_team_season_stats(row: TeamSeasonStats) -> dict:
    return {
        "season_number": row.season_number,
        "team_name": row.team_name,
        "division": row.division,
        "played": row.played,
        "wins": row.wins,
        "draws": row.draws,
        "losses": row.losses,
        "goals_for": row.goals_for,
        "goals_against": row.goals_against,
        "points": row.points,
        "goal_diff": row.goal_diff,
        "goals": row.goals,
        "assists": row.assists,
        "chances_created": row.chances_created,
        "successful_defenses": row.successful_defenses,
        "successful_saves": row.successful_saves,
        "clean_sheets": row.clean_sheets,
        "total_market_value": row.total_market_value,
    }
