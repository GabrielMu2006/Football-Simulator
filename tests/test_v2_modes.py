"""观察三模式一致性、暂停类型与事实故事去重。"""

from __future__ import annotations

import random
import unittest

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store

from tests.support import FreezeTestCase


class ModeConsistencyTests(FreezeTestCase):
    def _make(self, name: str) -> GameService:
        service = GameService(rng_factory=lambda: random.Random(2026))
        service.create_save(name)
        service.initialize_save(name)
        return service

    def test_auto_and_manual_produce_same_facts(self) -> None:
        manual = self._make("manual")
        auto = self._make("auto")
        manual.set_mode("manual", "manual")
        auto.set_mode("auto", "auto")
        for _ in range(5):
            manual.advance_week("manual")
            auto.advance_week("auto")
        manual_world = store.load_world("manual")
        auto_world = store.load_world("auto")
        manual_scores = [
            (f["match_id"], f["home_goals"], f["away_goals"], f.get("winner_team_id"))
            for f in manual_world["season"]["fixtures"]
            if f["status"] == "completed"
        ]
        auto_scores = [
            (f["match_id"], f["home_goals"], f["away_goals"], f.get("winner_team_id"))
            for f in auto_world["season"]["fixtures"]
            if f["status"] == "completed"
        ]
        self.assertEqual(manual_scores, auto_scores)
        manual_facts = {
            f["match_id"]: {pid: (fact["goals"], fact["assists"]) for pid, fact in f["player_facts"].items()}
            for f in manual_world["season"]["fixtures"]
            if f["status"] == "completed"
        }
        auto_facts = {
            f["match_id"]: {pid: (fact["goals"], fact["assists"]) for pid, fact in f["player_facts"].items()}
            for f in auto_world["season"]["fixtures"]
            if f["status"] == "completed"
        }
        self.assertEqual(manual_facts, auto_facts)

    def test_manual_mode_pauses_after_each_week(self) -> None:
        service = self._make("pause")
        service.set_mode("pause", "manual")
        result = service.advance_week("pause")
        self.assertIsNotNone(result["pause"])
        self.assertEqual(result["pause"]["type"], "week")

    def test_default_is_key_pause_and_normal_week_does_not_pause(self) -> None:
        service = self._make("key")
        self.assertEqual(service.snapshot("key")["mode"], "key_pause")
        result = service.advance_week("key")
        self.assertIsNone(result["pause"])
        self.assertEqual(result["week"], 1)

    def test_story_events_are_stable_and_evidence_linked(self) -> None:
        service = self._make("stories")
        for _ in range(5):
            service.advance_week("stories")
        world = store.load_world("stories")
        events = world["story_events"]
        self.assertTrue(events)
        first_ids = [event["event_id"] for event in events]
        self.assertEqual(len(first_ids), len(set(first_ids)), "事件 ID 必须唯一")
        # 读取页面/重新加载不新增事件。
        service.snapshot("stories")
        service.load_world("stories")
        again = store.load_world("stories")
        self.assertEqual([event["event_id"] for event in again["story_events"]], first_ids)
        review = queries.multi_week_review(again, again["season"]["week"] - 1, 4)
        self.assertEqual(len(review["weeks"]), 4)
        self.assertEqual(review["weeks"][-1]["week"], again["season"]["week"] - 1)
        self.assertTrue(any(item["match_count"] > 0 for item in review["weeks"]))
        for event in again["story_events"]:
            self.assertIn(event["type"], {"match_result", "champion", "qualification", "ranking", "comeback", "transition", "transfer"})
            self.assertNotIn("xG", event["text"])
            self.assertNotIn("采访", event["text"])
            if event["type"] == "match_result":
                self.assertIsNotNone(event["match_id"])
            elif event.get("type") in ("champion", "qualification", "ranking"):
                self.assertTrue(event.get("match_id") or (event.get("payload") or {}).get("route"))


if __name__ == "__main__":
    unittest.main()
