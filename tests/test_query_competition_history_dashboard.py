"""赛事 / 历史 / 首页查询测试（阶段 2，Agent C2）。

共享夹具（单个临时存档根目录 + 固定随机源，setUp 重新指向、atexit 清理）：
- 主存档 ``query_comp``：第 1 赛季完整跑完（有归档），第 2 赛季推进 33 周
  （优胜者杯小组赛与 1/4 决赛两回合已赛，联赛进行中）；
- 待办存档 ``query_comp_pending``：第 1 赛季推进 25 周（冬窗产生转会待办）；
- 杯赛存档 ``query_comp_cups``：跑完第 1、2 赛季后在第 3 赛季推进 29 周
  （超级杯决赛已于第 28 周决出冠军）。
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Tuple

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.domain import formulas
from football_simulator.models import Player, PlayerSeasonStats
from football_simulator.queries import base, competition_queries, dashboard_queries, history_queries, team_queries

from tests.support import create_save, run_season, run_weeks, seeded_provider

MAIN_SAVE = "query_comp"
PENDING_SAVE = "query_comp_pending"
CUP_SAVE = "query_comp_cups"
MAIN_WEEKS = 33
PENDING_WEEKS = 25
CUP_WEEKS = 29

_SHARED: Dict[str, Path] = {}


def _teardown_shared() -> None:
    root = _SHARED.get("root")
    sim_state.set_rng_provider(None)
    sim_runtime.set_save_root_override(None)
    if root is not None:
        shutil.rmtree(str(root), ignore_errors=True)


def _shared_root() -> Path:
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_query_comp_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())

        create_save(MAIN_SAVE)
        run_season(MAIN_SAVE)
        create_save(MAIN_SAVE)
        run_weeks(MAIN_SAVE, MAIN_WEEKS)

        create_save(PENDING_SAVE)
        run_weeks(PENDING_SAVE, PENDING_WEEKS)

        create_save(CUP_SAVE)
        run_season(CUP_SAVE)
        create_save(CUP_SAVE)
        run_season(CUP_SAVE)
        create_save(CUP_SAVE)
        run_weeks(CUP_SAVE, CUP_WEEKS)

        _SHARED["root"] = root
        atexit.register(_teardown_shared)
    else:
        sim_runtime.set_save_root_override(_SHARED["root"])
        sim_state.set_rng_provider(seeded_provider())
    return _SHARED["root"]


class QueryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        _shared_root()

    def _archive(self, conn, season_number: int) -> dict:
        row = conn.execute(
            """
            SELECT sa.archive_json FROM season_archives AS sa
            JOIN seasons AS s ON s.season_id = sa.season_id
            WHERE s.season_number = ?
            """,
            (season_number,),
        ).fetchone()
        self.assertIsNotNone(row, f"第 {season_number} 赛季应有归档")
        return json.loads(row["archive_json"])

    def _runtime(self, conn, season_number: int) -> dict:
        row = conn.execute(
            """
            SELECT sr.data_json FROM season_runtime AS sr
            JOIN seasons AS s ON s.season_id = sr.season_id
            WHERE s.season_number = ?
            """,
            (season_number,),
        ).fetchone()
        if row is None:
            return {}
        return json.loads(row["data_json"])

    def _stable_player_map(self, conn) -> Dict[str, Tuple[str, str, int]]:
        """player_match_stats 中的 player_id -> (稳定 ID, 位置, 能力)。"""
        mapping: Dict[str, Tuple[str, str, int]] = {}
        for row in conn.execute(
            "SELECT player_id, name, position, ability, is_real, team_id, slot_number FROM players"
        ):
            display = row["name"] or f"默认 {row['position']} {int(row['slot_number'])}"
            if row["is_real"]:
                stable = row["player_id"]
            else:
                stable = base.default_player_id(int(row["team_id"]), int(row["slot_number"]))
            mapping[row["player_id"]] = (stable, row["position"], int(row["ability"]))
        return mapping


class OverviewTests(QueryTestBase):
    def test_current_season_overview_statuses(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            overviews = competition_queries.list_competitions(conn, 2)
            self.assertEqual(
                [o.competition.competition_id for o in overviews],
                list(base.ALL_COMPETITIONS),
            )
            by_name = {o.competition.competition_id: o for o in overviews}

            season_id = conn.execute("SELECT season_id FROM seasons WHERE season_number = 2").fetchone()["season_id"]
            for competition in ("一级联赛", "次级联赛"):
                overview = by_name[competition]
                self.assertEqual(overview.status, "进行中")
                self.assertEqual(overview.total_matches, 380)
                self.assertIsNone(overview.champion)
                completed = conn.execute(
                    "SELECT COUNT(*) AS total FROM matches WHERE season_id = ? AND competition = ? AND status = 'completed'",
                    (season_id, competition),
                ).fetchone()["total"]
                self.assertEqual(overview.completed_matches, completed)
                self.assertGreater(completed, 0)

            self.assertEqual(by_name["优胜者杯"].status, "进行中")
            self.assertIsNone(by_name["优胜者杯"].champion)
            self.assertIsNone(by_name["优胜者杯"].total_matches)
            self.assertEqual(by_name["挑战杯"].status, "进行中")
            self.assertEqual(by_name["超级杯"].status, "未举办")
            self.assertEqual(by_name["升级附加赛"].status, "未开始")
            self.assertIsNone(by_name["升级附加赛"].champion)

    def test_completed_season_overview(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            overviews = {o.competition.competition_id: o for o in competition_queries.list_competitions(conn, 1)}
            archive = self._archive(conn, 1)
            self.assertEqual(overviews["一级联赛"].status, "已结束")
            self.assertEqual(overviews["一级联赛"].completed_matches, 380)
            self.assertEqual(overviews["一级联赛"].total_matches, 380)
            self.assertEqual(overviews["一级联赛"].champion, archive["premier_order"][0])
            self.assertEqual(overviews["次级联赛"].status, "已结束")
            self.assertEqual(overviews["次级联赛"].champion, archive["second_order"][0])
            for cup in ("优胜者杯", "挑战杯", "超级杯"):
                self.assertEqual(overviews[cup].status, "未举办")
                self.assertIsNone(overviews[cup].champion)
            self.assertEqual(overviews["升级附加赛"].status, "已结束")
            self.assertEqual(
                overviews["升级附加赛"].champion,
                archive["last_transition"]["playoff_winner"],
            )
            self.assertEqual(overviews["升级附加赛"].completed_matches, 6)

    def test_unknown_season_raises(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            with self.assertRaises(KeyError):
                competition_queries.list_competitions(conn, 99)
            with self.assertRaises(KeyError):
                competition_queries.get_competition_profile(conn, "一级联赛", 99)

    def test_unknown_competition_raises(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            with self.assertRaises(KeyError):
                competition_queries.get_competition_profile(conn, "不存在的杯赛", 2)


class StandingsConsistencyTests(QueryTestBase):
    def test_league_standings_match_team_queries(self) -> None:
        for season_number in (1, 2):
            with self.subTest(season_number=season_number):
                with base.open_read_connection(MAIN_SAVE) as conn:
                    profile = competition_queries.get_competition_profile(conn, "一级联赛", season_number)
                    standings_rows = profile.standings
                    self.assertIsNotNone(standings_rows)
                    self.assertEqual(len(standings_rows), 20)
                    directory = {
                        row.team.display_name: row
                        for row in team_queries.list_teams(conn, season_number, division="一级联赛")
                    }
                    for row in (standings_rows[0], standings_rows[7]):
                        target = directory[row.team_name]
                        self.assertEqual(row.rank, target.rank)
                        self.assertEqual(row.points, target.points)
                        self.assertEqual(row.played, target.played)
                        self.assertEqual(row.wins, target.wins)
                        self.assertEqual(row.draws, target.draws)
                        self.assertEqual(row.losses, target.losses)
                        self.assertEqual(row.goals_for, target.goals_for)
                        self.assertEqual(row.goals_against, target.goals_against)
                        self.assertEqual(row.team_id, target.team.team_id)
                        self.assertEqual(row.points, 3 * row.wins + row.draws)

    def test_second_league_standings_match_team_queries(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            profile = competition_queries.get_competition_profile(conn, "次级联赛", 2)
            directory = {
                row.team.display_name: row
                for row in team_queries.list_teams(conn, 2, division="次级联赛")
            }
            for row in profile.standings[:2]:
                target = directory[row.team_name]
                self.assertEqual(row.rank, target.rank)
                self.assertEqual(row.points, target.points)


class CupStageTests(QueryTestBase):
    def test_winners_cup_structure_and_determinism(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            profile = competition_queries.get_competition_profile(conn, "优胜者杯", 2)
            rows = profile.stage_rows
            self.assertTrue(rows, "第 2 赛季优胜者杯应有 stage_rows")
            rounds = sorted({row.round_number for row in rows})
            self.assertEqual(rounds, [1, 2, 3, 4, 5, 6, 7, 8], "小组赛 6 轮 + 1/4 决赛两回合")
            self.assertEqual([row.round_number for row in rows], sorted(row.round_number for row in rows))
            self.assertEqual(
                [(row.round_number, row.match.match_id) for row in rows],
                sorted((row.round_number, row.match.match_id) for row in rows),
            )
            # 再次查询结果完全一致（确定性）。
            again = competition_queries.get_competition_profile(conn, "优胜者杯", 2)
            self.assertEqual(again.stage_rows, rows)

            group_rows = [row for row in rows if row.round_number <= 6]
            knockout_rows = [row for row in rows if row.round_number >= 7]
            self.assertTrue(all(row.advancing is None for row in group_rows))
            for row in group_rows:
                if row.match.home_goals > row.match.away_goals:
                    self.assertEqual(row.match_winner, row.match.home)
                elif row.match.home_goals < row.match.away_goals:
                    self.assertEqual(row.match_winner, row.match.away)
                else:
                    self.assertIsNone(row.match_winner)
            # 1/4 决赛两回合：签表已生成（半决赛在第 34 周后才抽签）。
            self.assertEqual(len(knockout_rows), 8)
            self.assertTrue(all(row.advancing is None for row in knockout_rows))

    def test_challenge_cup_advancing_from_winners(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            profile = competition_queries.get_competition_profile(conn, "挑战杯", 2)
            rows = profile.stage_rows
            rounds = sorted({row.round_number for row in rows})
            self.assertEqual(rounds, [1, 2, 3, 4], "第 33 周时挑战杯已赛至半决赛，决赛未到")
            counts = {1: 16, 2: 8, 3: 4, 4: 2}
            for round_number, expected in counts.items():
                actual = sum(1 for row in rows if row.round_number == round_number)
                self.assertEqual(actual, expected)
            # 挑战杯每轮晋级方直接记录在 cup_state.winners 中。
            for row in rows:
                self.assertIsNotNone(row.advancing)
            # 决赛未赛，冠军为空。
            self.assertIsNone(profile.champion)

    def test_super_cup_champion_in_season_three(self) -> None:
        with base.open_read_connection(CUP_SAVE) as conn:
            profile = competition_queries.get_competition_profile(conn, "超级杯", 3)
            # 半决赛 2 场（round 1）+ 决赛 1 场（round 2）。
            self.assertEqual(len(profile.stage_rows), 3)
            self.assertEqual(
                sorted(row.round_number for row in profile.stage_rows),
                [1, 1, 2],
            )
            self.assertTrue(all(row.advancing is not None for row in profile.stage_rows))
            runtime = self._runtime(conn, 3)
            expected = runtime["cup_state"]["super_cup"]["champion"]
            self.assertIsNotNone(expected)
            self.assertEqual(profile.champion, expected)
            self.assertEqual(profile.stage_rows[-1].advancing.display_name, expected)

            overviews = {o.competition.competition_id: o for o in competition_queries.list_competitions(conn, 3)}
            self.assertEqual(overviews["超级杯"].status, "已结束")
            self.assertEqual(overviews["超级杯"].champion, expected)
            self.assertEqual(overviews["优胜者杯"].status, "进行中")
            self.assertEqual(overviews["挑战杯"].status, "进行中")

    def test_cup_profile_matches_count(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            season_id = conn.execute("SELECT season_id FROM seasons WHERE season_number = 2").fetchone()["season_id"]
            profile = competition_queries.get_competition_profile(conn, "优胜者杯", 2)
            db_count = conn.execute(
                "SELECT COUNT(*) AS total FROM matches WHERE season_id = ? AND competition = '优胜者杯'",
                (season_id,),
            ).fetchone()["total"]
            self.assertEqual(len(profile.matches), db_count)
            self.assertEqual(len(profile.stage_rows), db_count)


class LeaderboardTests(QueryTestBase):
    def _manual_entries(self, conn, season_id: int, competition: str):
        """按契约口径从 player_match_stats 手工聚合（含被替换默认球员的收敛）。"""
        player_map = self._stable_player_map(conn)
        fallback_row = conn.execute(
            "SELECT ability FROM players WHERE is_real = 0 ORDER BY team_id, roster_index LIMIT 1"
        ).fetchone()
        fallback_ability = int(fallback_row["ability"]) if fallback_row else 0
        accumulated: Dict[str, Dict[str, object]] = {}
        for row in conn.execute(
            """
            SELECT pms.player_id AS player_id,
                   MIN(pms.team_id) AS team_id,
                   SUM(pms.appeared) AS matches_played,
                   SUM(pms.goals) AS goals,
                   SUM(pms.assists) AS assists,
                   SUM(pms.chances_created) AS chances_created,
                   SUM(pms.successful_defenses) AS successful_defenses,
                   SUM(pms.successful_saves) AS successful_saves,
                   SUM(pms.clean_sheets) AS clean_sheets
            FROM player_match_stats AS pms
            JOIN matches AS m ON m.match_id = pms.match_id
            WHERE m.season_id = ? AND m.competition = ? AND pms.appeared = 1
            GROUP BY pms.player_id
            """,
            (season_id, competition),
        ):
            raw_id = row["player_id"]
            known = player_map.get(raw_id)
            if known is not None:
                stable, position, ability = known
                is_real = True
            else:
                # 赛季中被真实球员替换掉的默认球员：按 ID 中的槽位 + 比赛当时
                # team_id 合成稳定身份（与查询层同语义）。
                base_id = raw_id[: -len("-default")] if raw_id.endswith("-default") else raw_id
                parts = base_id.rsplit("-", 2)
                if len(parts) != 3 or parts[1] not in {"gk", "df", "mf", "fw"} or not parts[2].isdigit():
                    continue
                position = parts[1].upper()
                ability = fallback_ability
                is_real = False
                stable = base.default_player_id(int(row["team_id"] or 0), int(parts[2]))
            bucket = accumulated.setdefault(
                stable,
                {
                    "position": position,
                    "ability": ability,
                    "is_real": is_real,
                    "matches": 0,
                    "goals": 0,
                    "assists": 0,
                    "chances_created": 0,
                    "successful_defenses": 0,
                    "successful_saves": 0,
                    "clean_sheets": 0,
                },
            )
            bucket["matches"] += int(row["matches_played"] or 0)
            bucket["goals"] += int(row["goals"] or 0)
            bucket["assists"] += int(row["assists"] or 0)
            bucket["chances_created"] += int(row["chances_created"] or 0)
            bucket["successful_defenses"] += int(row["successful_defenses"] or 0)
            bucket["successful_saves"] += int(row["successful_saves"] or 0)
            bucket["clean_sheets"] += int(row["clean_sheets"] or 0)

        entries = []
        for stable, bucket in accumulated.items():
            stats = PlayerSeasonStats(
                player=Player(
                    player_id=stable,
                    name=None,
                    position=bucket["position"],
                    ability=int(bucket["ability"]),
                    is_real=bool(bucket["is_real"]),
                    slot_number=0,
                ),
                team_name="",
            )
            stats.goals = int(bucket["goals"])
            stats.assists = int(bucket["assists"])
            stats.chances_created = int(bucket["chances_created"])
            stats.successful_defenses = int(bucket["successful_defenses"])
            stats.successful_saves = int(bucket["successful_saves"])
            stats.clean_sheets = int(bucket["clean_sheets"])
            rating = formulas.calculate_player_rating(stats, int(bucket["matches"]))
            entries.append(
                {
                    "stable": stable,
                    "matches": int(bucket["matches"]),
                    "goals": stats.goals,
                    "assists": stats.assists,
                    "rating": rating,
                    "ability": int(bucket["ability"]),
                }
            )
        return entries

    def test_leaderboard_top_equals_manual_aggregation(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            season_id = conn.execute("SELECT season_id FROM seasons WHERE season_number = 2").fetchone()["season_id"]
            entries = self._manual_entries(conn, season_id, "一级联赛")
            self.assertTrue(entries)

            profile = competition_queries.get_competition_profile(conn, "一级联赛", 2)
            boards = profile.leaderboards
            self.assertLessEqual(len(boards.top_scorers), 10)
            self.assertLessEqual(len(boards.top_assisters), 10)
            self.assertLessEqual(len(boards.top_rated), 10)

            max_goals = max(entry["goals"] for entry in entries)
            self.assertEqual(boards.top_scorers[0].goals, max_goals)
            max_assists = max(entry["assists"] for entry in entries)
            self.assertEqual(boards.top_assisters[0].assists, max_assists)

            best_rating = max(entry["rating"] for entry in entries)
            self.assertEqual(boards.top_rated[0].rating, best_rating)
            best_ids = {
                entry["stable"]
                for entry in entries
                if entry["rating"] == best_rating
                and entry["ability"] == max(e["ability"] for e in entries if e["rating"] == best_rating)
            }
            self.assertIn(boards.top_rated[0].player.player_id, best_ids)

            # 榜单整体排序确定性：重复查询一致。
            again = competition_queries.get_competition_profile(conn, "一级联赛", 2)
            self.assertEqual(again.leaderboards, boards)
            # 每行 matches_played 为该赛事出场数（appeared=1 行数）。
            manual_by_id = {entry["stable"]: entry for entry in entries}
            for entry in [*boards.top_scorers, *boards.top_assisters, *boards.top_rated]:
                self.assertEqual(entry.matches_played, manual_by_id[entry.player.player_id]["matches"])

    def test_cup_leaderboards_use_same_aggregation(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            season_id = conn.execute("SELECT season_id FROM seasons WHERE season_number = 2").fetchone()["season_id"]
            entries = self._manual_entries(conn, season_id, "优胜者杯")
            profile = competition_queries.get_competition_profile(conn, "优胜者杯", 2)
            self.assertTrue(entries)
            max_goals = max(entry["goals"] for entry in entries)
            self.assertEqual(profile.leaderboards.top_scorers[0].goals, max_goals)


class HistoryTests(QueryTestBase):
    def test_season_summaries_match_standings_and_archive(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            summaries = history_queries.list_season_summaries(conn)
            self.assertEqual([s.season_number for s in summaries], [1, 2])
            self.assertEqual(summaries[0].status, "completed")
            self.assertEqual(summaries[1].status, "active")

            profile = competition_queries.get_competition_profile(conn, "一级联赛", 1)
            champion = profile.standings[0].team_name
            self.assertEqual(summaries[0].premier_champion, champion)
            self.assertIsNone(summaries[0].winners_cup_champion)
            self.assertIsNone(summaries[0].challenge_cup_champion)
            self.assertIsNone(summaries[0].super_cup_champion)

            archive = self._archive(conn, 1)
            expected_top3 = tuple(item["label"] for item in archive["season_awards"]["top20"][:3])
            self.assertEqual(len(expected_top3), 3)
            self.assertEqual(summaries[0].top20_top3, expected_top3)

            self.assertIsNone(summaries[1].premier_champion)
            self.assertEqual(summaries[1].top20_top3, ())

    def test_competition_history(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            premier_history = history_queries.get_competition_history(conn, "一级联赛")
            self.assertEqual([line.season_number for line in premier_history], [1])
            archive = self._archive(conn, 1)
            mvp = archive["season_awards"]["competitions"]["一级联赛"]["mvp"]
            self.assertEqual(premier_history[0].champion, archive["premier_order"][0])
            self.assertIsNotNone(premier_history[0].champion_player)
            self.assertEqual(premier_history[0].champion_player.display_name, mvp["label"])
            self.assertEqual(
                premier_history[0].champion_player.player_id,
                base.canonical_player_id_for_name(mvp["label"]),
            )

            winners_history = history_queries.get_competition_history(conn, "优胜者杯")
            self.assertEqual([line.season_number for line in winners_history], [1])
            self.assertIsNone(winners_history[0].champion)

            playoff_history = history_queries.get_competition_history(conn, "升级附加赛")
            self.assertEqual(
                playoff_history[0].champion,
                archive["last_transition"]["playoff_winner"],
            )

            second_history = history_queries.get_competition_history(conn, "次级联赛")
            self.assertIsNone(second_history[0].champion_player, "次级联赛没有 MVP 奖项")

            with self.assertRaises(KeyError):
                history_queries.get_competition_history(conn, "不存在的赛事")

    def test_archive_detail(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            detail = history_queries.get_season_archive_detail(conn, 1)
            archive = self._archive(conn, 1)

            self.assertEqual(detail.season_number, 1)
            self.assertEqual([row.rank for row in detail.premier_order], list(range(1, 21)))
            self.assertEqual(detail.premier_order[0].team.display_name, archive["premier_order"][0])
            self.assertEqual([row.rank for row in detail.second_order], list(range(1, 21)))
            self.assertIsNone(detail.cup_champions.winners_cup)
            self.assertIsNone(detail.cup_champions.challenge_cup)
            self.assertIsNone(detail.cup_champions.super_cup)

            self.assertEqual(len(detail.top20), 20)
            self.assertEqual([line.rank for line in detail.top20], list(range(1, 21)))
            for line in detail.top20:
                self.assertTrue(line.player.player_id.startswith("real::"))
                self.assertEqual(line.player.player_id, base.canonical_player_id_for_name(line.label))
                self.assertTrue(line.label)
            self.assertEqual(detail.top20[0].label, archive["season_awards"]["top20"][0]["label"])

            award_types = {(line.competition, line.award_type) for line in detail.competition_awards}
            self.assertIn(("一级联赛", "top_scorer"), award_types)
            for line in detail.competition_awards:
                self.assertTrue(line.player.player_id.startswith("real::"))

            self.assertEqual(len(detail.team_honor_table), 40)
            champion_line = next(row for row in detail.team_honor_table if row.team_name == detail.premier_order[0].team.display_name)
            self.assertEqual(champion_line.league_result, "第 1 名")
            self.assertEqual(champion_line.total_titles, 1)

            self.assertTrue(detail.player_settlement_points)
            for point in detail.player_settlement_points:
                self.assertTrue(point.player.player_id.startswith("real::"))
                self.assertIn(point.stage, {"冬窗", "赛季末"})

            with self.assertRaises(KeyError):
                history_queries.get_season_archive_detail(conn, 2)


class DashboardTests(QueryTestBase):
    def test_dashboard_main_save(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            snapshot = dashboard_queries.get_dashboard(conn)
            self.assertEqual(snapshot.current_season, 2)
            self.assertEqual(snapshot.current_week, MAIN_WEEKS)
            self.assertFalse(snapshot.season_complete)

            pending_rows = conn.execute("SELECT type, COUNT(*) AS total FROM pending_actions GROUP BY type").fetchall()
            expected_counts = {row["type"]: row["total"] for row in pending_rows}
            self.assertEqual(snapshot.pending_counts.ability_review, expected_counts.get("ability_review", 0))
            self.assertEqual(snapshot.pending_counts.transfer_review, expected_counts.get("transfer_review", 0))
            self.assertEqual(snapshot.pending_counts.draft, expected_counts.get("draft", 0))
            self.assertEqual(
                (snapshot.pending_counts.ability_review, snapshot.pending_counts.transfer_review, snapshot.pending_counts.draft),
                (0, 0, 0),
            )

            upcoming = snapshot.upcoming_matches
            self.assertEqual(len(upcoming), 8)
            self.assertTrue(all(not m.is_completed for m in upcoming))
            self.assertTrue(all(m.season_number == 2 for m in upcoming))
            self.assertEqual([m.week_number for m in upcoming], sorted(m.week_number for m in upcoming))
            self.assertEqual(min(m.week_number for m in upcoming), MAIN_WEEKS + 1)

            latest = snapshot.latest_results
            self.assertEqual(len(latest), 8)
            self.assertTrue(all(m.is_completed for m in latest))
            self.assertEqual([m.week_number for m in latest], sorted((m.week_number for m in latest), reverse=True))
            self.assertEqual(max(m.week_number for m in latest), MAIN_WEEKS)

            self.assertEqual(len(snapshot.league_leaders), 2)
            for leaders in snapshot.league_leaders:
                self.assertEqual(len(leaders.top_scorers), 3)
                self.assertEqual(len(leaders.assist_leaders), 3)
                values = [entry.value for entry in leaders.top_scorers]
                self.assertEqual(values, sorted(values, reverse=True))
            self.assertEqual(
                [leaders.competition.competition_id for leaders in snapshot.league_leaders],
                ["一级联赛", "次级联赛"],
            )

            self.assertEqual(snapshot.cup_champions_so_far, ())

    def test_dashboard_league_leaders_match_manual_top3(self) -> None:
        with base.open_read_connection(MAIN_SAVE) as conn:
            season_id = conn.execute("SELECT season_id FROM seasons WHERE season_number = 2").fetchone()["season_id"]
            snapshot = dashboard_queries.get_dashboard(conn)
            premier = snapshot.league_leaders[0]
            manual_goals = [
                row["goals"]
                for row in conn.execute(
                    """
                    SELECT SUM(pms.goals) AS goals
                    FROM player_match_stats AS pms
                    JOIN matches AS m ON m.match_id = pms.match_id
                    WHERE m.season_id = ? AND m.category = 'premier' AND pms.appeared = 1
                    GROUP BY pms.player_id
                    ORDER BY goals DESC
                    LIMIT 3
                    """,
                    (season_id,),
                )
            ]
            self.assertEqual([entry.value for entry in premier.top_scorers], manual_goals)

    def test_dashboard_pending_transfer_counts(self) -> None:
        with base.open_read_connection(PENDING_SAVE) as conn:
            snapshot = dashboard_queries.get_dashboard(conn)
            self.assertEqual(snapshot.current_week, PENDING_WEEKS)
            pending_rows = conn.execute("SELECT type, COUNT(*) AS total FROM pending_actions GROUP BY type").fetchall()
            expected = {row["type"]: row["total"] for row in pending_rows}
            self.assertEqual(snapshot.pending_counts.transfer_review, expected.get("transfer_review", 0))
            self.assertGreaterEqual(snapshot.pending_counts.transfer_review, 1, "第 25 周冬窗应产生转会待办")
            self.assertEqual(snapshot.pending_counts.ability_review, 0)
            self.assertEqual(snapshot.pending_counts.draft, 0)

    def test_dashboard_cup_champions_so_far(self) -> None:
        with base.open_read_connection(CUP_SAVE) as conn:
            snapshot = dashboard_queries.get_dashboard(conn)
            self.assertEqual(snapshot.current_season, 3)
            runtime = self._runtime(conn, 3)
            expected = runtime["cup_state"]["super_cup"]["champion"]
            self.assertIsNotNone(expected)
            lines = {line.competition.competition_id: line.champion for line in snapshot.cup_champions_so_far}
            self.assertEqual(lines, {"超级杯": expected})

            self.assertEqual(len(snapshot.upcoming_matches), 8)
            self.assertEqual(min(m.week_number for m in snapshot.upcoming_matches), CUP_WEEKS + 1)


if __name__ == "__main__":
    unittest.main()
