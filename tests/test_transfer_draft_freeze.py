"""转会与选秀冻结测试（阶段 0）。

锁定内容：转会窗口触发周与数量、批准/拒绝/系统重算状态语义、转会历史
留痕字段、选秀候选池与初始身价、能力审核按姓名决策的当前行为。
"""

from __future__ import annotations

import unittest

from football_simulator.data import real_player_id
from football_simulator.state import apply_ability_review_decisions, apply_draft_prospects, apply_transfer_review_decisions

from tests.support import (
    FreezeTestCase,
    advance_week,
    create_save,
    load_snapshot,
    load_state_json,
    run_weeks,
)

TRANSFER_HISTORY_FIELDS = {
    "season_number",
    "week_number",
    "window",
    "trade_id",
    "team_a",
    "team_b",
    "team_a_players",
    "team_b_players",
    "team_a_total_value",
    "team_b_total_value",
    "value_gap",
    "approved",
    "status",
    "recalculated",
    "reason",
}


class TransferReviewTests(FreezeTestCase):
    def _run_to_first_winter(self, save: str):
        create_save(save)
        run_weeks(save, 24)
        advance_week(save)  # 第 25 周（冬窗第 1 周）产生转会待办
        return load_snapshot(save)

    def test_reject_all_transfers_marks_rejected(self) -> None:
        save = "reject_all"
        snap = self._run_to_first_winter(save)
        self.assertTrue(snap.pending_transfer_review)
        decisions = {item["trade_id"]: False for item in snap.pending_transfer_review}
        apply_transfer_review_decisions(save, decisions)

        snap = load_snapshot(save)
        self.assertEqual(snap.pending_transfer_review, [])
        self.assertRosterIntegrity(snap)
        state_json = load_state_json(save)
        self.assertTrue(state_json["transfer_history"])
        for row in state_json["transfer_history"]:
            self.assertFalse(row["approved"])
            self.assertEqual(row["status"], "玩家拒绝")

    def test_transfer_history_row_shape(self) -> None:
        save = "history_shape"
        self._run_to_first_winter(save)
        snap = load_snapshot(save)
        apply_transfer_review_decisions(save, {item["trade_id"]: True for item in snap.pending_transfer_review})
        state_json = load_state_json(save)
        for row in state_json["transfer_history"]:
            self.assertTrue(TRANSFER_HISTORY_FIELDS.issubset(row.keys()), f"转会历史缺少字段：{TRANSFER_HISTORY_FIELDS - set(row)}")
            self.assertEqual(row["season_number"], 1)
            self.assertIn(row["week_number"], (25, 26, 27))
            self.assertTrue(row["window"])

    def test_apply_without_pending_raises(self) -> None:
        create_save("no_pending")
        with self.assertRaises(ValueError):
            apply_transfer_review_decisions("no_pending", {})


class DraftTests(FreezeTestCase):
    def _run_to_draft(self, save: str):
        create_save(save)
        run_weeks(save, 48)
        advance_week(save)  # 第 49 周产生能力审核 + 选秀待办
        snap = load_snapshot(save)
        apply_ability_review_decisions(save, {item["name"]: True for item in snap.pending_ability_review})
        return load_snapshot(save)

    def test_draft_awaiting_input_and_completion(self) -> None:
        save = "draft_flow"
        snap = self._run_to_draft(save)
        self.assertEqual(snap.pending_draft.get("status"), "awaiting_input")
        pool_before = len(snap.real_player_pool)
        target_count = int(snap.pending_draft.get("candidate_count", 0))
        self.assertGreaterEqual(target_count, 6)
        self.assertLessEqual(target_count, 10)

        apply_draft_prospects(save, [])
        snap = load_snapshot(save)
        self.assertEqual(snap.pending_draft, {})
        self.assertEqual(len(snap.real_player_pool), pool_before + target_count)

        state_json = load_state_json(save)
        draft_log = state_json["last_draft"]
        self.assertEqual(draft_log["season_number"], 1)
        self.assertEqual(draft_log["target_count"], target_count)
        self.assertEqual(len(draft_log["prospects"]), target_count)
        self.assertEqual(len(draft_log["results"]), target_count)
        self.assertEqual(draft_log["undrafted"], [])
        names = [result["name"] for result in draft_log["results"]]
        self.assertEqual(len(names), len(set(names)))
        for result in draft_log["results"]:
            self.assertEqual(result["market_value"], 30.0)
            self.assertIn(result["position"], {"GK", "DF", "MF", "FW"})
        self.assertGreater(state_json["draft_pool_index"], 0)
        self.assertRosterIntegrity(snap)

        # 新球员进入存档池，ID 由姓名 slug 生成且唯一。
        new_names = {result["name"] for result in draft_log["results"]}
        profiles_by_id = {real_player_id(profile.name): profile for profile in snap.real_player_pool}
        for name in new_names:
            self.assertIn(real_player_id(name), profiles_by_id)

    def test_draft_blocked_before_ability_review_resolved(self) -> None:
        save = "draft_order"
        create_save(save)
        run_weeks(save, 48)
        advance_week(save)
        # 能力审核未处理时选秀必须先被拒绝。
        with self.assertRaises(ValueError):
            apply_draft_prospects(save, [])


class AbilityReviewTests(FreezeTestCase):
    def test_review_items_shape_and_key_by_name(self) -> None:
        save = "ability"
        create_save(save)
        run_weeks(save, 48)
        advance_week(save)
        snap = load_snapshot(save)
        pool_names = {profile.name for profile in snap.real_player_pool}
        expected_count = max(1, int(len(snap.real_player_pool) * 0.4))
        self.assertEqual(len(snap.pending_ability_review), expected_count)
        for item in snap.pending_ability_review:
            self.assertIn(item["name"], pool_names)
            self.assertIn(item["position"], {"GK", "DF", "MF", "FW"})
            self.assertEqual(item["delta"], item["new_ability"] - item["old_ability"])
            self.assertGreaterEqual(item["new_ability"], 60)
            self.assertLessEqual(item["new_ability"], 88)

        # 拒绝全部：池内能力保持不变，条目标记未通过。
        apply_ability_review_decisions(save, {item["name"]: False for item in snap.pending_ability_review})
        snap = load_snapshot(save)
        for item in load_state_json(save)["last_ability_review"]:
            self.assertFalse(item["approved"])


if __name__ == "__main__":
    unittest.main()
