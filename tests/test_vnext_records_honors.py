"""NH-020/030/040: 冠军快照、九赛事统计、榜单与次级三奖。"""
from __future__ import annotations
import copy, random, shutil, sqlite3, tempfile, unittest
from collections import defaultdict
from pathlib import Path
from football_simulator.app import records
from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.game import world as game_world
from football_simulator.persistence_v2 import store
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures_vnext"


class RecordsHonorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_vnext_rec_")
        support.install_test_data_root(cls._tmp)
        cls.svc = GameService(rng_factory=lambda: random.Random(20261020))
        cls.svc.import_save(FIX / "s3-finalized.fsarchive", "s3rec")
        cls.world = store.load_world("s3rec")
        records.invalidate("s3rec")

    @classmethod
    def tearDownClass(cls) -> None:
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def _oracle(self) -> dict:
        conn = store.open_readonly("s3rec")
        try:
            rows = conn.execute(
                "SELECT pms.player_id, pms.season_number, pms.team_id, m.competition, pms.goals, pms.assists "
                "FROM player_match_stats pms JOIN matches m ON m.match_id=pms.match_id "
                "WHERE m.status='completed' AND pms.appeared=1"
            ).fetchall()
        finally:
            conn.close()
        by_player = defaultdict(lambda: {"appearances": 0, "goals": 0, "assists": 0})
        by_comp = defaultdict(lambda: {"appearances": 0, "goals": 0, "assists": 0})
        by_season = defaultdict(lambda: {"appearances": 0, "goals": 0, "assists": 0})
        for row in rows:
            for target in (by_player[row["player_id"]], by_comp[(row["player_id"], row["competition"])], by_season[(row["player_id"], row["season_number"])]):
                target["appearances"] += 1
                target["goals"] += int(row["goals"])
                target["assists"] += int(row["assists"])
        return {"rows": rows, "player": dict(by_player), "competition": dict(by_comp), "season": dict(by_season)}

    def test_all_players_and_nine_competitions_match_oracle(self) -> None:
        oracle = self._oracle()
        self.assertGreater(len(oracle["player"]), 0)
        comps = {row["competition"] for row in oracle["rows"]}
        self.assertTrue(set(rules.ALL_COMPETITION_IDS) <= comps, comps)
        for player_id, expected in oracle["player"].items():
            got = records.career_totals("s3rec", player_id)
            self.assertEqual((got["appearances"], got["goals"], got["assists"]), (expected["appearances"], expected["goals"], expected["assists"]), player_id)
        for player_id in oracle["player"]:
            splits = records.career_splits("s3rec", player_id)
            self.assertEqual(sum(row["appearances"] for row in splits["competition"]), oracle["player"][player_id]["appearances"])
            self.assertEqual(sum(row["goals"] for row in splits["season"]), oracle["player"][player_id]["goals"])
            self.assertEqual(sum(row["assists"] for row in splits["season_competition"]), oracle["player"][player_id]["assists"])

    def test_leaderboards_pagination_shared_ranks_and_no_duplicate_players(self) -> None:
        result = records.leaderboards("s3rec", scope="all", metric="goals", page=1, page_size=10)
        self.assertEqual(len(result["rows"]), 10)
        self.assertTrue(all(row["goals"] > 0 for row in result["rows"]))
        ids = [row["player_id"] for row in result["rows"]]
        self.assertEqual(len(ids), len(set(ids)))
        all_rows = records.leaderboards("s3rec", scope="all", metric="goals", page=1, page_size=1000)["rows"]
        self.assertEqual(len(all_rows), result["total"])
        # shared values share rank, and order is deterministic by player id
        values = defaultdict(list)
        for row in all_rows:
            values[row["goals"]].append(row["rank"])
        for ranks in values.values():
            self.assertEqual(len(set(ranks)), 1)
        self.assertEqual(ids, [row["player_id"] for row in all_rows[:10]])
        second_page = records.leaderboards("s3rec", scope="all", metric="goals", page=2, page_size=10)
        self.assertFalse(set(ids) & {row["player_id"] for row in second_page["rows"]})

    def test_edition_and_metric_independent(self) -> None:
        season = 3
        goals = records.leaderboards("s3rec", competition_id="premier", scope="edition", metric="goals", season_number=season, page_size=500)
        assists = records.leaderboards("s3rec", competition_id="premier", scope="edition", metric="assists", season_number=season, page_size=500)
        self.assertTrue(goals["rows"] and assists["rows"])
        self.assertEqual({row["player_id"] for row in goals["rows"]}, set(row["player_id"] for row in goals["rows"]))
        self.assertTrue(all(row["season"] == season for row in goals["rows"]))
        self.assertTrue(all(row["goals"] > 0 for row in goals["rows"]))
        self.assertTrue(all(row["assists"] > 0 for row in assists["rows"]))
        self.assertNotEqual([row["player_id"] for row in goals["rows"]], [row["player_id"] for row in assists["rows"]])

    def test_awards_cover_second_not_playoff_and_store_projection(self) -> None:
        awards = (self.world["season"].get("awards") or {}).get("competitions", {})
        self.assertEqual(set(awards), {rules.PREMIER, rules.SECOND, *rules.CUP_IDS})
        self.assertNotIn(rules.PLAYOFF, awards)
        for comp in (rules.PREMIER, rules.SECOND, *rules.CUP_IDS):
            self.assertIn("top_scorer", awards[comp])
            self.assertIn("assist_leader", awards[comp])
            self.assertIn("mvp", awards[comp])
        conn = store.open_readonly("s3rec")
        try:
            count = conn.execute("SELECT COUNT(*) FROM awards WHERE season_number=3").fetchone()[0]
            comps = {row[0] for row in conn.execute("SELECT DISTINCT competition FROM awards WHERE season_number=3")}
        finally:
            conn.close()
        self.assertGreater(count, 0)
        self.assertEqual(comps, {rules.PREMIER, rules.SECOND, *rules.CUP_IDS})

    def test_championship_snapshot_roster_and_second_points(self) -> None:
        champs = self.world["season"]["championships"]
        self.assertEqual(len(champs), 8)
        for row in champs.values():
            self.assertTrue(row["roster_player_ids"])
            self.assertEqual(row["roster_player_ids"], list(dict.fromkeys(row["roster_player_ids"])))
            self.assertTrue(set(row["roster_player_ids"]) <= set(self.world["players"]))
            self.assertEqual(row["source"], "championship_decision")
        second = next(row for row in champs.values() if row["competition_id"] == rules.SECOND)
        second_points = [h["points"] for h in self.world["season"]["honor_rows"] if h["competition"] == rules.SECOND and h["team_id"] == second["champion_team_id"]]
        self.assertTrue(second_points)
        self.assertTrue(all(int(p) == 0 for p in second_points))
        # player medals never add team honor points
        self.assertEqual(sum(int(p) for p in second_points), 0)

    def test_capture_is_idempotent_and_respects_frozen_roster(self) -> None:
        world = copy.deepcopy(self.world)
        cup_id = "confluence"
        state = world["season"]["cups"][cup_id]
        team_id = state["champion"]
        title_id = f"{world['world_id']}:{world['season_number']}:{cup_id}"
        original = list(world["season"]["championships"][title_id]["roster_player_ids"])
        removed = original[0]
        world["teams"][team_id]["roster"] = [r for r in world["teams"][team_id]["roster"] if r["player_id"] != removed]
        game_world._capture_championships(world, 31)
        self.assertEqual(world["season"]["championships"][title_id]["roster_player_ids"], original)
        # already captured champion cannot be overwritten; remove record to prove boundary
        del world["season"]["championships"][title_id]
        game_world._capture_championships(world, 31)
        self.assertNotIn(removed, world["season"]["championships"][title_id]["roster_player_ids"])

    def test_archive_and_current_dedup_for_team_and_player(self) -> None:
        team_id = next(row["champion_team_id"] for row in self.world["season"]["championships"].values())
        honors = records.team_honors(self.world, team_id)
        self.assertEqual(honors["total_titles"], len(honors["championships"]))
        keys = {(row["title_id"], row["champion_team_id"]) for row in honors["championships"]}
        self.assertEqual(len(keys), honors["total_titles"])
        player_id = next(row["roster_player_ids"][0] for row in self.world["season"]["championships"].values())
        ph = records.player_honors(self.world, player_id)
        self.assertEqual(len({(row["title_id"]) for row in ph["championships"]}), len(ph["championships"]))
        # three awards dedup on (season, competition, award_type, player_id)
        award_keys = {(row["season_number"], row["competition"], row["award_type"], row["player_id"]) for row in records._award_rows(self.world)}
        self.assertEqual(len(award_keys), len(records._award_rows(self.world)))


    def test_export_import_preserves_championship_snapshots(self) -> None:
        source = Path(self._tmp) / "snap.fsarchive"
        self.svc.export_save("s3rec", source)
        imported = self.svc.import_save(source, "s3rec-copy")
        copied = store.load_world("s3rec-copy")
        self.assertEqual(set(copied["season"]["championships"]), set(self.world["season"]["championships"]))
        for title_id, row in self.world["season"]["championships"].items():
            self.assertEqual(copied["season"]["championships"][title_id]["roster_player_ids"], row["roster_player_ids"])
        player = next(iter(self.world["players"]))
        self.assertEqual(records.career_totals("s3rec-copy", player), records.career_totals("s3rec", player))

    def test_records_query_does_not_write(self) -> None:
        path = store.database_path("s3rec")
        import hashlib
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        records.leaderboards("s3rec", scope="all", metric="goals")
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
