"""UIR2-004 回归：荣誉层级中文展示，内部键和分值不变。"""

from __future__ import annotations

import hashlib
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidget

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.persistence_v2 import store
from football_simulator.ui.main_window import MainWindow
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures_vnext"


class HonorLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir2_honor_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(20261020))
        cls.service.import_save(FIXTURES / "s3-finalized.fsarchive", "s3honor")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.window.open_save("s3honor")
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_all_17_labels_and_unknown_fallback(self) -> None:
        expected = {
            "champion": "冠军", "runner_up": "亚军", "semifinal": "四强", "quarterfinal": "八强",
            "group_third": "小组第3", "group_fourth": "小组第4", "round_16": "16强",
            "round_32": "32强", "swiss_2_3": "瑞士轮2胜3负", "swiss_1_3": "瑞士轮1胜3负",
            "swiss_0_3": "瑞士轮0胜3负", "third": "季军", "fourth": "第4名",
            "fifth_sixth": "第5–6名", "seventh_eighth": "第7–8名", "play_in_exit": "突围赛出局",
            "nirvana_sixth": "涅槃组第6",
        }
        self.assertEqual(queries.TIER_LABELS, expected)
        for key, label in expected.items():
            self.assertEqual(queries.tier_label(key), label)
        self.assertEqual(queries.tier_label("future_key"), "未分类")

    def test_cup_placement_rows_keep_internal_keys_and_points(self) -> None:
        world = store.load_world("s3honor")
        for competition in rules.CUP_IDS:
            dto = queries.competition_detail(world, "s3honor", competition)
            self.assertTrue(dto["placement_rows"], competition)
            for row in dto["placement_rows"]:
                self.assertIn("tier", row)
                self.assertNotEqual(row["tier"], row["tier_label"])
                self.assertEqual(row["tier_label"], queries.tier_label(row["tier"]))
                self.assertIn(row["tier_label"], queries.TIER_LABELS.values())
                self.assertEqual(row["points"], rules.honor_points_for(competition, row["rank"]))
        self.assertTrue(all(row["points"] == 0 for row in queries.competition_detail(world, "s3honor", rules.SECOND).get("placement_rows", [])))

    def test_six_cup_pages_show_chinese_tiers_only(self) -> None:
        raw_keys = set(queries.TIER_LABELS)
        for competition in rules.CUP_IDS:
            self.window.navigate("competition", competition_id=competition)
            QTest.qWait(100)
            self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
            page = self.window._pages["competition"]
            cells = []
            for table in page.findChildren(QTableWidget):
                for row in range(table.rowCount()):
                    for col in range(table.columnCount()):
                        item = table.item(row, col)
                        if item is not None:
                            cells.append(item.text())
            self.assertFalse(raw_keys & set(cells), f"{competition} 暴露内部层级键")
            self.assertTrue(any(text in queries.TIER_LABELS.values() for text in cells), competition)
            self.assertFalse(any("champion" in text or "semifinal" in text or "swiss_" in text for text in cells), competition)

    def test_honor_facts_unchanged_by_reads(self) -> None:
        path = store.database_path("s3honor")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        world = store.load_world("s3honor")
        for competition in rules.CUP_IDS:
            queries.competition_detail(world, "s3honor", competition)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
