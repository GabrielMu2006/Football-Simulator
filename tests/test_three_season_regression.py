"""三赛季状态机回归测试（阶段 0）。

在临时目录以固定随机源连续推进三个完整赛季，验证：
- 赛程完整性（每队 38 场、两级各 38 轮）与积分约束；
- 冬窗/赛季末结算缓存、评分与身价口径；
- 升降级与附加赛衔接、历史归档；
- 杯赛按第 2/3 赛季激活的设计时序与冠军产出；
- 待办阻塞行为（能力审核、转会、选秀）；
- 球员“赛事合计 == 赛季总计”一致性。

只断言结构不变量，不把随机具体比分写死为规则；逐周比分由基线指纹测试覆盖。
"""

from __future__ import annotations

import unittest

from football_simulator.data import real_player_id
from football_simulator.state import (
    AWARD_COMPETITIONS,
    PREMIER_DIVISION,
    SECOND_DIVISION,
    _build_competition_player_stats,
    _build_season_awards,
    apply_ability_review_decisions,
    apply_transfer_review_decisions,
)

from tests.support import (
    FreezeTestCase,
    advance_week,
    create_save,
    load_snapshot,
    load_state_json,
    resolve_pending,
    run_season,
    run_weeks,
    simulate_next_week,
    state_path,
)

CUP_KEYS = ("优胜者杯", "挑战杯", "超级杯")


class Season1StructureTests(FreezeTestCase):
    def test_full_season_league_structure(self) -> None:
        create_save()
        weeks_run = run_season()
        self.assertEqual(weeks_run, 52)

        snap = load_snapshot()
        state_json = load_state_json()

        # -- 赛程完整性 ------------------------------------------------
        premier_matchdays = [
            matchday
            for week in state_json["simulated_weeks"]
            for matchday in week.get("premier_matchdays", [])
        ]
        second_matchdays = [
            matchday
            for week in state_json["simulated_weeks"]
            for matchday in week.get("second_matchdays", [])
        ]
        self.assertEqual(len(premier_matchdays), 38)
        self.assertEqual(len(second_matchdays), 38)
        team_matches: dict = {}
        total_home_goals = 0
        total_away_goals = 0
        for matchday in premier_matchdays:
            self.assertEqual(matchday["competition"], PREMIER_DIVISION)
            self.assertEqual(len(matchday["results"]), 10)
            for result in matchday["results"]:
                team_matches[result["home_team"]] = team_matches.get(result["home_team"], 0) + 1
                team_matches[result["away_team"]] = team_matches.get(result["away_team"], 0) + 1
                total_home_goals += result["home_goals"]
                total_away_goals += result["away_goals"]
        self.assertEqual({name: count for name, count in team_matches.items() if count != 38}, {})

        # -- 积分约束 --------------------------------------------------
        table = snap.premier_table
        self.assertEqual(len(table), 20)
        for row in table:
            self.assertEqual(row.played, 38)
            self.assertEqual(row.wins + row.draws + row.losses, 38)
            self.assertEqual(row.points, row.wins * 3 + row.draws)
        sum_points = sum(row.points for row in table)
        sum_gf = sum(row.goals_for for row in table)
        sum_ga = sum(row.goals_against for row in table)
        total_draws = sum(row.draws for row in table)
        self.assertEqual(sum_gf, total_home_goals + total_away_goals)
        self.assertEqual(sum_ga, total_home_goals + total_away_goals)
        # 每场胜负产生 3 分、平局产生 2 分；表格中平局行数为双倍计。
        self.assertEqual(total_draws % 2, 0)
        self.assertEqual(sum_points, 380 * 3 - total_draws // 2)
        points_in_order = [row.points for row in table]
        self.assertEqual(points_in_order, sorted(points_in_order, reverse=True))

        # -- 第 1 赛季无杯赛 --------------------------------------------
        for cup_key in CUP_KEYS:
            self.assertIsNone(snap.cup_champions.get(cup_key))
        cup_matchdays = [
            matchday
            for week in state_json["simulated_weeks"]
            for matchday in week.get("cup_matchdays", [])
        ]
        self.assertEqual(cup_matchdays, [])
        playoff_matchdays = [
            matchday
            for week in state_json["simulated_weeks"]
            for matchday in week.get("playoff_matchdays", [])
        ]
        # 附加赛在第 46-49 周进行，且第 1 赛季同样执行。
        self.assertEqual(len(playoff_matchdays), 4)

        # -- 归档与过渡 ------------------------------------------------
        self.assertTrue(snap.season_complete)
        self.assertEqual(snap.current_week, 52)
        self.assertEqual(len(snap.history), 1)
        archive = snap.history[0]
        self.assertEqual(archive["season_number"], 1)
        self.assertEqual(len(archive["premier_order"]), 20)
        self.assertEqual(len(archive["season_awards"]["top20"]), 20)

        transition = state_json["last_transition"]
        self.assertEqual(transition["season_number"], 1)
        self.assertEqual(len(transition["relegated"]), 3)
        self.assertEqual(len(transition["promoted_direct"]), 2)
        self.assertEqual(transition["relegated"], [row.team.name for row in snap.premier_table[-3:]])
        self.assertEqual(transition["promoted_direct"], [row.team.name for row in snap.second_table[:2]])
        self.assertEqual(len(snap.next_premier_team_names), 20)
        self.assertEqual(len(snap.next_second_team_names), 20)
        self.assertEqual(len(set(snap.next_premier_team_names) | set(snap.next_second_team_names)), 40)

        # -- 结算缓存 --------------------------------------------------
        self.assertIn("winter", state_json["settlement_cache"])
        self.assertIn("final", state_json["settlement_cache"])
        self.assertTrue(state_json["settlement_cache"]["winter"])
        self.assertTrue(state_json["settlement_cache"]["final"])
        for value in state_json["settlement_cache"]["final"].values():
            self.assertGreaterEqual(value["season_rating"], 0.0)
            self.assertLessEqual(value["season_rating"], 10.0)
            self.assertGreaterEqual(value["market_value"], 8.0)

    def test_snapshot_stats_replay_exactly_premier_and_cup_matchdays(self) -> None:
        # 已知口径（阶段 0 冻结）：快照 player_stats 只累计 premier_matchdays +
        # cup_matchdays 的球员增量；second_matchdays 与 playoff_matchdays 的球员
        # 统计保留在 simulated_weeks 原始数据中但不参与聚合。快照按真实球员
        # 注册表建行，零统计球员也有行。
        create_save()
        run_season()
        snap = load_snapshot()
        state_json = load_state_json()
        season_rows = {row.player.player_id: row for row in snap.player_stats if row.player.is_real}
        self.assertTrue(season_rows)

        replayed: dict = {}
        for simulated_week in state_json["simulated_weeks"]:
            for key in ("premier_matchdays", "cup_matchdays"):
                for matchday in simulated_week.get(key, []):
                    for result in matchday.get("results", []):
                        for player_id, delta in result.get("player_stats", {}).items():
                            if player_id not in season_rows:
                                continue
                            entry = replayed.setdefault(
                                player_id,
                                {field: 0 for field in ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets")},
                            )
                            for field in entry:
                                entry[field] += int(delta.get(field, 0))

        self.assertEqual(set(replayed).issubset(set(season_rows)), True, "重放出现的球员必须都在快照统计中")
        for player_id, totals in replayed.items():
            row = season_rows[player_id]
            for field, value in totals.items():
                self.assertEqual(value, getattr(row, field), f"球员 {player_id} {field} 与重放值不一致")

    def test_awards_structure_season1(self) -> None:
        create_save()
        run_season()
        snap = load_snapshot()
        awards = _build_season_awards(snap)
        self.assertEqual(len(awards["top20"]), 20)
        self.assertEqual([item["rank"] for item in awards["top20"]], list(range(1, 21)))
        scores = [item["score"] for item in awards["top20"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for item in awards["top20"]:
            self.assertIn(item["position"], {"GK", "DF", "MF", "FW"})
        premier_awards = awards["competitions"].get(PREMIER_DIVISION)
        self.assertIsNotNone(premier_awards)
        self.assertIsNotNone(premier_awards["mvp"])
        self.assertIsNotNone(premier_awards["top_scorer"])
        self.assertGreater(premier_awards["top_scorer"]["goals"], 0)


class PendingBlockingTests(FreezeTestCase):
    def test_pending_states_block_and_resolve(self) -> None:
        save = "blocking"
        create_save(save)
        run_weeks(save, 24)
        snap = load_snapshot(save)
        self.assertEqual(snap.current_week, 24)
        self.assertEqual(snap.pending_transfer_review, [])

        # 第 25 周（冬窗第一周）产生转会待办，随后阻塞推进。
        simulate_next_week(save)
        snap = load_snapshot(save)
        self.assertEqual(snap.current_week, 25)
        self.assertTrue(snap.pending_transfer_review)
        with self.assertRaises(ValueError):
            simulate_next_week(save)

        decisions = {item["trade_id"]: True for item in snap.pending_transfer_review}
        apply_transfer_review_decisions(save, decisions)
        snap = load_snapshot(save)
        self.assertEqual(snap.pending_transfer_review, [])
        state_json = load_state_json(save)
        self.assertTrue(state_json["transfer_history"])
        statuses = {row["status"] for row in state_json["transfer_history"]}
        self.assertTrue(statuses.issubset({"玩家通过", "系统重算通过"}))
        self.assertRosterIntegrity(snap)

        # 第 26-48 周正常推进（第 26/27 周的转会待办逐周处理）。
        run_weeks(save, 23)
        self.assertEqual(load_snapshot(save).current_week, 48)

        # 第 49 周（赛季末结算周）产生能力审核 + 选秀待办，阻塞推进。
        simulate_next_week(save)
        snap = load_snapshot(save)
        self.assertEqual(snap.current_week, 49)
        pool_size = len(snap.real_player_pool)
        expected_review_count = max(1, int(pool_size * 0.4))
        self.assertEqual(len(snap.pending_ability_review), expected_review_count)
        self.assertEqual(snap.pending_draft.get("status"), "awaiting_input")
        with self.assertRaises(ValueError):
            simulate_next_week(save)

        # 能力审核按姓名决策（已知缺陷：同名球员会互相覆盖，当前池内无重名）。
        review_items = list(snap.pending_ability_review)
        apply_ability_review_decisions(save, {item["name"]: True for item in review_items})
        snap = load_snapshot(save)
        self.assertEqual(snap.pending_ability_review, [])
        pool_by_name = {profile.name: profile for profile in snap.real_player_pool}
        for item in review_items:
            self.assertEqual(pool_by_name[item["name"]].ability, item["new_ability"])

        # 选秀使用配置候选池，完成后池增长 target_count，新球员有初始身价。
        snap = resolve_pending(save)
        draft_log = load_state_json(save)["last_draft"]
        target_count = int(draft_log["target_count"])
        self.assertEqual(len(snap.real_player_pool), pool_size + target_count)
        self.assertEqual(len(draft_log["results"]), target_count)
        for result in draft_log["results"]:
            self.assertEqual(result["market_value"], 30.0)
        new_profiles = snap.real_player_pool[pool_size:]
        for profile in new_profiles:
            self.assertEqual(profile.initial_market_value, 30.0)
        self.assertRosterIntegrity(snap)

        # 第 50-52 周（夏窗）各自产生转会待办并被自动处理，然后赛季结束。
        run_weeks(save, 3)
        snap = load_snapshot(save)
        self.assertTrue(snap.season_complete)
        self.assertRosterIntegrity(snap)


class ThreeSeasonCupTests(FreezeTestCase):
    def test_cups_activate_by_design_and_archives_accumulate(self) -> None:
        save = "cups"
        real_ids_per_season = []

        create_save(save)
        run_season(save)
        snap = load_snapshot(save)
        self.assertTrue(all(snap.cup_champions.get(key) is None for key in CUP_KEYS))
        real_ids_per_season.append({real_player_id(p.name) for p in snap.real_player_pool})

        # 第 2 赛季：优胜者杯 + 挑战杯激活，超级杯需要往届冠军数据才激活。
        create_save(save)
        snap = load_snapshot(save)
        self.assertTrue(snap.cup_state.get("winners_cup", {}).get("active"))
        self.assertTrue(snap.cup_state.get("challenge_cup", {}).get("active"))
        self.assertFalse(snap.cup_state.get("super_cup", {}).get("active"))
        run_season(save)
        snap = load_snapshot(save)
        self.assertIsNotNone(snap.cup_champions.get("优胜者杯"))
        self.assertIsNotNone(snap.cup_champions.get("挑战杯"))
        self.assertIsNone(snap.cup_champions.get("超级杯"))
        state_json = load_state_json(save)
        cup_matchdays = [
            matchday
            for week in state_json["simulated_weeks"]
            for matchday in week.get("cup_matchdays", [])
        ]
        self.assertGreater(len(cup_matchdays), 0)
        real_ids_per_season.append({real_player_id(p.name) for p in snap.real_player_pool})

        # 第 3 赛季：超级杯激活，三项杯赛都产生冠军。
        create_save(save)
        snap = load_snapshot(save)
        self.assertTrue(snap.cup_state.get("super_cup", {}).get("active"))
        run_season(save)
        snap = load_snapshot(save)
        for key in CUP_KEYS:
            self.assertIsNotNone(snap.cup_champions.get(key), f"{key} 冠军缺失")
        self.assertEqual(len(snap.history), 3)
        self.assertEqual([season["season_number"] for season in snap.history], [1, 2, 3])
        real_ids_per_season.append({real_player_id(p.name) for p in snap.real_player_pool})

        # 选秀逐年扩充真实球员池，且 ID 唯一、历史池不缩水。
        for previous, current in zip(real_ids_per_season, real_ids_per_season[1:]):
            self.assertTrue(previous.issubset(current))
            self.assertGreater(len(current), len(previous))
        all_ids = [real_player_id(p.name) for p in snap.real_player_pool]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertRosterIntegrity(snap)

        # 三赛季后赛事合计 == 赛季总计（对第 3 赛季快照）。
        self._assertCompetitionTotalsMatchSeasonTotals(snap)

        # 第 1/2 赛季归档仍然可查且未被覆盖。
        archived_numbers = [season["season_number"] for season in snap.history]
        self.assertEqual(archived_numbers, [1, 2, 3])
        for season in snap.history:
            self.assertEqual(len(season["premier_order"]), 20)

    def _assertCompetitionTotalsMatchSeasonTotals(self, snap) -> None:
        # 已知口径：赛事统计行的 player_id 使用历史键 real::<显示名>，而赛季
        # 统计/注册表使用 real::<slug>（两套 ID 并存，阶段 2 必须收敛）。
        # 因此这里按 label 连接，断言六项统计的“赛事合计 == 赛季总计”。
        competition_stats = _build_competition_player_stats(snap)
        season_rows = {
            row.player.label: row for row in snap.player_stats if row.player.is_real
        }
        fields = ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets")
        merged: dict = {}
        for competition, rows in competition_stats.items():
            self.assertIn(competition, AWARD_COMPETITIONS)
            for row in rows:
                self.assertIn(row["label"], season_rows, f"{row['label']} 不在赛季统计中")
                entry = merged.setdefault(row["label"], {field: 0 for field in fields})
                for field in fields:
                    entry[field] += row[field]
        self.assertTrue(merged)
        for label, totals in merged.items():
            season_row = season_rows[label]
            for field in fields:
                self.assertEqual(
                    totals[field],
                    getattr(season_row, field),
                    f"球员 {label} 六项统计赛事合计与赛季总计不一致：{field}",
                )

    def test_transfers_keep_roster_integrity_across_seasons(self) -> None:
        save = "transfer_integrity"
        create_save(save)
        run_season(save)
        create_save(save)
        run_season(save)
        snap = load_snapshot(save)
        self.assertRosterIntegrity(snap)
        state_json = load_state_json(save)
        # 每个赛季有冬窗（3 周）与夏窗（3 周）共 6 个转会窗口。
        self.assertGreaterEqual(len(state_json["transfer_history"]), 2)
        for row in state_json["transfer_history"]:
            self.assertIn(row["status"], {"玩家通过", "系统重算通过", "玩家拒绝"})


if __name__ == "__main__":
    unittest.main()
