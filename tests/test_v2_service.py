"""应用服务允许动作/待办顺序/赛季边界。"""

from __future__ import annotations

import random
import unittest

from football_simulator.app.service import GameService, ServiceError
from football_simulator.persistence_v2 import store

from tests.support import FreezeTestCase


class ServiceStateTests(FreezeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = GameService(rng_factory=lambda: random.Random(3))
        self.service.create_save("state")
        self.service.initialize_save("state")

    def _set_pending(self, **pending) -> None:
        world = store.load_world("state")
        world["season"]["pending"].update(pending)
        store.write_world("state", world)

    def test_ability_review_is_primary_and_blocks_other_actions(self) -> None:
        self._set_pending(ability_review=[{"player_id": "x", "old_ability": 50, "new_ability": 55}])
        snapshot = self.service.snapshot("state")
        self.assertEqual(snapshot["primary_action"]["id"], "resolve_ability")
        self.assertFalse(snapshot["actions"]["actions"]["advance"])
        self.assertFalse(snapshot["actions"]["actions"]["run_draft"])
        with self.assertRaises(Exception):
            self.service.apply_draft("state")

    def test_draft_requires_ability_review_complete(self) -> None:
        self._set_pending(
            ability_review=[{"player_id": "x", "old_ability": 50, "new_ability": 55}],
            draft={"status": "awaiting_input", "candidates": [], "draft_order": []},
        )
        snapshot = self.service.snapshot("state")
        self.assertEqual(snapshot["primary_action"]["id"], "resolve_ability")
        with self.assertRaises(Exception):
            self.service.apply_draft("state")

    def test_transfer_requires_ability_and_draft_complete(self) -> None:
        self._set_pending(
            transfer_review=[{"trade_id": "t1", "team_a": "a", "team_b": "b", "players_a": ["p1"], "players_b": ["p2"], "value_gap": 0}],
            draft={"status": "awaiting_input", "candidates": [], "draft_order": []},
        )
        snapshot = self.service.snapshot("state")
        self.assertEqual(snapshot["primary_action"]["id"], "run_draft")
        with self.assertRaises(Exception):
            self.service.apply_transfer_reviews("state", {})

    def test_season_end_requires_explicit_next_season(self) -> None:
        world = store.load_world("state")
        world["season"]["season_complete"] = True
        store.write_world("state", world)
        snapshot = self.service.snapshot("state")
        self.assertEqual(snapshot["primary_action"]["id"], "start_next_season")
        self.assertFalse(snapshot["actions"]["actions"]["advance"])
        with self.assertRaises(Exception):
            self.service.advance_week("state")
        self.assertEqual(store.load_world("state")["season_number"], 1)

    def test_duplicate_save_names_are_rejected(self) -> None:
        with self.assertRaises(Exception):
            self.service.create_save("state")
        with self.assertRaises(ServiceError):
            self.service.create_save("bad/name")

    def test_watchlist_requires_known_entity(self) -> None:
        with self.assertRaises(ServiceError):
            self.service.toggle_watch("state", "not-an-entity")
        team_id = next(iter(store.load_world("state")["teams"]))
        self.service.toggle_watch("state", team_id)
        self.assertIn(team_id, store.load_world("state")["settings"]["watchlist"])


if __name__ == "__main__":
    unittest.main()
