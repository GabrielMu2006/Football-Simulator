"""规则注册表、赛历、球场与荣誉表的独立断言。"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from football_simulator.game import rules


class RuleRegistryTests(unittest.TestCase):
    def test_rule_files_match_confirmed_sources(self) -> None:
        for key, path in (("competitions", rules.COMPETITIONS_PATH), ("stadiums", rules.STADIUMS_PATH)):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, rules.RULES_SOURCE_SHA256[key])

    def test_six_stable_ids_and_display_names(self) -> None:
        self.assertEqual(
            rules.CUP_IDS,
            ("aureole", "confluence", "starpath", "ember", "ascendant", "zenith"),
        )
        expected = {
            "aureole": ("君临杯", "Reign Cup"),
            "confluence": ("命途杯", "Destiny Cup"),
            "starpath": ("极境杯", "Apex Cup"),
            "ember": ("不灭杯", "Resurgence Cup"),
            "ascendant": ("凌越杯", "Ascendance Cup"),
            "zenith": ("永世杯", "Aeon Cup"),
        }
        for cup_id, (zh, en) in expected.items():
            self.assertEqual(rules.display_name(cup_id, "zh") if zh else rules.display_name(cup_id, "zh"), zh or en)
            self.assertEqual(rules.display_name(cup_id, "en"), en)
        self.assertEqual(rules.competition_label("zenith"), "Aeon Cup")

    def test_cup_match_counts_and_entrants(self) -> None:
        counts = {cup_id: rules.CUP_META[cup_id]["match_count"] for cup_id in rules.CUP_IDS}
        self.assertEqual(
            counts,
            {"aureole": 62, "confluence": 31, "starpath": 46, "ember": 14, "ascendant": 46, "zenith": 7},
        )

    def test_calendar_is_52_weeks_with_breaks_and_finals(self) -> None:
        weeks = rules.calendar_weeks()
        self.assertEqual(len(weeks), 52)
        self.assertEqual([week["week"] for week in weeks], list(range(1, 53)))
        breaks = {week["week"]: week.get("break_type") for week in weeks if week.get("break_type")}
        self.assertEqual(breaks, {25: "winter", 26: "winter", 27: "winter", 50: "summer", 51: "summer", 52: "summer"})
        finals = {
            slot["competition"]: week["week"]
            for week in weeks
            for slot in week.get("cup_slots", [])
            if slot.get("stage") in ("final", "final_leg_2", "grand_final")
        }
        self.assertEqual(finals, {"zenith": 45, "confluence": 30, "ascendant": 43, "aureole": 47, "ember": 48, "starpath": 49})
        self.assertTrue(all("cup" not in week for week in weeks))

    def test_non_knockout_stages_end_by_week_24(self) -> None:
        self.assertEqual(rules.stage_weeks("ascendant")["cross_group_6"], [19])
        self.assertEqual(rules.stage_weeks("aureole")["group_6"], [23])
        self.assertEqual(rules.stage_weeks("starpath")["swiss_5"], [24])

    def test_active_cups_by_season(self) -> None:
        self.assertEqual(rules.active_cups(1), ())
        self.assertEqual(rules.active_cups(2), ("aureole", "confluence", "starpath", "ember", "ascendant"))
        self.assertEqual(len(rules.active_cups(3)), 6)

    def test_stadiums_bind_all_40_clubs(self) -> None:
        stadiums = rules.load_stadiums()
        self.assertEqual(len(stadiums), 40)
        self.assertEqual(len({item["stadium_id"] for item in stadiums}), 40)
        self.assertEqual(len({item["club_en"] for item in stadiums}), 40)
        mapping = rules.stadiums_by_club()
        config = json.loads((Path(__file__).resolve().parent.parent / "足球模拟器总配置.json").read_text(encoding="utf-8"))
        clubs = set(config["premier_teams"]) | set(config["second_division_teams"])
        self.assertEqual(set(mapping), clubs)

    def test_selected_stadium_names_are_in_production(self) -> None:
        selected = json.loads(
            (Path(__file__).resolve().parent.parent / "Reviews" / "identity_redesign_2026-10-20" / "stadiums.selected.json").read_text(encoding="utf-8")
        )["stadiums"]
        production = {item["club_en"]: item for item in rules.load_stadiums()}
        self.assertEqual(set(production), {item["club_en"] for item in selected})
        for item in selected:
            self.assertEqual(production[item["club_en"]]["stadium_id"], item["stadium_id"])
            self.assertEqual(production[item["club_en"]]["name_zh"], item["name_zh"])
            self.assertEqual(production[item["club_en"]]["name_en"], item["name_en"])

    def test_honor_table_tiers_cover_every_final_place(self) -> None:
        cases = {
            "aureole": {1: 90, 2: 72, 3: 56, 5: 42, 9: 24, 13: 18},
            "confluence": {1: 65, 2: 52, 3: 40, 5: 30, 9: 20, 17: 12},
            "starpath": {1: 90, 2: 72, 3: 56, 5: 42, 9: 32, 12: 24, 15: 18},
            "ember": {1: 70, 2: 56, 3: 44, 4: 34, 5: 24, 7: 16},
            "ascendant": {1: 80, 2: 64, 3: 50, 5: 38, 9: 26, 12: 18},
            "zenith": {1: 45, 2: 28, 3: 16, 5: 8},
        }
        for cup_id, ranks in cases.items():
            for rank, points in ranks.items():
                self.assertEqual(rules.honor_points_for(cup_id, rank), points, f"{cup_id} rank {rank}")
        self.assertEqual(rules.premier_honor_points(1), 120)
        self.assertEqual(rules.premier_honor_points(20), 1)

    def test_second_division_has_zero_honor_points(self) -> None:
        table = rules.honor_points()["second"]
        self.assertFalse(table["enabled"])
        self.assertEqual(table["all_places"], 0)
        self.assertTrue(table["champion_still_qualifies_for_cup6"])


if __name__ == "__main__":
    unittest.main()
