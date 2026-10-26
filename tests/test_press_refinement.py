"""Week-boundary evidence and independent positives/negatives from the first pilot."""
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest

from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.press import PressError, repository
from football_simulator.press.editorial import request_preview, render_markdown
from football_simulator.press.validation import validate_article, validate_packet
from tests.test_press_export import PressSaveCase

ROOT = Path(__file__).resolve().parents[1]


def article(packet, text, refs, *, topic=None):
    if topic is None:
        topic = next(t for t in packet["editorial"]["topics"] if set(t["required_fact_ids"]) <= set(refs))
    return {"schema_version": "press.article/0.1-draft", "report_id": packet["meta"]["report_id"],
            "title": text, "title_fact_ids": refs, "summary": text, "summary_fact_ids": refs,
            "used_topic_ids": [topic["topic_id"]], "sections": [{"heading": text, "heading_fact_ids": refs,
                "blocks": [{"kind": "body", "label": None, "text": text, "fact_ids": refs}]}]}


class WeeklyComparisonTests(PressSaveCase):
    def fixture(self, mid, week, home, away, hg, ag, comp="premier"):
        w = self.world()
        return {"match_id": mid, "season_number": 1, "competition": comp, "stage": "league", "week": week,
                "status": "completed", "home_team_id": home, "away_team_id": away,
                "stadium_id": w["teams"][home]["stadium_id"], "venue_mode": "home", "home_goals": hg, "away_goals": ag,
                "home_goals_et": 0, "away_goals_et": 0, "home_pk": None, "away_pk": None, "extra_time_played": False,
                "events": [], "shootout": [], "player_facts": {}, "winner_team_id": home if hg > ag else away if ag > hg else None}

    def capture(self, fixtures, week=2):
        repository.enable("Press")
        w = self.world(); w["season"]["fixtures"] = fixtures; w["season"]["week"] = week + 1
        store.write_world("Press", w, completed_week=week)
        return repository.load_week("Press", 1, week)

    def scenario(self, changed=False):
        a, b, c, d = self.world()["season"]["league"]["premier"][:4]
        fixtures = [self.fixture("before-a", 1, a, b, 3, 0), self.fixture("before-c", 1, c, d, 2, 0)]
        fixtures += [self.fixture("current-a", 2, a, c, 0 if changed else 2, 1 if changed else 0),
                     self.fixture("current-b", 2, b, d, 6 if changed else 1, 0)]
        return self.capture(fixtures), (a, b, c, d)

    def table(self, material, tid):
        return next(f for f in material["packet"]["facts"] if f["kind"] == "table_row" and f["data"]["team_id"] == tid and f["data"]["timing"] == "end_of_week")

    def leader_article(self, m, tid, text):
        p = m["packet"]; topic = next(t for t in p["editorial"]["topics"] if t["event_type"] == "league_lead:premier")
        return article(p, text, topic["required_fact_ids"], topic=topic)

    def test_continuing_leader_and_gap_are_derived_from_whole_previous_week(self):
        m, (a, b, c, d) = self.scenario()
        row = self.table(m, a)["data"]
        self.assertEqual((row["rank"], row["points"], row["played"]), (1, 6, 2))
        self.assertEqual(row["week_comparison"], {"basis": "completed_week_start_to_end", "before_rank": 1,
                         "before_points": 3, "before_played": 1, "rank_change": 0, "points_change": 3})
        self.assertEqual(row["lead_context"], {"before_leader_team_id": a, "leader_changed": False,
                         "before_gap": 0, "after_gap": 3, "same_leader_gap_change": 3})
        name = m["packet"]["entities"]["teams"][a]["name_en"]
        good = self.leader_article(m, a, name + "继续领跑，领先优势扩大至3分")
        self.assertEqual(validate_article(good, m["packet"])["narrative_policy"], "weekly-narrative/3")

    def test_false_new_leader_and_incorrect_gap_fail(self):
        m, (a, b, c, d) = self.scenario(); name = m["packet"]["entities"]["teams"][a]["name_en"]
        for text in (name + "登顶榜首", name + "以6分升至 Premier Division 榜首", "榜首换人",
                     name + "继续领跑，领先优势扩大至4分", name + "领先优势缩小至3分"):
            with self.subTest(text=text), self.assertRaises(PressError):
                validate_article(self.leader_article(m, a, text), m["packet"])

    def test_new_leader_passes_and_cannot_borrow_previous_leaders_gap_growth(self):
        m, (a, b, c, d) = self.scenario(changed=True); row = self.table(m, c)["data"]
        self.assertEqual((row["rank"], row["week_comparison"]["before_rank"], row["week_comparison"]["rank_change"]), (1, 2, 1))
        self.assertTrue(row["lead_context"]["leader_changed"])
        self.assertIsNone(row["lead_context"]["same_leader_gap_change"])
        name = m["packet"]["entities"]["teams"][c]["name_en"]
        validate_article(self.leader_article(m, c, name + "登顶榜首，榜首换人"), m["packet"])
        for text in (name + "继续领跑", name + "领先优势扩大至3分"):
            with self.subTest(text=text), self.assertRaises(PressError):
                validate_article(self.leader_article(m, c, text), m["packet"])

    def test_first_round_does_not_invent_movement_from_initial_seeding(self):
        a, b = self.world()["season"]["league"]["premier"][:2]
        m = self.capture([self.fixture("round-one", 1, a, b, 1, 0)], week=1)
        d = self.table(m, a)["data"]
        self.assertIsNone(d["week_comparison"]["before_rank"])
        self.assertIsNone(d["week_comparison"]["rank_change"])
        self.assertIsNone(d["lead_context"]["leader_changed"])
        name = m["packet"]["entities"]["teams"][a]["name_en"]
        validate_article(self.leader_article(m, a, name + "首轮领跑"), m["packet"])
        for text in (name + "升至第1名", name + "登顶榜首", name + "继续领跑"):
            with self.subTest(text=text), self.assertRaises(PressError):
                validate_article(self.leader_article(m, a, text), m["packet"])

    def test_rank_movement_correct_wrong_direction_and_still_are_distinct(self):
        m, (a, b, c, d) = self.scenario(changed=True); p = m["packet"]
        name = p["entities"]["teams"][a]["name_en"]; fid = self.table(m, a)["fact_id"]
        topic = next(t for t in p["editorial"]["topics"] if t["event_type"] == "league_lead:premier")
        refs = [fid, *topic["required_fact_ids"]]
        self.assertEqual(self.table(m, a)["data"]["rank"], 3)
        validate_article(article(p, name + "下滑至第三位", refs, topic=topic), p)
        for text in (name + "仍列第3位", name + "升至第三位", name + "位居第四位"):
            with self.subTest(text=text), self.assertRaises(PressError): validate_article(article(p, text, refs, topic=topic), p)

    def test_mixed_team_sentence_does_not_attach_a_verb_to_the_wrong_team(self):
        m, (a, b, c, d) = self.scenario(changed=True); p = m["packet"]
        an, cn = [p["entities"]["teams"][t]["name_en"] for t in (a, c)]
        topic = next(t for t in p["editorial"]["topics"] if t["event_type"] == "league_lead:premier")
        refs = [self.table(m, a)["fact_id"], *topic["required_fact_ids"]]
        validate_article(article(p, an + "下滑至第3位，" + cn + "登顶榜首", refs, topic=topic), p)
        validate_article(self.leader_article(m, c, cn + "并非继续领跑，而是本周登顶榜首"), p)

    def test_week_start_rank_is_not_misread_as_current_rank(self):
        m, (a, b, c, d) = self.scenario(changed=True); p = m["packet"]
        name = p["entities"]["teams"][c]["name_en"]
        validate_article(self.leader_article(m, c, name + "周初排名第二，本周登顶榜首"), p)
        with self.assertRaises(PressError):
            validate_article(self.leader_article(m, c, name + "周初排名第三，本周登顶榜首"), p)

    def test_unnamed_headline_still_needs_a_verified_leader_transition(self):
        m, (a, b, c, d) = self.scenario()
        with self.assertRaises(PressError): validate_article(self.leader_article(m, a, "五连胜后登顶榜首"), m["packet"])

    def test_self_consistent_forged_comparison_is_checked_against_raw_matches(self):
        m, (a, b, c, d) = self.scenario(); bad = deepcopy(m)
        f = self.table(bad, a); f["data"]["week_comparison"]["before_rank"] = 2
        bad["sources"]["records"]["fact:" + f["fact_id"]]["value"]["result"] = deepcopy(f["data"])
        with self.assertRaisesRegex(PressError, "周初排名"): validate_packet(bad["packet"], bad["sources"])
        bad = deepcopy(m)
        bad["sources"]["records"]["table:premier"]["value"]["before_match_keys"].append("match:current-a")
        with self.assertRaisesRegex(PressError, "周初积分榜"): validate_packet(bad["packet"], bad["sources"])

    def test_equal_streak_counts_with_different_cup_sequences_do_not_merge(self):
        a, b, c, d = self.world()["season"]["league"]["premier"][:4]
        fixtures = [self.fixture("league-loss", 1, a, b, 0, 1), self.fixture("league-win-1", 2, a, b, 1, 0),
                    self.fixture("cup-loss", 3, a, b, 0, 1, "confluence"), self.fixture("league-win-2", 4, a, b, 1, 0),
                    self.fixture("cup-win", 5, a, b, 1, 0, "confluence"), self.fixture("league-win-3", 6, a, b, 1, 0)]
        m = self.capture(fixtures, week=6); p = m["packet"]
        ids = {f["fact_id"] for f in p["facts"] if f["kind"] == "streak" and f["data"]["team_id"] == a and f["data"]["kind"] == "wins" and f["data"]["after"] == 3}
        self.assertEqual(len(ids), 2)
        self.assertFalse(any(ids <= set(g["equivalent_fact_ids"]) for g in p["editorial"]["streak_groups"]))


class FirstPilotRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fs_press_refinement_")
        old = runtime._DATA_ROOT_OVERRIDE
        try:
            runtime.set_data_root_override(Path(cls.tmp.name))
            service = GameService(rng_factory=lambda: random.Random(71397), edition_archives=True)
            service.create_save("Regression"); service.initialize_save("Regression"); repository.enable("Regression")
            for _ in range(7): service.advance_week("Regression")
            cls.material = repository.load_week("Regression", 1, 7)
        finally:
            runtime.set_data_root_override(old)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_first_real_response_is_now_rejected_without_modifying_original(self):
        path = ROOT / "tests/fixtures_press/first_weekly_response.json"
        original = path.read_bytes(); a = json.loads(original)
        a["report_id"] = self.material["packet"]["meta"]["report_id"]
        with self.assertRaisesRegex(PressError, "新登顶|榜首变更"):
            validate_article(a, self.material["packet"])
        self.assertEqual(path.read_bytes(), original)

    def test_actual_week_seven_continuing_leader_and_two_point_gap(self):
        p = self.material["packet"]; tid = "team-yellowjackets-fc"
        row = next(f["data"] for f in p["facts"] if f["fact_id"] == "table:premier:" + tid)
        self.assertEqual((row["week_comparison"]["before_rank"], row["week_comparison"]["before_points"], row["points"]), (1, 15, 18))
        self.assertEqual((row["lead_context"]["before_gap"], row["lead_context"]["after_gap"]), (0, 2))
        topic = next(t for t in p["editorial"]["topics"] if t["event_type"] == "league_lead:premier")
        good = article(p, "Yellowjackets FC继续领跑，领先优势扩大至2分", topic["required_fact_ids"], topic=topic)
        validate_article(good, p)

    def test_equivalent_streaks_prefer_league_scope_and_tampering_fails(self):
        p = self.material["packet"]; groups = p["editorial"]["streak_groups"]
        self.assertTrue(groups)
        self.assertTrue(all(":league_all_levels:" in g["preferred_fact_id"] for g in groups))
        bad = deepcopy(self.material); bad["packet"]["editorial"]["streak_groups"][0]["preferred_fact_id"] = "unknown"
        with self.assertRaisesRegex(PressError, "等价连续纪录"): validate_packet(bad["packet"], bad["sources"])

    def test_player_story_requires_formal_name_and_stadium_first_mention_is_bilingual(self):
        p = self.material["packet"]
        topic = next(t for t in p["editorial"]["topics"] if "antoine-griezmann" in t["event_type"])
        refs = topic["required_fact_ids"]
        with self.assertRaisesRegex(PressError, "正式姓名"): validate_article(article(p, "格列兹曼上演帽子戏法", refs, topic=topic), p)
        good = "Antoine Griezmann上演帽子戏法，比赛在星穹守望球场（Starwatch Stadium）进行"
        validate_article(article(p, good, refs, topic=topic), p)
        with self.assertRaisesRegex(PressError, "双语"): validate_article(article(p, good.replace("（Starwatch Stadium）", ""), refs, topic=topic), p)

    def test_home_record_cannot_be_invented_in_a_fictional_comment(self):
        p = self.material["packet"]
        topic = next(t for t in p["editorial"]["topics"] if t["event_type"].startswith("streak:team-star-guardians:league_all_levels:unbeaten"))
        a = article(p, "Star Guardians的五场不败结束", topic["required_fact_ids"], topic=topic)
        a["sections"][0]["blocks"].append({"kind": "media_comment", "label": "媒体评论（虚构）", "text": "Star Guardians的主场五场不败结束", "fact_ids": topic["required_fact_ids"]})
        with self.assertRaisesRegex(PressError, "主场纪录"): validate_article(a, p)

    def test_prompt_revision_is_recorded_and_does_not_mutate_frozen_packet(self):
        before = repository.canonical(self.material)
        preview = request_preview(self.material["packet"], self.material["sources"])
        self.assertEqual(preview["prompt_versions"], ["editorial-system-draft-5", "weekly-user-draft-5"])
        self.assertIn("不需要报道所有比赛", preview["request"]["messages"][1]["content"])
        self.assertEqual(repository.canonical(self.material), before)

    def test_legacy_input_remains_readable_without_rewriting_old_fact_snapshots(self):
        path = ROOT / "docs/press/examples"
        p = json.loads((path / "weekly-feature.json").read_text()); a = json.loads((path / "weekly-feature.article.json").read_text())
        text, checks = render_markdown(a, p)
        self.assertIn(a["title"], text)
        self.assertEqual(checks["narrative_policy"], "legacy_material")


if __name__ == "__main__": unittest.main()
