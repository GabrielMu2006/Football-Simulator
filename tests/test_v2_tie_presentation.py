"""UIR2-001/002 回归：两回合范围、归档 leg、比分和链接。"""

from __future__ import annotations

import hashlib
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.main_window import MainWindow
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures_vnext"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TiePresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir2_tie_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(20261020))
        cls.service.import_save(FIXTURES / "s3-finalized.fsarchive", "s3tie")
        cls.service.import_save(FIXTURES / "s2-week25.fsarchive", "s2tie")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_two_leg_stage_whitelist(self) -> None:
        for competition, stage in (
            ("aureole", "quarterfinal_leg_1"),
            ("aureole", "final_leg_2"),
            ("starpath", "semifinal_leg_1"),
            ("playoff", "promotion_semifinal_leg_2"),
            ("playoff", "promotion_final_leg_1"),
        ):
            self.assertIsNotNone(queries.two_leg_stage_base(competition, stage), (competition, stage))
        for competition, stage in (
            ("premier", "round_1"),
            ("second", "round_20"),
            ("aureole", "group_a"),
            ("starpath", "swiss_1"),
            ("confluence", "quarterfinal"),
            ("ember", "grand_final"),
            ("zenith", "final"),
            ("playoff", "promotion_quarterfinal"),
        ):
            self.assertIsNone(queries.two_leg_stage_base(competition, stage), (competition, stage))

    def test_league_samples_have_no_tie(self) -> None:
        s3 = store.load_world("s3tie")
        s2 = store.load_world("s2tie")
        for world, save, match_id in (
            (s3, "s3tie", "m02-00001"),
            (s3, "s3tie", "m03-00001"),
            (s2, "s2tie", "m02-00001"),
        ):
            detail = queries.match_detail(world, save, match_id)
            self.assertIsNotNone(detail, (save, match_id))
            self.assertIsNone(detail["tie"], (save, match_id))
            self.assertNotIn("总比分", " ".join(detail["fixture"].keys()))
        # Dashboard focus from the same league must not turn the normal double round-robin
        # into a two-leg tie.
        self.window.open_save("s2tie")
        QTest.qWait(100)
        self.window.navigate("dashboard")
        QTest.qWait(120)
        labels = [label.text() for label in self.window._pages["dashboard"].findChildren(__import__("PySide6").QtWidgets.QLabel) if label.text()]
        self.assertFalse(any("第 2 回合" in text or "两回合总比分" in text for text in labels))

    def test_archived_legs_and_scores(self) -> None:
        world = store.load_world("s3tie")
        path = store.database_path("s3tie")
        before = sha(path)
        first = queries.match_detail(world, "s3tie", "m02-00919")
        second = queries.match_detail(world, "s3tie", "m02-00920")
        self.assertEqual([leg["leg"] for leg in first["tie"]["legs"]], [1, 2])
        self.assertEqual([leg["leg"] for leg in second["tie"]["legs"]], [1, 2])
        self.assertEqual((first["fixture"]["total_home"], first["fixture"]["total_away"], first["fixture"]["leg"]), (4, 1, 1))
        self.assertEqual((second["fixture"]["total_home"], second["fixture"]["total_away"], second["fixture"]["leg"]), (5, 0, 2))
        self.assertEqual(first["tie"]["aggregate_text"], "Glorious Vision 4 – Ironvale FC 6")
        self.assertEqual(second["tie"]["aggregate_text"], "Ironvale FC 6 – Glorious Vision 4")
        self.assertEqual({leg["match_id"] for leg in second["tie"]["legs"]}, {"m02-00919", "m02-00920"})
        self.assertEqual(before, sha(path), "读取归档不应写盘")

    def test_playoff_two_leg_remains(self) -> None:
        world = store.load_world("s3tie")
        detail = queries.match_detail(world, "s3tie", "m03-00962")
        self.assertIsNotNone(detail["tie"])
        self.assertEqual([leg["leg"] for leg in detail["tie"]["legs"]], [1, 2])

    def test_tie_whole_cup_stages_do_not_cross_isolate(self) -> None:
        world = store.load_world("s3tie")
        detail = queries.match_detail(world, "s3tie", "m02-00920")
        ids = {leg["match_id"] for leg in detail["tie"]["legs"]}
        self.assertEqual(ids, {"m02-00919", "m02-00920"})
        # Another aureole tie with different teams must remain separate.
        other = queries.match_detail(world, "s3tie", "m02-00921")
        self.assertTrue(other["tie"])
        self.assertFalse(ids & {leg["match_id"] for leg in other["tie"]["legs"]})


    def test_unplayed_two_leg_has_no_final_aggregate(self) -> None:
        world = store.load_world("s2tie")
        detail = queries.match_detail(world, "s2tie", "m02-00919")
        self.assertIsNotNone(detail["tie"])
        self.assertEqual(detail["tie"]["completed_legs"], 0)
        self.window.open_save("s2tie")
        self.window.navigate("match", match_id="m02-00919")
        QTest.qWait(120)
        page = self.window._pages["match"]
        from football_simulator.ui.components import FocusScoreCard

        focus = page.findChild(FocusScoreCard)
        self.assertEqual(focus.aggregate_label.text(), "两回合尚未开赛")
        labels = [label.text() for label in page.findChildren(__import__("PySide6").QtWidgets.QLabel) if label.text()]
        self.assertIn("两回合赛程（尚未开赛）", labels)
        self.assertNotIn("总比分：", " ".join(labels))

    def test_detail_focus_and_leg_links_use_normalized_legs(self) -> None:
        self.window.open_save("s3tie")
        self.window.navigate("match", match_id="m02-00920")
        QTest.qWait(140)
        page = self.window._pages["match"]
        self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
        focus = page.findChild(__import__("football_simulator.ui.components", fromlist=["FocusScoreCard"]).FocusScoreCard)
        self.assertEqual(focus.score_label.text(), "5–0")
        self.assertIn("第 2 回合", focus.status.text())
        self.assertEqual(focus.aggregate_label.text(), "总比分：Ironvale FC 6 – Glorious Vision 4")
        labels = [label.text() for label in page.findChildren(__import__("PySide6").QtWidgets.QLabel) if label.text()]
        self.assertIn("第 1 回合", labels)
        self.assertIn("第 2 回合", labels)
        links = [button for button in page.findChildren(__import__("football_simulator.ui.components", fromlist=["EntityLink"]).EntityLink) if getattr(button, "kind", "") == "match"]
        self.assertGreaterEqual(len(links), 2)
        target = next(link for link in links if "m02" in link.entity_id or getattr(link, "entity_id", ""))
        self.assertIn(getattr(target, "entity_id", ""), {"m02-00919", "m02-00920"})


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
