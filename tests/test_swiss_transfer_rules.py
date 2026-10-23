"""Record-pool Swiss graph and value/capacity/window transfer contracts."""

from __future__ import annotations

import copy
import json
import random
import shutil
import tempfile
import unittest
from collections import Counter
from unittest import mock

from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from tests import support
from tests.test_macos_identity_p2 import _Base
from football_simulator.app.service import GameService
from football_simulator.game import transfers, world as game_world
from football_simulator.game.config import team_objects
from football_simulator.game.models import FORMATION_RULES, default_player_id
from football_simulator.persistence_v2 import store
from football_simulator.ui.competition_boards import GraphPanel, SwissBoard
from football_simulator.ui.presentation import swiss


def small_world():
    world = {"season_number": 1, "players": {}, "teams": {}, "transfer_history": [],
             "season": {"number": 1, "week": 25, "transfers": [], "pending": {"transfer_review": [], "ability_review": [], "draft": {}}}}
    for team in ("a", "b", "c"):
        roster = []
        world["teams"][team] = {"team_id": team, "club_en": team, "name_zh": team, "division": "一级联赛", "stadium_id": "test-stadium", "roster": roster}
        for position, count in FORMATION_RULES.items():
            for slot in range(1, count + 1):
                pid = default_player_id(team, position, slot)
                roster.append({"player_id": pid, "position": position, "slot": slot})
                world["players"][pid] = {"player_id": pid, "name": None, "position": position, "slot_number": slot, "team_id": team, "is_real": False, "ability": 50, "market_value": None}
    for team, position, name, value in (("a", "DF", "ad", 30), ("a", "MF", "am", 60), ("a", "FW", "af", 30),
                                        ("b", "MF", "bm", 30), ("b", "FW", "bf", 30), ("b", "DF", "bd", 60),
                                        ("c", "GK", "cg", 30), ("c", "DF", "cd", 30)):
        add_real(world, team, position, name, value)
    return world


def add_real(world, team, position, name, value):
    entry = next(entry for entry in world["teams"][team]["roster"] if entry["position"] == position and not world["players"][entry["player_id"]]["is_real"])
    world["players"].pop(entry["player_id"])
    pid = "real::" + name
    entry["player_id"] = pid
    world["players"][pid] = {"player_id": pid, "name": name, "position": position, "slot_number": entry["slot"], "team_id": team, "is_real": True, "ability": 75, "market_value": value, "initial_market_value": value}
    return pid


def trade(a=("ad",), b=("bm",), *, team_a="a", team_b="b", window="winter", week=25, tid="test-trade"):
    return {"trade_id": tid, "team_a": team_a, "team_b": team_b, "players_a": ["real::" + p for p in a], "players_b": ["real::" + p for p in b], "window": window, "week": week}


def assert_rosters(case, world):
    all_ids = []
    for team, data in world["teams"].items():
        roster = data["roster"]
        case.assertEqual(len(roster), 11)
        case.assertEqual(Counter(world["players"][e["player_id"]]["position"] for e in roster), FORMATION_RULES)
        for entry in roster:
            player = world["players"][entry["player_id"]]
            case.assertEqual((player["team_id"], player["position"], player["slot_number"]), (team, entry["position"], entry["slot"]))
            all_ids.append(entry["player_id"])
    case.assertEqual(len(all_ids), len(set(all_ids)))
    case.assertEqual(len(team_objects(world["teams"], world["players"])), len(world["teams"]))


class TransferRulesTests(unittest.TestCase):
    def setUp(self):
        self.world = small_world()

    def test_cross_position_exchange_keeps_formation_and_identity(self):
        game_world._apply_trade(self.world, trade())
        self.assertEqual(self.world["players"]["real::ad"]["team_id"], "b")
        self.assertEqual(self.world["players"]["real::bm"]["team_id"], "a")
        self.assertEqual(self.world["players"]["real::ad"]["position"], "DF")
        assert_rosters(self, self.world)

    def test_one_for_two_and_two_for_two(self):
        for item in (trade(("am",), ("bm", "bf")), trade(("ad", "af"), ("bm", "bf"))):
            world = small_world()
            game_world._apply_trade(world, item)
            assert_rosters(self, world)

    def test_value_threshold_uses_package_totals_and_includes_twenty(self):
        self.world["players"]["real::bm"]["market_value"] = 50
        game_world._apply_trade(self.world, trade())
        self.world = small_world()
        self.world["players"]["real::bm"]["market_value"] = 50.01
        before = copy.deepcopy(self.world)
        with self.assertRaisesRegex(game_world.WorldError, "身价差超过20"):
            game_world._apply_trade(self.world, trade())
        self.assertEqual(self.world, before)

    def test_position_over_capacity_is_rejected_without_mutation(self):
        add_real(self.world, "a", "GK", "ag", 30)
        before = copy.deepcopy(self.world)
        with self.assertRaisesRegex(game_world.WorldError, "位置人数超出"):
            game_world._apply_trade(self.world, trade(("ad",), ("cg",), team_b="c"))
        self.assertEqual(self.world, before)

    def test_window_lock_spans_weeks_but_not_next_window_or_season(self):
        item = trade()
        game_world._apply_trade(self.world, item)
        self.world["transfer_history"].append({**item, "season": 1, "status": "approved"})
        self.world["season"]["week"] = 26
        reverse = trade(("bm",), ("ad",), week=26)
        with self.assertRaisesRegex(game_world.WorldError, "本转会窗"):
            game_world._apply_trade(self.world, reverse)
        self.world["season"]["week"] = 50
        game_world._apply_trade(self.world, trade(("bm",), ("ad",), window="summer", week=50))
        self.world["season"].update(number=2, week=25)
        self.world["season_number"] = 2
        game_world._apply_trade(self.world, trade())
        assert_rosters(self, self.world)

    def test_duplicate_players_and_forged_window_rejected(self):
        for item in (trade(("ad", "ad"), ("bm",)), trade(window="summer", week=50)):
            before = copy.deepcopy(self.world)
            with self.assertRaises(game_world.WorldError):
                game_world._apply_trade(self.world, item)
            self.assertEqual(self.world, before)

    def test_generator_all_approvals_are_legal_and_do_not_mutate_before_approval(self):
        forms, cross = set(), False
        for seed in range(30):
            world = small_world()
            before = copy.deepcopy(world)
            rows = transfers.generate_proposals(world, random.Random(seed), "winter", game_world._player_value)
            self.assertEqual(world, before)
            used = set()
            for item in rows:
                ids = item["players_a"] + item["players_b"]
                self.assertFalse(used & set(ids))
                used.update(ids)
                self.assertLessEqual(item["value_gap"], 20)
                forms.add(tuple(sorted((len(item["players_a"]), len(item["players_b"])))))
                cross |= {world["players"][pid]["position"] for pid in item["players_a"]} != {world["players"][pid]["position"] for pid in item["players_b"]}
                game_world._apply_trade(world, item)
                world["transfer_history"].append({**item, "season": 1, "status": "approved"})
                assert_rosters(self, world)
        self.assertTrue({(1, 1), (1, 2), (2, 2)} <= forms)
        self.assertTrue(forms <= {(1, 1), (1, 2), (2, 2), (1, 3), (2, 3)})
        self.assertTrue(cross)

    def test_generator_excludes_earlier_window_moves_and_rejected_is_not_locked(self):
        row = {**trade(), "season": 1, "status": "approved"}
        self.world["transfer_history"] = [row]
        self.world["season"]["week"] = 27
        rows = transfers.generate_proposals(self.world, random.Random(17), "winter", game_world._player_value)
        self.assertFalse({"real::ad", "real::bm"} & {pid for item in rows for key in ("players_a", "players_b") for pid in item[key]})
        row["status"] = "rejected"
        self.assertEqual(transfers.moved_in_window(self.world, "winter"), set())

    def test_later_invalid_approval_does_not_write_partial_batch(self):
        first = trade()
        self.world["season"]["pending"]["transfer_review"] = [first, {**first, "trade_id": "duplicate"}]
        before = copy.deepcopy(self.world)
        with mock.patch.object(store, "load_world", return_value=copy.deepcopy(self.world)), mock.patch.object(store, "write_world") as write:
            with self.assertRaises(game_world.WorldError):
                game_world.apply_transfer_reviews("test", {"test-trade": True, "duplicate": True})
            write.assert_not_called()
        self.assertEqual(self.world, before)

    def test_three_for_one_and_three_for_two_in_both_directions(self):
        for other in (("bd",), ("bm", "bf")):
            for reverse in (False, True):
                world = small_world()
                for pid in other:
                    world["players"]["real::" + pid]["market_value"] = 120 / len(other)
                item = trade(other, ("ad", "am", "af"), team_a="b", team_b="a") if reverse else trade(("ad", "am", "af"), other)
                game_world._apply_trade(world, item)
                assert_rosters(self, world)

    def test_unsupported_three_for_three_and_four_for_one_rejected(self):
        for item in (trade(("ad", "am", "af"), ("bm", "bf", "bd")), trade(("ad", "am", "af", "ad"), ("bm",))):
            before = copy.deepcopy(self.world)
            with self.assertRaisesRegex(game_world.WorldError, "支持"):
                game_world._apply_trade(self.world, item)
            self.assertEqual(self.world, before)

    def test_generator_can_offer_three_player_packages(self):
        found = set()
        for seed in range(60):
            world = small_world()
            for pid in ("ad", "am", "af"):
                world["players"]["real::" + pid]["market_value"] = 20
            for pid in ("bm", "bf"):
                world["players"]["real::" + pid]["market_value"] = 40
            for item in transfers.generate_proposals(world, random.Random(seed), "winter", game_world._player_value):
                found.add(tuple(sorted((len(item["players_a"]), len(item["players_b"])))))
        self.assertTrue({(1, 3), (2, 3)} <= found)


class TransferPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fs_transfer_contract_")
        support.install_test_data_root(self.tmp)
        self.service = GameService(rng_factory=lambda: random.Random(21))
        self.service.create_save("trade")
        self.world = self.service.initialize_save("trade")
        self.world["season"]["week"] = 25

    def tearDown(self):
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(self.tmp))

    def test_window_lock_survives_database_reload(self):
        rows = transfers.generate_proposals(self.world, random.Random(9), "winter", game_world._player_value)
        self.assertTrue(rows)
        row = rows[0]
        self.world["season"]["pending"]["transfer_review"] = [row]
        store.write_world("trade", self.world)
        self.service.apply_transfer_reviews("trade", {row["trade_id"]: True})
        world = store.load_world("trade")
        reverse = {**row, "players_a": row["players_b"], "players_b": row["players_a"], "trade_id": "reverse", "week": 26}
        world["season"]["week"] = 26
        world["season"]["pending"]["transfer_review"] = [reverse]
        store.write_world("trade", world)
        before = json.dumps(store.load_world("trade"), sort_keys=True)
        with self.assertRaisesRegex(game_world.WorldError, "本转会窗"):
            self.service.apply_transfer_reviews("trade", {"reverse": True})
        self.assertEqual(json.dumps(store.load_world("trade"), sort_keys=True), before)
        assert_rosters(self, store.load_world("trade"))


class SwissRecordGraphTests(_Base):
    def board(self, world=None):
        model = swiss.swiss_graph(world or self.world)
        view = SwissBoard(world or self.world, model)
        view.resize(view.minimumSize())
        return view, model

    def test_high_win_pools_are_above_low_win_pools(self):
        view, model = self.board()
        for rnd in model["rounds"]:
            expected = sorted([tuple(b["record"]) for b in rnd["buckets"]], key=lambda rec: (-rec[0], rec[1]))
            blocks = sorted([b for b in view._pool_blocks if b["round"] == rnd["round"]], key=lambda b: b["y"])
            self.assertEqual([b["record"] for b in blocks], expected)
        self.assertEqual(len(view._pool_blocks), 9)

    def test_connections_are_record_transitions_not_individual_matches(self):
        view, _ = self.board()
        actual = {(e["from"], e["to"]) for e in view._edges}
        self.assertEqual(len(actual), 12)
        self.assertTrue({("pool-3-1-1", "pool-4-2-1"), ("pool-3-1-1", "pool-4-1-2"), ("pool-3-0-2", "pool-4-1-2")} <= actual)
        for edge in view._edges:
            source = next(b for b in view._pool_blocks if b["id"] == edge["from"])
            target = next(b for b in view._pool_blocks if b["id"] == edge["to"])
            w, l = source["record"]
            self.assertEqual(target["record"], (w + 1, l) if edge["style"] == "winner" else (w, l + 1))
            self.assertEqual(target["round"], source["round"] + 1)
            self.assertTrue(edge["arrow"])

    def test_outcomes_are_below_all_swiss_rounds_with_all_clubs_and_crests(self):
        view, _ = self.board()
        bottom = max(b["y"] + b["h"] for b in view._pool_blocks)
        for key in ("qualified-", "eliminated-"):
            rows = [n for n in view._nodes if n["id"].startswith(key)]
            self.assertEqual(len(rows), 8)
            self.assertTrue(all(n["y"] > bottom and n["rows"][0]["crest"] for n in rows))
        self.assertTrue(all(panel["y"] > bottom for panel in view._outcome_panels))

    def test_drawn_pool_routes_do_not_cross_match_cards(self):
        view, _ = self.board()
        image = QImage(view.minimumSize(), QImage.Format_ARGB32)
        view.render(image)
        self.assertEqual(len(view._painted_polylines), 12)
        for painted in view._painted_polylines:
            edge = next(e for e in view._edges if (e["from"], e["to"]) == (painted["from"], painted["to"]))
            self.assertEqual(painted["points"], edge["polyline"])
            for (x1, y1), (x2, y2) in zip(painted["points"], painted["points"][1:]):
                for n in view._nodes:
                    if x1 == x2:
                        hit = n["x"] < x1 < n["x"] + n["w"] and min(y1, y2) < n["y"] + n["h"] and max(y1, y2) > n["y"]
                    else:
                        hit = n["y"] < y1 < n["y"] + n["h"] and min(x1, x2) < n["x"] + n["w"] and max(x1, x2) > n["x"]
                    self.assertFalse(hit, (painted, n["id"]))

    def test_unknown_future_pools_are_not_invented(self):
        world = copy.deepcopy(self.world)
        world["season"]["fixtures"] = [f for f in world["season"]["fixtures"] if f.get("competition") != "starpath" or f.get("stage") in ("swiss_1", "swiss_2")]
        view, _ = self.board(world)
        self.assertEqual(len(view._pool_blocks), 3)
        self.assertEqual(len(view._edges), 2)
        self.assertFalse(any(n["terminal"] for n in view._nodes if "terminal" in n))

    def test_fit_and_zoom_do_not_clip_outcomes(self):
        view, _ = self.board()
        panel = GraphPanel(view)
        panel.resize(1040, 800)
        panel.show()
        QTest.qWait(50)
        for action in (panel.fit, panel.zoom_in, panel.zoom_in, panel.zoom_out):
            action()
            QTest.qWait(20)
            self.assertGreaterEqual(panel.scroll.height(), view.minimumHeight())
            self.assertEqual(panel.scroll.verticalScrollBar().maximum(), 0)
        panel.close()
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
