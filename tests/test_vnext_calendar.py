"""NH-010: 新版赛历与三赛季冠军周次。"""
from __future__ import annotations
import random, shutil, tempfile, unittest
from collections import defaultdict
from pathlib import Path
from football_simulator.game import rules
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.game.calendar import completed_matches
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures_vnext"


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_vnext_cal_")
        support.install_test_data_root(cls._tmp)
        cls.svc = GameService(rng_factory=lambda: random.Random(20261020))
        cls.svc.import_save(FIX / "s3-finalized.fsarchive", "s3cal")
        cls.svc.import_save(FIX / "s2-week25.fsarchive", "s2cal")

    @classmethod
    def tearDownClass(cls) -> None:
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_52_weeks_single_cup_slots_source(self) -> None:
        weeks = rules.calendar_weeks()
        self.assertEqual(len(weeks), 52)
        self.assertEqual([w["week"] for w in weeks], list(range(1, 53)))
        self.assertTrue(all("cup_slots" in w and "cup" not in w for w in weeks))
        for week in weeks:
            if week["week"] in (25, 26, 27, 50, 51, 52):
                self.assertEqual(week["cup_slots"], [])
        final_weeks = {}
        for week in weeks:
            for slot in week["cup_slots"]:
                if slot["stage"] in ("final", "final_leg_2", "grand_final"):
                    final_weeks[slot["competition"]] = week["week"]
        self.assertEqual(final_weeks, {"zenith": 45, "confluence": 30, "ascendant": 43, "aureole": 47, "ember": 48, "starpath": 49})
        self.assertEqual(rules.stage_weeks("zenith"), {"quarterfinal": [43], "semifinal": [44], "final": [45]})
        self.assertEqual(rules.stage_weeks("ember")["wb_final"], [44])
        self.assertEqual(rules.stage_weeks("ember")["lb_round_3"], [44])
        self.assertEqual(rules.week_slot(43)["cup_slots"][0]["competition"], "ascendant")
        self.assertEqual(rules.week_slot(43)["cup_slots"][1]["competition"], "zenith")

    def test_s2_and_s3_counts_and_frozen_championships(self) -> None:
        s3 = store.load_world("s3cal")
        s2 = store.load_world("s2cal")
        cups3 = [f for f in s3["season"]["fixtures"] if f["competition"] in rules.CUP_IDS]
        cups2 = [f for f in s2["season"]["fixtures"] if f["competition"] in rules.CUP_IDS]
        self.assertEqual(len(cups3), 206)
        self.assertEqual({c: sum(1 for f in cups3 if f["competition"] == c) for c in rules.CUP_IDS}, {"aureole": 62, "confluence": 31, "starpath": 46, "ember": 14, "ascendant": 46, "zenith": 7})
        # s2 fixture is frozen at week 25 (winter), later rounds are not created yet;
        # the completed S2 edition is available as historical matches in S3.
        self.assertEqual(len(cups2), 174)
        s2_completed = [row for row in store.fetch_matches("s3cal", season=2) if row["competition"] in rules.CUP_IDS]
        self.assertEqual(len(s2_completed), 199)
        league3 = [f for f in s3["season"]["fixtures"] if f["competition"] in (rules.PREMIER, rules.SECOND)]
        playoff3 = [f for f in s3["season"]["fixtures"] if f["competition"] == rules.PLAYOFF]
        self.assertEqual(len(league3), 760)
        self.assertEqual(len(playoff3), 6)
        champs = s3["season"]["championships"]
        self.assertEqual(len(champs), 8)
        self.assertTrue(all(int(row["decided_week"]) > 27 for row in champs.values()))
        self.assertEqual(sorted(row["decided_week"] for row in champs.values()), [30, 42, 42, 43, 45, 47, 48, 49])
        for comp, expected in (("premier", 42), ("second", 42), ("confluence", 30), ("ascendant", 43), ("zenith", 45), ("aureole", 47), ("ember", 48), ("starpath", 49)):
            row = next(r for r in champs.values() if r["competition_id"] == comp)
            self.assertEqual(int(row["decided_week"]), expected, comp)
        self.assertEqual([archive["season_number"] for archive in s3["archives"]], [1, 2, 3])

    def test_team_week_capacity_and_breaks(self) -> None:
        s3 = store.load_world("s3cal")
        counts = defaultdict(int)
        for fixture in s3["season"]["fixtures"]:
            counts[(fixture["home_team_id"], fixture["week"])] += 1
            counts[(fixture["away_team_id"], fixture["week"])] += 1
        self.assertLessEqual(max(counts.values()), 2)
        for week in (25, 26, 27, 50, 51, 52):
            self.assertEqual([f for f in s3["season"]["fixtures"] if int(f["week"]) == week], [])
        # Non-knockout stages still end at 19/23/24.
        for comp, stage, week in (("ascendant", "cross_group_6", 19), ("aureole", "group_6", 23), ("starpath", "swiss_5", 24)):
            fixtures = [f for f in s3["season"]["fixtures"] if f["competition"] == comp and f["stage"] == stage]
            self.assertTrue(fixtures)
            self.assertTrue(all(int(f["week"]) == week for f in fixtures))
        for week in (43, 44, 45):
            relevant = [f for f in s3["season"]["fixtures"] if int(f["week"]) == week]
            self.assertTrue(relevant)

    def test_old_rule_archive_rejected(self) -> None:
        old = FIX / "old-rules-s3.fsarchive"
        self.assertTrue(old.is_file(), "旧规则拒绝测试夹具缺失")
        with self.assertRaises(Exception):
            self.svc.import_save(old, "old-rule-should-reject")


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
