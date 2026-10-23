"""Real trophy files, live Qt routes, missing assets and resource reload regressions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from football_simulator.app import records
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui import trophies
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.honors import TrophyGrid
from tests import support

ROOT = Path(__file__).resolve().parent.parent


class RealTrophyAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = support.ensure_qapp()

    def setUp(self):
        trophies.clear_cache()
        self.tmp = tempfile.TemporaryDirectory(prefix="fs_trophy_contract_")
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        trophies.clear_cache()
        self.tmp.cleanup()

    def _fixture(self):
        image = self.dir / "premier.png"
        shutil.copy2(trophies.trophy_dir() / "premier.png", image)
        data = {"version": 1, "assets": {"premier": {
            "path": image.name, "status": "ready", "width": 1254, "height": 1254,
        }}}
        path = self.dir / "manifest.json"
        path.write_text(json.dumps(data))
        return path, image, data

    def test_eight_real_files_match_manifest_and_decode_at_two_pixel_ratios(self):
        seen = set()
        for cid in trophies.TROPHY_IDS:
            with self.subTest(cid=cid):
                status = trophies.asset_status(cid)
                self.assertTrue(status["ready"], status["reason"])
                entry = status["entry"]
                digest = hashlib.sha256(status["path"].read_bytes()).hexdigest()
                self.assertEqual(digest, entry["sha256"])
                seen.add(digest)
                image = status["pixmap"].toImage()
                self.assertTrue(image.hasAlphaChannel())
                self.assertEqual(image.size().width(), image.size().height())
                self.assertGreaterEqual(image.width(), 1024)
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                for size in (64, 128, 256, 320):
                    for dpr in (1.0, 2.0):
                        pix = trophies.trophy_pixmap(cid, size, dpr)
                        self.assertFalse(pix.isNull())
                        self.assertEqual(pix.devicePixelRatio(), dpr)
                        self.assertLessEqual(pix.deviceIndependentSize().width(), size)
        self.assertEqual(len(seen), 8)
        self.assertFalse(trophies.asset_status("playoff")["ready"])

    def test_repeated_paints_reuse_decoded_sources(self):
        with patch.object(trophies.hashlib, "sha256", wraps=hashlib.sha256) as digest:
            for size in (64, 128, 64, 128):
                self.assertIsNotNone(trophies.trophy_pixmap("premier", size))
            self.assertEqual(digest.call_count, 1)

    def test_image_replacement_invalidates_scaled_cache_without_manifest_edit(self):
        path, image, _ = self._fixture()
        first = trophies.trophy_pixmap("premier", 128, path=path)
        shutil.copyfile(trophies.trophy_dir() / "second.png", image)
        second = trophies.trophy_pixmap("premier", 128, path=path)
        self.assertNotEqual(first.cacheKey(), second.cacheKey())

    def test_unsupported_manifest_bad_entry_and_wrong_dimensions_are_safe(self):
        path, _, data = self._fixture()
        data["version"] = 99
        path.write_text(json.dumps(data))
        self.assertIsNone(trophies.trophy_pixmap("premier", path=path))
        data["version"] = 1
        data["assets"]["premier"] = "not an object"
        path.write_text(json.dumps(data))
        self.assertFalse(trophies.asset_status("premier", path)["ready"])
        path, _, data = self._fixture()
        data["assets"]["premier"]["width"] = 1
        path.write_text(json.dumps(data))
        self.assertIn("尺寸", trophies.asset_status("premier", path)["reason"])

    def test_unreadable_image_is_a_fallback_not_a_page_exception(self):
        path, image, _ = self._fixture()
        original = Path.read_bytes
        def read(target):
            if target.resolve() == image.resolve():
                raise PermissionError("image unavailable")
            return original(target)
        with patch.object(Path, "read_bytes", read):
            self.assertFalse(trophies.asset_status("premier", path)["ready"])


class TrophyGamePagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fs_trophy_game_")
        support.install_test_data_root(cls.tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService()
        cls.service.import_save(ROOT / "tests/fixtures_vnext/s3-finalized.fsarchive", "trophy-ui")
        cls.window = MainWindow(cls.service)
        cls.window.open_save("trophy-ui")
        cls.window.resize(1440, 900)
        cls.window.show()
        QTest.qWait(60)
        cls.world = store.load_world("trophy-ui")
        cls.team_id = next(iter(cls.world["season"]["championships"].values()))["champion_team_id"]
        real_players = [pid for pid, p in cls.world["players"].items() if p.get("is_real")]
        cls.player_id = max(real_players, key=lambda pid: len(records.player_honors(cls.world, pid)["championships"]))

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def _page(self, route, **params):
        self.window.navigate(route, **params)
        QTest.qWait(35)
        self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
        return self.window.stack.currentWidget()

    def test_catalog_and_gallery_show_all_eight_real_trophies(self):
        for route in ("competitions", "honors"):
            page = self._page(route)
            images = page.findChildren(trophies.TrophyImage)
            self.assertEqual({i.competition_id for i in images}, set(trophies.TROPHY_IDS))
            # 荣誉馆首屏新增“最新荣誉”缩略图，八座主陈列仍然齐全。
            self.assertGreaterEqual(len(images), 8)
            self.assertTrue(all(i.property("assetReady") for i in images))

    def test_all_eight_competition_headers_and_history_use_correct_image(self):
        for cid in trophies.TROPHY_IDS:
            page = self._page("competition", competition_id=cid)
            images = page.findChildren(trophies.TrophyImage)
            self.assertTrue(images)
            # 页头奖杯与（已结束时）冠军卡片使用同一张赛事奖杯，不得串图。
            self.assertEqual({i.competition_id for i in images}, {cid})
            self.assertTrue(images[0].property("assetReady"))
        page = self._page("history_competition", competition_id="aureole", season=2)
        self.assertEqual(page.findChild(trophies.TrophyImage).competition_id, "aureole")

    def test_team_unearned_images_are_muted_and_keep_explicit_text(self):
        page = self._page("team_honors", team_id=self.team_id)
        expected = records.team_honors(self.world, self.team_id)
        images = page.findChildren(trophies.TrophyImage)
        self.assertEqual(len(images), 8)
        for image in images:
            title = next(t for t in expected["titles"] if t["competition_id"] == image.competition_id)
            self.assertEqual(image.muted, not title["champion"])
        labels = [w.text() for w in page.findChildren(QLabel)]
        self.assertIn("尚未夺冠", labels)
        self.assertTrue(any("次" in s for s in labels))

    def test_player_medals_use_actual_championship_competitions_and_links(self):
        page = self._page("player_honors", player_id=self.player_id)
        expected = records.player_honors(self.world, self.player_id)["championships"]
        # The current player hall groups newest titles first; preserve each medal.
        self.assertCountEqual([i.competition_id for i in page.findChildren(trophies.TrophyImage)], [r["competition_id"] for r in expected])
        button = next(b for b in page.findChildren(QPushButton) if b.text() == "查看该届赛事")
        page.scroll.ensureWidgetVisible(button)
        QTest.mouseClick(button, Qt.LeftButton)
        QTest.qWait(30)
        self.assertIn(self.window._current_route[0], ("competition", "history_competition"))
        self.window.go_back()
        self.assertEqual(self.window._current_route, ("player_honors", {"player_id": self.player_id}))

    def test_missing_images_do_not_hide_championship_facts_and_f5_recovers(self):
        missing = Path(self.tmp) / "missing-manifest.json"
        with patch.object(trophies, "manifest_path", return_value=missing):
            page = self._page("team_honors", team_id=self.team_id)
            self.assertFalse(any(i.property("assetReady") for i in page.findChildren(trophies.TrophyImage)))
            self.assertTrue(any("次" in label.text() for label in page.findChildren(QLabel)))
        self.window.refresh_all()
        QTest.qWait(40)
        page = self.window.stack.currentWidget()
        self.assertTrue(all(i.property("assetReady") for i in page.findChildren(trophies.TrophyImage)))

    def test_three_sizes_and_continuous_shrink_keep_images_in_cards(self):
        evidence = os.environ.get("FS_TROPHY_EVIDENCE")
        routes = [("honors", {}), ("team_honors", {"team_id": self.team_id}),
                  ("player_honors", {"player_id": self.player_id}), ("competitions", {}),
                  ("competition", {"competition_id": "premier"}), ("competition", {"competition_id": "aureole"})]
        for width, height in ((1280, 800), (1440, 900), (1680, 980), (1280, 800)):
            self.window.resize(width, height)
            for route, params in routes:
                page = self._page(route, **params)
                self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0, (width, route))
                for image in page.findChildren(trophies.TrophyImage):
                    self.assertTrue(image.parentWidget().contentsRect().contains(image.geometry()), (width, route, image.geometry()))
                if evidence:
                    dest = Path(evidence)
                    dest.mkdir(parents=True, exist_ok=True)
                    self.window.grab().save(str(dest / f"{route}-{params.get('competition_id', '')}-{width}.png"))
        page = self._page("team_honors", team_id=self.team_id)
        grid = page.findChild(TrophyGrid)
        self.window.resize(1680, 980)
        QTest.qWait(30)
        wide = grid._columns
        self.window.resize(1280, 800)
        QTest.qWait(30)
        self.assertLessEqual(grid._columns, wide)


def tearDownModule():
    support.cleanup_qt_widgets()
