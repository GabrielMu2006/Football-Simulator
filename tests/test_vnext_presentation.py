"""NH-050/060/080: 英文主名、纪录/荣誉页与键盘布局。"""
from __future__ import annotations
import random, shutil, tempfile, unittest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from football_simulator.app import queries, records
from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.persistence_v2 import store
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.components import EntityLink, PageHeader
from football_simulator.ui.main_window import MainWindow
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures_vnext"


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_vnext_ui_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.svc = GameService(rng_factory=lambda: random.Random(20261020))
        cls.svc.import_save(FIX / "s3-finalized.fsarchive", "s3pres")
        cls.window = MainWindow(cls.svc)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.window.open_save("s3pres")
        QTest.qWait(100)
        cls.world = store.load_world("s3pres")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_primary_names_and_detail_subtitles(self) -> None:
        team_id = next(iter(self.world["teams"]))
        team = self.world["teams"][team_id]
        self.assertEqual(queries.team_short(self.world, team_id), team["club_en"])
        self.assertEqual(queries.team_label(self.world, team_id), team["club_en"])
        self.assertEqual(rules.competition_label("aureole"), "Reign Cup")
        self.assertEqual(rules.display_name("aureole"), "Reign Cup")
        self.assertEqual(rules.display_name("aureole", "zh"), "君临杯")
        stadium = queries.stadium_name(team["stadium_id"])
        self.assertIn("·", stadium)
        self.assertIn(team["club_en"], stadium)
        # team detail title EN, Chinese subtitle
        self.window.navigate("team", team_id=team_id)
        QTest.qWait(100)
        self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
        header = self.window._pages["team"].findChildren(PageHeader)[0]
        self.assertEqual(header.title.text(), team["club_en"])
        self.assertIn(team.get("name_zh", ""), header.subtitle.text())
        # competition detail title EN, Chinese subtitle
        self.window.navigate("competition", competition_id="aureole")
        QTest.qWait(100)
        self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
        header = self.window._pages["competition"].findChildren(PageHeader)[0]
        self.assertEqual(header.title.text(), "Reign Cup")
        self.assertIn("君临杯", header.subtitle.text())

    def test_records_honors_routes_and_history_facts(self) -> None:
        for route, params in (
            ("records", {}),
            ("honors", {}),
            ("team_honors", {"team_id": next(iter(self.world["teams"]))}),
            ("player_honors", {"player_id": next(iter(self.world["players"]))}),
            ("history_competition", {"competition_id": "aureole", "season": 2}),
        ):
            self.window.navigate(route, **params)
            QTest.qWait(80)
            self.assertTrue(self.window.error_label.isHidden(), f"{route}: {self.window.error_label.text()}")
            self.assertEqual(self.window._current_route[0], route)
        # archived S2 is real, not current bracket
        labels = [label.text() for label in self.window._pages["history_competition"].findChildren(__import__("PySide6").QtWidgets.QLabel) if label.text()]
        joined = " ".join(labels)
        self.assertIn("第 2 赛季", joined)
        self.assertIn("Reign Cup", joined)
        # The current macOS page uses the real edition context and selector.
        page = self.window._pages["history_competition"]
        if macos.enabled():
            self.assertEqual(page._display_world["season_number"], 2)
            self.assertEqual(page.findChild(__import__("PySide6").QtWidgets.QComboBox, "CompetitionSeason").currentData(), 2)
        else:
            self.assertIn("往届赛季", joined)

    def test_team_honors_trophy_fallback_preserves_champion_text(self) -> None:
        row = next(iter(self.world["season"]["championships"].values()))
        self.window.navigate("team_honors", team_id=row["champion_team_id"])
        QTest.qWait(100)
        page = self.window._pages["team_honors"]
        labels = [label.text() for label in page.findChildren(__import__("PySide6").QtWidgets.QLabel) if label.text()]
        joined = " ".join(labels)
        self.assertIn("冠军总数", joined)
        # champion count is shown regardless of whether formal trophy images are delivered
        self.assertTrue(any("次" in text for text in labels))
        from football_simulator.ui.trophies import asset_status
        if not asset_status(row["competition_id"])["ready"]:
            self.assertIn("奖杯图片待交付", joined)

    def test_entity_link_return_and_numeric_enter_once(self) -> None:
        self.window.navigate("honors")
        QTest.qWait(100)
        page = self.window._pages["honors"]
        links = [link for link in page.findChildren(EntityLink) if link.kind == "team_honors"]
        self.assertTrue(links)
        target = links[0]
        before = len(self.window._back)
        target.setFocus()
        QTest.keyClick(target, Qt.Key_Return)
        QTest.qWait(80)
        self.assertEqual(self.window._current_route[0], "team_honors")
        self.assertEqual(len(self.window._back), before + 1)
        # numeric keypad Enter
        self.window.go_back()
        QTest.qWait(80)
        page = self.window._pages["honors"]
        target = next(link for link in page.findChildren(EntityLink) if link.kind == "team_honors")
        target.setFocus()
        before = len(self.window._back)
        QTest.keyClick(target, Qt.Key_Enter)
        QTest.qWait(80)
        self.assertEqual(len(self.window._back), before + 1)

    def test_three_sizes_no_horizontal_overflow(self) -> None:
        for route, params in (("records", {}), ("team_honors", {"team_id": next(iter(self.world["teams"]))}), ("player_honors", {"player_id": next(iter(self.world["players"]))})):
            self.window.navigate(route, **params)
            for width, height in ((1280, 800), (1440, 900), (1680, 980), (1280, 800)):
                self.window.setFixedSize(width, height)
                QTest.qWait(180)
                page = self.window._pages[route]
                scroll = getattr(page, "scroll")
                self.assertLessEqual(scroll.widget().width(), scroll.viewport().width() + 1, f"{route} {width}")
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0, f"{route} {width}")
                self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())

    def test_stats_and_championship_queries_are_scoped_to_save(self) -> None:
        self.svc.import_save(FIX / "s2-week25.fsarchive", "s2other")
        s2 = store.load_world("s2other")
        pid = next(iter(s2["players"]))
        self.assertGreaterEqual(records.career_totals("s2other", pid)["appearances"], 0)
        self.assertNotEqual(records.leaderboards("s2other", scope="all")["total"], records.leaderboards("s3pres", scope="all")["total"])


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
