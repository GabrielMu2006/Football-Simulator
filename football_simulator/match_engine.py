import random
from typing import Dict, Iterable, List, Optional, Sequence, Set

from football_simulator.models import (
    Fixture,
    MatchResult,
    Player,
    PlayerStatDelta,
    Team,
)


EVENT_MINUTES = [4, 10, 16, 23, 29, 36, 43, 51, 59, 67, 75, 83, 89]
ROLE_WEIGHTS = {
    "creator": {"GK": 0.0, "DF": 0.55, "MF": 1.35, "FW": 1.00},
    "scorer": {"GK": 0.0, "DF": 0.18, "MF": 0.72, "FW": 1.60},
    "assist": {"GK": 0.0, "DF": 0.45, "MF": 1.30, "FW": 1.00},
    "defense": {"GK": 0.0, "DF": 1.45, "MF": 0.95, "FW": 0.18},
}


def simulate_match(fixture: Fixture, rng: random.Random) -> MatchResult:
    home_goals = 0
    away_goals = 0
    key_events: list[str] = []
    player_stats: Dict[str, PlayerStatDelta] = {}

    home_pressure = _team_pressure_score(fixture.home_team, fixture.away_team, is_home=True)
    away_pressure = _team_pressure_score(fixture.away_team, fixture.home_team, is_home=False)

    match_tempo = rng.uniform(0.94, 1.18)
    match_chaos = rng.uniform(-0.04, 0.08)

    for minute in EVENT_MINUTES:
        swing = rng.uniform(-0.22, 0.22)
        home_window = home_pressure * match_tempo + swing + match_chaos
        away_window = away_pressure * match_tempo - swing + match_chaos

        if rng.random() < max(0.06, min(0.62, home_window)):
            event_text, goal_delta = _resolve_attack(
                attacking_team=fixture.home_team,
                defending_team=fixture.away_team,
                minute=minute,
                rng=rng,
                player_stats=player_stats,
                is_home=True,
                home_goals=home_goals,
                away_goals=away_goals,
            )
            home_goals += goal_delta
            if event_text:
                key_events.append(event_text)

        if rng.random() < max(0.06, min(0.62, away_window)):
            event_text, goal_delta = _resolve_attack(
                attacking_team=fixture.away_team,
                defending_team=fixture.home_team,
                minute=minute,
                rng=rng,
                player_stats=player_stats,
                is_home=False,
                home_goals=home_goals,
                away_goals=away_goals,
            )
            away_goals += goal_delta
            if event_text:
                key_events.append(event_text)

    if not key_events:
        key_events.append("比赛比较胶着，双方都没创造出太多绝对机会。")
    else:
        key_events = key_events[:5]

    if away_goals == 0:
        _record_stat(player_stats, fixture.home_team.goalkeeper, "clean_sheets")
    if home_goals == 0:
        _record_stat(player_stats, fixture.away_team.goalkeeper, "clean_sheets")

    return MatchResult(
        fixture=fixture,
        home_goals=home_goals,
        away_goals=away_goals,
        key_events=key_events,
        player_stats=player_stats,
    )


def _team_pressure_score(attacking_team: Team, defending_team: Team, is_home: bool) -> float:
    mismatch = (
        attacking_team.attack
        + attacking_team.midfield * 0.55
        + attacking_team.star_power * 1.20
        - defending_team.defense
        - defending_team.star_power * 0.55
    )
    score = (
        0.08
        + attacking_team.attack * 0.0030
        + attacking_team.midfield * 0.0024
        - defending_team.defense * 0.0018
        + attacking_team.mentality * 0.0013
        + mismatch * 0.0011
    )
    if is_home:
        score += 0.030
    return max(0.08, min(0.46, score))


def _is_goal(
    attacking_team: Team,
    defending_team: Team,
    shooter: Player,
    rng: random.Random,
    is_home: bool,
) -> bool:
    conversion = (
        0.10
        + attacking_team.attack * 0.0020
        + attacking_team.mentality * 0.0008
        + shooter.ability * 0.0015
        + attacking_team.star_power * 0.0010
        - defending_team.defense * 0.0012
        - defending_team.goalkeeper.ability * 0.0008
    )
    if shooter.is_real:
        conversion += 0.020
    if is_home:
        conversion += 0.010
    conversion += rng.uniform(-0.04, 0.05)
    return rng.random() < max(0.07, min(0.42, conversion))


def _resolve_attack(
    attacking_team: Team,
    defending_team: Team,
    minute: int,
    rng: random.Random,
    player_stats: Dict[str, PlayerStatDelta],
    is_home: bool,
    home_goals: int,
    away_goals: int,
) -> tuple[Optional[str], int]:
    creators = _pick_players(
        team=attacking_team,
        rng=rng,
        role="creator",
        count=_shared_event_count(rng, second_probability=0.48, third_probability=0.16),
    )
    for creator in creators:
        _record_stat(player_stats, creator, "chances_created")

    shooter = _pick_players(team=attacking_team, rng=rng, role="scorer", count=1)[0]

    if _is_goal(attacking_team, defending_team, shooter, rng, is_home=is_home):
        scorer = shooter
        _record_stat(player_stats, scorer, "goals")

        assist = _pick_assist(attacking_team, creators, scorer, rng)
        if assist is not None:
            _record_stat(player_stats, assist, "assists")

        if attacking_team == defending_team:
            raise ValueError("进攻方和防守方不能是同一支球队。")

        if is_home:
            return (
                _goal_text(
                    minute=minute,
                    scoring_team=attacking_team,
                    home_team=attacking_team,
                    away_team=defending_team,
                    home_goals=home_goals + 1,
                    away_goals=away_goals,
                    scorer=scorer,
                    assist=assist,
                ),
                1,
            )

        return (
            _goal_text(
                minute=minute,
                scoring_team=attacking_team,
                home_team=defending_team,
                away_team=attacking_team,
                home_goals=home_goals,
                away_goals=away_goals + 1,
                scorer=scorer,
                assist=assist,
            ),
            1,
        )

    if _is_saved(attacking_team, defending_team, shooter, rng):
        goalkeeper = defending_team.goalkeeper
        _record_stat(player_stats, goalkeeper, "successful_saves")
        return (
            f"{minute}' {goalkeeper.label} 为 {defending_team.name} 扑出了 {shooter.label} 的射门。",
            0,
        )

    defenders = _pick_players(
        team=defending_team,
        rng=rng,
        role="defense",
        count=_shared_event_count(rng, second_probability=0.52, third_probability=0.18),
    )
    for defender in defenders:
        _record_stat(player_stats, defender, "successful_defenses")

    return (
        f"{minute}' {_join_player_labels(defenders)} 成功化解了 {attacking_team.name} 的这次进攻。",
        0,
    )


def _is_saved(attacking_team: Team, defending_team: Team, shooter: Player, rng: random.Random) -> bool:
    save_probability = (
        0.18
        + defending_team.goalkeeper.ability * 0.0032
        + defending_team.defense * 0.0011
        - attacking_team.attack * 0.0010
        - shooter.ability * 0.0012
    )
    if shooter.is_real:
        save_probability -= 0.025
    save_probability += rng.uniform(-0.05, 0.05)
    return rng.random() < max(0.10, min(0.54, save_probability))


def _pick_assist(
    attacking_team: Team,
    creators: Sequence[Player],
    scorer: Player,
    rng: random.Random,
) -> Optional[Player]:
    if rng.random() >= 0.74:
        return None

    preferred_creators = [player for player in creators if player.player_id != scorer.player_id]
    if preferred_creators and rng.random() < 0.78:
        return _pick_from_pool(preferred_creators, rng, "assist")

    return _pick_optional_player(
        team=attacking_team,
        rng=rng,
        role="assist",
        exclude_ids={scorer.player_id},
    )


def _pick_players(
    team: Team,
    rng: random.Random,
    role: str,
    count: int,
    exclude_ids: Optional[Set[str]] = None,
) -> List[Player]:
    candidates = [
        player for player in team.roster if not exclude_ids or player.player_id not in exclude_ids
    ]
    selected: List[Player] = []
    while candidates and len(selected) < count:
        choice = _pick_from_pool(candidates, rng, role)
        selected.append(choice)
        candidates = [player for player in candidates if player.player_id != choice.player_id]
    return selected


def _pick_optional_player(
    team: Team,
    rng: random.Random,
    role: str,
    exclude_ids: Optional[Set[str]] = None,
) -> Optional[Player]:
    selected = _pick_players(team=team, rng=rng, role=role, count=1, exclude_ids=exclude_ids)
    return selected[0] if selected else None


def _pick_from_pool(players: Sequence[Player], rng: random.Random, role: str) -> Player:
    weighted_players = [
        (player, _player_role_weight(player, role))
        for player in players
        if _player_role_weight(player, role) > 0
    ]
    if not weighted_players:
        raise ValueError(f"角色 {role} 没有可用球员。")
    weights = [weight for _, weight in weighted_players]
    candidates = [player for player, _ in weighted_players]
    return rng.choices(candidates, weights=weights, k=1)[0]


def _player_role_weight(player: Player, role: str) -> float:
    role_weight = ROLE_WEIGHTS[role][player.position]
    ability_weight = max(1.0, player.ability) ** 1.14
    real_player_boost = 1.28 if player.is_real else 1.0
    if role == "scorer" and player.is_real:
        real_player_boost += 0.18
    if role == "creator" and player.is_real:
        real_player_boost += 0.10
    return ability_weight * role_weight * real_player_boost


def _record_stat(
    player_stats: Dict[str, PlayerStatDelta],
    player: Player,
    stat_name: str,
    amount: int = 1,
) -> None:
    delta = player_stats.setdefault(player.player_id, PlayerStatDelta())
    delta.add(stat_name, amount)


def _shared_event_count(
    rng: random.Random,
    second_probability: float,
    third_probability: float,
) -> int:
    count = 1
    if rng.random() < second_probability:
        count += 1
    if rng.random() < third_probability:
        count += 1
    return count


def _join_player_labels(players: Iterable[Player]) -> str:
    labels = [player.label for player in players]
    if not labels:
        return "防线"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]}和{labels[1]}"
    return f"{'、'.join(labels[:-1])}和{labels[-1]}"


def _goal_text(
    minute: int,
    scoring_team: Team,
    home_team: Team,
    away_team: Team,
    home_goals: int,
    away_goals: int,
    scorer: Player,
    assist: Optional[Player],
) -> str:
    assist_text = f"，助攻 {assist.label}" if assist is not None else ""
    return (
        f"{minute}' {scoring_team.name} 进球，进球者 {scorer.label}{assist_text}。"
        f"当前比分 {home_team.name} {home_goals}-{away_goals} {away_team.name}。"
    )
