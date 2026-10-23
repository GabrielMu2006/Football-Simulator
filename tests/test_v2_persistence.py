"""完整导出包、导入原子性、旧格式拒绝与只读查询。"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.app import queries
from football_simulator.game import world as game_world
from football_simulator.persistence_v2 import store

from tests import support
from tests.support import FreezeTestCase


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ExportImportTests(FreezeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = GameService(rng_factory=lambda: random.Random(77))
        self.service.create_save("origin")
        self.service.initialize_save("origin")

    def test_export_package_has_database_config_manifest(self) -> None:
        target = self._tmp_path / "out" / "origin.fsarchive"
        exported = self.service.export_save("origin", target)
        self.assertTrue(exported.exists())
        with zipfile.ZipFile(exported) as archive:
            names = set(archive.namelist())
            self.assertIn(store.DB_FILE_NAME, names)
            self.assertIn(runtime.SAVE_CONFIG_FILE_NAME, names)
            self.assertIn(store.MANIFEST_FILE_NAME, names)
            manifest = json.loads(archive.read(store.MANIFEST_FILE_NAME))
            packaged_db_sha = hashlib.sha256(archive.read(store.DB_FILE_NAME)).hexdigest()
        self.assertEqual(manifest["format"], store.FORMAT_NAME)
        self.assertEqual(manifest["format_version"], store.FORMAT_VERSION)
        self.assertEqual(manifest["rules_version"], "2026-10-20.2")
        self.assertEqual(manifest["save_name"], "origin")
        self.assertEqual(manifest["database_sha256"], packaged_db_sha)

    def test_import_under_new_name_continues_same_world(self) -> None:
        package = self.service.export_save("origin", self._tmp_path / "origin.fsarchive")
        imported = self.service.import_save(package, "copy")
        self.assertTrue((imported / store.DB_FILE_NAME).exists())
        original_world = store.load_world("origin")
        copied_world = store.load_world("copy")
        self.assertEqual(copied_world["world_id"], original_world["world_id"])
        self.assertEqual(copied_world["season_number"], original_world["season_number"])
        self.assertEqual(set(copied_world["teams"]), set(original_world["teams"]))
        # 导入后可以继续推进，且事实进入新的存档。
        self.service.advance_week("copy")
        after = store.load_world("copy")
        self.assertEqual(after["season"]["week"], 2)
        self.assertEqual(len([f for f in after["season"]["fixtures"] if f["status"] == "completed"]), 20)

    def test_duplicate_target_is_rejected_without_changing_existing(self) -> None:
        package = self.service.export_save("origin", self._tmp_path / "origin.fsarchive")
        before = store.load_world("origin")
        with self.assertRaises(FileExistsError):
            self.service.import_save(package, "origin")
        after = store.load_world("origin")
        self.assertEqual(before["world_id"], after["world_id"])
        self.assertEqual(before["version"], after["version"])

    def test_corrupted_package_is_rejected(self) -> None:
        package = self.service.export_save("origin", self._tmp_path / "origin.fsarchive")
        corrupt = self._tmp_path / "corrupt.fsarchive"
        with zipfile.ZipFile(package) as source, zipfile.ZipFile(corrupt, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == store.DB_FILE_NAME:
                    data = data[:-32] + b"0" * 32
                target.writestr(name, data)
        with self.assertRaises(store.SaveFormatError):
            self.service.import_save(corrupt, "broken")
        self.assertFalse(runtime.save_exists("broken"))

    def test_bare_sqlite_and_unknown_zip_are_rejected(self) -> None:
        bare = self._tmp_path / "some.sqlite3"
        sqlite3.connect(str(bare)).close()
        with self.assertRaises(store.SaveFormatError):
            self.service.import_save(bare, "legacy")
        unknown = self._tmp_path / "unknown.fsarchive"
        with zipfile.ZipFile(unknown, "w") as archive:
            archive.writestr("readme.txt", "not a save")
        with self.assertRaises(store.SaveFormatError):
            self.service.import_save(unknown, "unknown")

    def test_install_failure_is_atomic(self) -> None:
        package = self.service.export_save("origin", self._tmp_path / "origin.fsarchive")
        with mock.patch("football_simulator.persistence_v2.store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.import_save(package, "atomic")
        self.assertFalse(runtime.save_exists("atomic"))
        self.assertTrue(runtime.save_exists("origin"))
        self.assertEqual(store.load_world("origin")["save_name"], "origin")

    def test_old_database_is_not_migrated_or_deleted(self) -> None:
        legacy = self._tmp_path / "saves" / "legacy"
        legacy.mkdir(parents=True)
        connection = sqlite3.connect(str(legacy / store.DB_FILE_NAME))
        connection.execute("CREATE TABLE old_state (id INTEGER PRIMARY KEY, payload TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaises(store.SaveFormatError):
            store.load_world("legacy")
        self.assertTrue((legacy / store.DB_FILE_NAME).exists())
        self.assertEqual(
            [item for item in self.service.list_saves() if item["save_name"] == "legacy"][0]["compatible"],
            False,
        )


class ReadOnlyQueryTests(FreezeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = GameService(rng_factory=lambda: random.Random(5))
        self.service.create_save("readonly")
        self.service.initialize_save("readonly")
        self.service.advance_week("readonly")
        self.db_path = store.database_path("readonly")

    def test_queries_do_not_write_or_consume_randomness(self) -> None:
        before = _sha(self.db_path)
        counter = {"calls": 0}

        def provider():
            counter["calls"] += 1
            return random.Random(1)

        self.service._rng_factory = provider
        try:
            world = self.service.load_world("readonly")
            snapshot = self.service.snapshot("readonly")
            queries.dashboard(world, "readonly")
            queries.weekly_report(world, 1)
            queries.matches_page(world, "readonly")
            queries.competitions_page(world)
            queries.competition_detail(world, "readonly", "premier")
            queries.season_overview(world)
            queries.teams_page(world, "readonly")
            queries.players_page(world, "readonly")
            queries.history_page(world, "readonly")
            queries.transfers_page(world)
            queries.draft_page(world)
            team_id = next(iter(world["teams"]))
            player_id = next(iter(world["players"]))
            queries.team_profile(world, "readonly", team_id)
            queries.player_profile(world, "readonly", player_id)
            completed = next(f for f in world["season"]["fixtures"] if f["status"] == "completed")
            queries.match_detail(world, "readonly", completed["match_id"])
        finally:
            self.service._rng_factory = lambda: random.Random(5)
        self.assertEqual(counter["calls"], 0, "只读查询不得消费随机源")
        self.assertEqual(_sha(self.db_path), before, "只读查询不得改写数据库")

    def test_repeated_reads_keep_same_standings_and_match_ids(self) -> None:
        world = self.service.load_world("readonly")
        first = queries.competition_detail(world, "readonly", "premier")
        fingerprint = [(row["team_id"], row["points"], row["goal_diff"]) for row in first["table"]]
        match_ids = sorted(fixture["match_id"] for fixture in world["season"]["fixtures"])
        for _ in range(5):
            again = self.service.load_world("readonly")
            table = queries.competition_detail(again, "readonly", "premier")["table"]
            self.assertEqual([(row["team_id"], row["points"], row["goal_diff"]) for row in table], fingerprint)
            self.assertEqual(sorted(fixture["match_id"] for fixture in again["season"]["fixtures"]), match_ids)


if __name__ == "__main__":
    unittest.main()
