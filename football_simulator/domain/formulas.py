"""球员评分与身价公式（自 state.py 原样抽取，行为冻结）。

锁定来源：阶段 0 测试 `tests/test_settlement_awards_freeze.py` 中的
受控输入断言与三赛季基线指纹。
"""

from __future__ import annotations

from football_simulator.models import (
    POSITION_DEFENDER,
    POSITION_FORWARD,
    POSITION_GOALKEEPER,
    POSITION_MIDFIELDER,
    Player,
    PlayerSeasonStats,
)


def calculate_player_rating(player_stats: PlayerSeasonStats, matches_played: int) -> float:
    if matches_played <= 0:
        return 0.0

    ability_bonus = max(0.0, (player_stats.player.ability - 50) / 10)
    goals_per_match = player_stats.goals / matches_played
    assists_per_match = player_stats.assists / matches_played
    chances_per_match = player_stats.chances_created / matches_played
    defenses_per_match = player_stats.successful_defenses / matches_played
    saves_per_match = player_stats.successful_saves / matches_played
    clean_sheet_rate = player_stats.clean_sheets / matches_played

    if player_stats.player.position == POSITION_FORWARD:
        rating = (
            4.80
            + 4.60 * goals_per_match
            + 2.40 * assists_per_match
            + 0.32 * chances_per_match
            + 0.12 * defenses_per_match
            + 0.10 * ability_bonus
        )
    elif player_stats.player.position == POSITION_MIDFIELDER:
        rating = (
            4.95
            + 2.20 * goals_per_match
            + 2.80 * assists_per_match
            + 0.42 * chances_per_match
            + 0.28 * defenses_per_match
            + 0.14 * ability_bonus
        )
    elif player_stats.player.position == POSITION_DEFENDER:
        rating = (
            5.25
            + 1.10 * goals_per_match
            + 1.40 * assists_per_match
            + 0.20 * chances_per_match
            + 0.72 * defenses_per_match
            + 0.24 * ability_bonus
        )
    else:
        rating = 5.45 + 0.24 * saves_per_match + 2.10 * clean_sheet_rate + 0.30 * ability_bonus

    return round(max(0.0, min(10.0, rating)), 2)


def calculate_market_value(player: Player, season_rating: float) -> float:
    if not player.is_real:
        return 0.0

    performance_factor = 0.58 * season_rating + 0.42 * (player.ability / 10)
    if player.position == POSITION_GOALKEEPER:
        position_factor = 1.03
    else:
        position_factor = {
            POSITION_FORWARD: 1.08,
            POSITION_MIDFIELDER: 1.05,
            POSITION_DEFENDER: 1.04,
        }.get(player.position, 1.0)

    base_value = (performance_factor ** 2.05) * position_factor
    star_bonus = (max(0, player.ability - 74) ** 1.45) * 0.55
    rating_bonus = (max(0.0, season_rating - 7.4) ** 2) * 9.0
    return round(max(8.0, base_value + star_bonus + rating_bonus), 2)
