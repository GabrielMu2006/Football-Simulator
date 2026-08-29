"""SQLite 事务、并发与故障恢复测试（阶段 1b）。

验收口径（实施方案 §12.2）：
- 初始化、模拟周、审核、选秀为事务操作，故意抛错后周指针、比赛、统计、
  奖项和待办全部回滚；
- 同一存档的并发写入要么串行成功，要么清晰拒绝，不得静默覆盖；
- 写入中断（连接未提交即关闭）后重启读到的是上一个完整状态。
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from football_simulator.persistence import connection as db_connection
from football_simulator.persistence.save_repository import SaveRepository
from football_simulator.state import (
    _state_transaction,
    load_save_snapshot,
    simulate_next_week,
)

from tests.support import FreezeTestCase, advance_week, create_save, load_snapshot, load_state_json, state_path


def open_raw(save_name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(state_path(save_name)), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


class TransactionRollbackTests(FreezeTestCase):
    def test_exception_during_persist_rolls_back(self) -> None:
        save = "rollback_persist"
        create_save(save)
        advance_week(save)
        before = load_snapshot(save)
        self.assertEqual(before.current_week, 1)

        with mock.patch.object(
            SaveRepository, "persist_state", side_effect=RuntimeError("注入的持久化失败")
        ):
            with self.assertRaises(RuntimeError):
                simulate_next_week(save)

        after = load_snapshot(save)
        self.assertEqual(after.current_week, 1)
        self.assertEqual(len(after.simulated_weeks), 1)

    def test_exception_after_persist_rolls_back(self) -> None:
        # persist 已执行但 COMMIT 前失败：同样不允许半提交。
        save = "rollback_commit"
        create_save(save)
        with mock.patch.object(
            db_connection, "commit", side_effect=RuntimeError("注入的提交失败")
        ):
            with self.assertRaises(RuntimeError):
                advance_week(save)
        after = load_snapshot(save)
        self.assertEqual(after.current_week, 0)
        self.assertEqual(after.simulated_weeks, [])

    def test_failed_advance_keeps_matches_consistent(self) -> None:
        save = "rollback_matches"
        create_save(save)
        advance_week(save)
        with mock.patch.object(
            db_connection, "commit", side_effect=RuntimeError("注入的提交失败")
        ):
            with self.assertRaises(RuntimeError):
                advance_week(save)
        raw = load_state_json(save)
        self.assertEqual(raw["current_week"], 1)
        self.assertEqual(len(raw["simulated_weeks"]), 1)


class UncleanShutdownTests(FreezeTestCase):
    def test_close_without_commit_rolls_back(self) -> None:
        save = "crash"
        create_save(save)
        advance_week(save)
        before = load_state_json(save)
        transfers_before = len(before["transfer_history"])

        conn = open_raw(save)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO transfers (
                    season_number, week_number, window, trade_id, team_a, team_b,
                    team_a_players_json, team_b_players_json, team_a_total_value,
                    team_b_total_value, value_gap, approved, status, recalculated, reason
                ) VALUES (1, 99, '测试窗口', NULL, 'A队', 'B队', '[]', '[]', 0, 0, 0, 1, '测试', 0, '')
                """
            )
        finally:
            # 模拟进程中断：不提交、不回滚，直接关闭连接。
            conn.close()

        after = load_state_json(save)
        self.assertEqual(len(after["transfer_history"]), transfers_before)


class ConcurrencyTests(FreezeTestCase):
    def test_second_writer_is_rejected_while_transaction_holds_lock(self) -> None:
        save = "concurrent"
        create_save(save)
        first = SaveRepository.open(state_path(save).parent, save)
        first.begin()
        try:
            second = SaveRepository.open(state_path(save).parent, save)
            try:
                second._conn.execute("PRAGMA busy_timeout = 200")
                with self.assertRaises(sqlite3.OperationalError):
                    second.begin()
            finally:
                second.close()
        finally:
            first.commit()
            first.close()

        # 持锁方提交后，后续写入可以串行成功。
        winner = SaveRepository.open(state_path(save).parent, save)
        try:
            winner.begin()
            winner.commit()
        finally:
            winner.close()

    def test_serial_weeks_via_separate_connections(self) -> None:
        save = "serial"
        create_save(save)
        for expected_week in (1, 2, 3):
            simulate_next_week(save)
            self.assertEqual(load_snapshot(save).current_week, expected_week)


class SchemaGuardTests(FreezeTestCase):
    def test_rejects_future_schema_version(self) -> None:
        save = "future"
        create_save(save)
        conn = open_raw(save)
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()
        with self.assertRaises(db_connection.SaveDatabaseError):
            SaveRepository.open(state_path(save).parent, save)

    def test_empty_database_treated_as_uninitialized(self) -> None:
        save = "empty"
        create_save(save)
        conn = open_raw(save)
        try:
            conn.execute("DELETE FROM save_meta")
        finally:
            conn.close()
        repo = SaveRepository.open(state_path(save).parent, save)
        try:
            self.assertIsNone(repo.load_state(), "空元数据数据库应视为未初始化")
        finally:
            repo.close()
        with self.assertRaises(FileNotFoundError):
            load_save_snapshot(save)

    def test_partial_meta_raises_corruption_error(self) -> None:
        save = "partial_meta"
        create_save(save)
        conn = open_raw(save)
        try:
            conn.execute("DELETE FROM save_meta WHERE key = 'current_week'")
        finally:
            conn.close()
        repo = SaveRepository.open(state_path(save).parent, save)
        try:
            with self.assertRaises(db_connection.SaveDatabaseError):
                repo.load_state()
        finally:
            repo.close()


class MidSeasonReinitializeTests(FreezeTestCase):
    def test_reinitialize_discards_incomplete_season(self) -> None:
        save = "reinit"
        create_save(save)
        advance_week(save)
        advance_week(save)
        self.assertEqual(load_snapshot(save).current_week, 2)
        first_attempt_week1 = load_state_json(save)["simulated_weeks"][0]

        # 旧语义：赛季中途重新初始化会丢弃未完成赛季（不归档）并进入下一赛季编号。
        create_save(save)
        snap = load_snapshot(save)
        self.assertEqual(snap.season_number, 2)
        self.assertEqual(snap.current_week, 0)
        self.assertEqual(snap.simulated_weeks, [])

        advance_week(save)
        raw = load_state_json(save)
        self.assertEqual(raw["current_week"], 1)
        self.assertEqual(len(raw["simulated_weeks"]), 1)

        conn = open_raw(save)
        try:
            completed = conn.execute(
                "SELECT COUNT(*) c FROM matches WHERE status = 'completed'"
            ).fetchone()["c"]
            # 被放弃赛季（S1）的比赛行已清空，只剩新赛季第 1 周的 20 场。
            self.assertEqual(completed, 20, "被放弃赛季的比赛行必须被清空")
            total = conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
            self.assertEqual(total, 760, "新赛季赛程行应完整重建为 760 场")
        finally:
            conn.close()

        # 物化只包含新尝试的数据，不含被放弃赛季残留。
        self.assertEqual(len(raw["simulated_weeks"]), 1)
        self.assertNotEqual(
            raw["simulated_weeks"][0]["premier_matchdays"][0]["results"],
            first_attempt_week1["premier_matchdays"][0]["results"],
        )


class StableMatchIdTests(FreezeTestCase):
    def test_league_fixtures_precreated_with_stable_ids(self) -> None:
        save = "match_ids"
        create_save(save)
        conn = open_raw(save)
        try:
            rows = conn.execute("SELECT match_id, category, status FROM matches").fetchall()
            self.assertEqual(len(rows), 760, "两级联赛 38 轮 × 10 场应在建档时全部生成")
            self.assertTrue(all(row["status"] == "scheduled" for row in rows))
            ids = [row["match_id"] for row in rows]
            self.assertEqual(len(ids), len(set(ids)), "match_id 必须唯一")
            self.assertTrue(all(row["match_id"].startswith("m-1-") for row in rows))
        finally:
            conn.close()

        advance_week(save)
        conn = open_raw(save)
        try:
            completed = conn.execute("SELECT match_id FROM matches WHERE status = 'completed'").fetchall()
            scheduled = conn.execute("SELECT match_id FROM matches WHERE status = 'scheduled'").fetchall()
            self.assertEqual(len(completed), 20)
            self.assertEqual(len(scheduled), 740)
            appeared = conn.execute(
                """
                SELECT m.match_id, COUNT(*) AS rows FROM matches AS m
                JOIN player_match_stats AS pms ON pms.match_id = m.match_id
                WHERE m.status = 'completed'
                GROUP BY m.match_id
                """
            ).fetchall()
            self.assertEqual(len(appeared), 20)
            self.assertTrue(all(row["rows"] == 22 for row in appeared), "每场已完成比赛应有 22 条 appeared 行")
        finally:
            conn.close()

        # 赛季中 cup 比赛产生后 match_id 依然唯一且稳定（读取两次一致）。
        first_pass = self._collect_match_ids(save)
        second_pass = self._collect_match_ids(save)
        self.assertEqual(first_pass, second_pass)

    def _collect_match_ids(self, save: str) -> list:
        conn = open_raw(save)
        try:
            return [row["match_id"] for row in conn.execute("SELECT match_id FROM matches ORDER BY match_id")]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
