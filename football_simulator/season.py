import random
from typing import Optional

from football_simulator.data import create_teams_from_save, load_save_config
from football_simulator.match_engine import simulate_match
from football_simulator.models import MatchdayReport, PlayerSeasonStats, SeasonReport, TableRow
from football_simulator.schedule import build_league_schedule


def simulate_league_season(save_name: str = "default", seed: Optional[int] = None) -> SeasonReport:
    rng = random.Random(seed)
    save_config = load_save_config(save_name)
    teams = create_teams_from_save(save_config, rng)
    schedule = build_league_schedule(teams)
    table_map = {team.name: TableRow(team=team) for team in teams}
    player_stats_map = {
        player.player_id: PlayerSeasonStats(player=player, team_name=team.name)
        for team in teams
        for player in team.roster
    }
    matchdays: list[MatchdayReport] = []

    for fixtures in schedule:
        matchday = MatchdayReport(round_number=fixtures[0].round_number)
        for fixture in fixtures:
            result = simulate_match(fixture, rng)
            matchday.results.append(result)

            home_row = table_map[result.home_team.name]
            away_row = table_map[result.away_team.name]
            home_row.record_match(result.home_goals, result.away_goals)
            away_row.record_match(result.away_goals, result.home_goals)

            for player_id, delta in result.player_stats.items():
                player_stats_map[player_id].apply_delta(delta)

        matchdays.append(matchday)

    sorted_table = sorted(
        table_map.values(),
        key=lambda row: (
            row.points,
            row.goals_for - row.goals_against,
            row.goals_for,
            row.team.rating,
        ),
        reverse=True,
    )
    best_attack = max(sorted_table, key=lambda row: row.goals_for)
    best_defense = min(sorted_table, key=lambda row: row.goals_against)

    return SeasonReport(
        save_name=save_name,
        matchdays=matchdays,
        table=sorted_table,
        best_attack=best_attack,
        best_defense=best_defense,
        teams=teams,
        player_stats=sorted(
            player_stats_map.values(),
            key=lambda row: (
                row.goals,
                row.assists,
                row.chances_created,
                row.successful_defenses,
                row.successful_saves,
                row.player.ability,
            ),
            reverse=True,
        ),
    )
