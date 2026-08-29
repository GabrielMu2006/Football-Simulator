"""持久化与存档路径测试（阶段 1）。

阶段 1 已用 SQLite 替代整份 JSON 覆盖写：
- 存档目录只包含 ``config.json`` 与 ``save.sqlite3``，不再写 ``state.json``；
- 存档名使用白名单（中文/字母/数字/空格/下划线/连字符，1–64 字符），
  拒绝路径分隔符、盘符形式、Windows 保留设备名；
- 删除只允许 save_root 的直接子目录，并拒绝符号链接与根目录本身；
- 旧 ``state.json`` 不被探测、不迁移：只有数据库的存档无法加载。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from football_simulator.persistence.connection import SaveDatabaseError
from football_simulator.runtime import (
    create_save_directory,
    delete_save_directory,
    normalize_save_name,
    save_root,
)
from football_simulator.state import load_save_snapshot

from tests.support import (
    FreezeTestCase,
    create_save,
    load_snapshot,
    load_state_json,
    run_weeks,
    state_path,
)


class SavePathBoundaryTests(FreezeTestCase):
    def test_save_root_redirect_active(self) -> None:
        # 所有测试写入都被重定向到临时目录，绝不触碰项目 saves/。
        self.assertEqual(save_root(), self._tmp_path)

    def test_normalize_save_name_accepts_valid_names(self) -> None:
        for name in ("default", "我的存档", "save-01", "My Save 2", "联赛2026"):
            self.assertEqual(normalize_save_name(name), name)

    def test_normalize_save_name_rejects_invalid_names(self) -> None:
        for bad_name in (
            "", "  ", ".", "..", "a/b", "a\\b", "..\\evil", "../outside",
            "con", "nul.txt", "COM1", "a.b", "-abc", ".hidden", "a" * 65,
        ):
            with self.assertRaises(ValueError, msg=f"应拒绝存档名：{bad_name!r}"):
                normalize_save_name(bad_name)

    def test_save_directory_layout(self) -> None:
        create_save("layout")
        save_dir = save_root() / "layout"
        self.assertTrue((save_dir / "save.sqlite3").exists())
        self.assertTrue((save_dir / "config.json").exists())
        self.assertFalse((save_dir / "state.json").exists(), "阶段 1 起不再写 state.json")

    def test_legacy_state_json_is_not_probed(self) -> None:
        # 旧格式不迁移：目录里只有 state.json 时视为未初始化。
        save_dir = save_root() / "legacy"
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "state.json").write_text('{"season_number": 3, "current_week": 10}', encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            load_save_snapshot("legacy")

    def test_delete_only_direct_children_and_no_symlinks(self) -> None:
        root = save_root()
        target = create_save_directory("victim")
        self.assertTrue(target.exists())
        delete_save_directory("victim")
        self.assertFalse(target.exists())

        # 符号链接：即使名字合法也拒绝删除。
        outside = self._tmp_path / "outside_dir"
        outside.mkdir(exist_ok=True)
        link = root / "链接存档"
        os.symlink(outside, link)
        try:
            with self.assertRaises(ValueError):
                delete_save_directory("链接存档")
        finally:
            link.unlink()

    def test_save_directory_lifecycle(self) -> None:
        path = create_save_directory("lifecycle")
        self.assertTrue(path.exists())
        with self.assertRaises(ValueError):
            create_save_directory("lifecycle")
        delete_save_directory("lifecycle")
        self.assertFalse(path.exists())
        with self.assertRaises(FileNotFoundError):
            delete_save_directory("lifecycle")


class StateRoundTripTests(FreezeTestCase):
    def test_initialize_creates_database(self) -> None:
        create_save("roundtrip")
        self.assertTrue(state_path("roundtrip").exists())

    def test_round_trip_after_weeks(self) -> None:
        save = "roundtrip"
        create_save(save)
        run_weeks(save, 5)

        first = load_snapshot(save)
        raw = load_state_json(save)
        again = load_save_snapshot(save)

        self.assertEqual(again.current_week, first.current_week)
        self.assertEqual(again.season_number, first.season_number)
        self.assertEqual(len(again.premier_table), len(first.premier_table))
        self.assertEqual(len(again.player_stats), len(first.player_stats))
        self.assertEqual(raw["current_week"], 5)
        self.assertEqual(len(raw["simulated_weeks"]), 5)
        self.assertEqual(len(raw["premier_teams"]), 20)
        self.assertEqual(len(raw["second_teams"]), 20)
        # player_registry 只登记真实球员（赛季 1 池为 50 人）。
        self.assertEqual(len(raw["player_registry"]), 50)

    def test_load_missing_save_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_save_snapshot("missing_save")


if __name__ == "__main__":
    unittest.main()
