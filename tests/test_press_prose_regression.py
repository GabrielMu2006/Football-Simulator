"""Real rejected text plus controlled positives; offline, never a model quality claim."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from football_simulator.press import PressError, repository
from football_simulator.press.prose import validate_narrative, style_advice
from football_simulator.press.validation import validate_article, validate_packet
from tests import test_press_refinement as refinement
from tests.test_press_refinement import article
from tests.test_press_export import PressSaveCase

FIXTURE = Path(__file__).parent / 'fixtures_press/second_weekly'


class SecondResponseRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = json.loads((FIXTURE / 'weekly.json').read_text())
        cls.sources = json.loads((FIXTURE / 'sources.json').read_text())
        cls.response = json.loads((FIXTURE / 'response.json').read_text())

    def prose(self, text, refs=None):
        # Isolate a real sentence without fabricating a new model response.
        return validate_narrative({'title': text, 'title_fact_ids': refs or self.all_refs(),
            'summary': '', 'summary_fact_ids': [], 'sections': [], 'used_topic_ids': []}, self.packet, self.sources)

    def all_refs(self):
        return [f['fact_id'] for f in self.packet['facts']]

    def corrected_copy(self):
        a = deepcopy(self.response)
        a['sections'][0]['heading'] = 'Premier Division：Pain FC 登顶，Celestial Vanguard 七轮不败终结'
        for sec in a['sections']:
            for b in sec['blocks']:
                b['text'] = b['text'].replace('本赛季联赛首场失利', '本赛季联赛第二场失利').replace('同一天', '同周')
                b['text'] = b['text'].replace('赛前 Pain FC', '周初 Pain FC').replace('Crownfield Athletic 赛前', 'Crownfield Athletic 周初')
        return a

    def test_actual_points_and_match_object_phrases_do_not_fail_as_rank_claims(self):
        for text in ('Ocean Stars 积分升至6分', 'Ocean Stars 积分升至六分',
                     'Ancient Dragonfire 客场3-1击败 Royal Junction，积14分从第10升至第5',
                     'Northern Lords 以1-0击败 Brookgate Wanderers，从第20升至第18',
                     'Celestial Vanguard 主场面对排名第9的 Polaris FC，赛后位列第2名'):
            with self.subTest(text=text): self.prose(text)

    def test_wrong_rank_and_wrong_origin_still_fail_after_object_fix(self):
        for text in ('Ancient Dragonfire 客场3-1击败 Royal Junction，从第10升至第6',
                     'Ancient Dragonfire 客场3-1击败 Royal Junction，从第9升至第5',
                     'Ocean Stars 位列第6名', 'Northern Lords 积分升至8分，仍列第20位'):
            with self.subTest(text=text), self.assertRaises(PressError): self.prose(text)

    def test_explicit_opponent_subject_and_anaphora(self):
        for text in ('Ancient Dragonfire 击败 Royal Junction，Royal Junction 下滑至第12名',
                     'Ancient Dragonfire 击败 Royal Junction，后者下滑至第12名',
                     'Ancient Dragonfire 击败 Royal Junction，前者升至第5名'):
            with self.subTest(text=text): self.prose(text)
        with self.assertRaises(PressError): self.prose('Ancient Dragonfire 击败 Royal Junction，后者升至第5名')
        self.prose('周初他们以16分排名第2，落后 Celestial Vanguard 三分')
        self.prose('Chaos Overture、Supersonic Knights、Yellowjackets FC 分别排在第1、第2、第3')

    def test_original_heading_gap_is_rejected(self):
        with self.assertRaisesRegex(PressError, '分差'):
            validate_article(self.response, self.packet, self.sources)
        self.prose('Premier Division：榜首三分之差易主', ['table:premier:team-pain-fc'])

    def test_original_false_first_loss_is_rejected_independently(self):
        a = self.corrected_copy()
        a['sections'][0]['blocks'][0]['text'] = self.response['sections'][0]['blocks'][0]['text']
        with self.assertRaisesRegex(PressError, '首败错误'):
            validate_article(a, self.packet, self.sources)
        self.prose('Celestial Vanguard 并非本赛季联赛首场失利')

    def test_first_lead_has_complete_week_history_not_just_an_upward_move(self):
        self.prose('Pain FC 本赛季首次登上榜首')
        with self.assertRaisesRegex(PressError, '首次领跑'):
            self.prose('Celestial Vanguard 本赛季首次登上榜首')

    def test_week_start_must_not_masquerade_as_match_pre_table(self):
        self.prose('周初 Pain FC 以16分排在第2名')
        self.prose('赛前 Pain FC 以16分排在第5名')
        self.prose('Crownfield Athletic 周初排名第4名')
        self.prose('Crownfield Athletic 赛前排名第5名')
        for text in ('赛前 Pain FC 以16分排在第2名', 'Crownfield Athletic 赛前排名第4名'):
            with self.subTest(text=text), self.assertRaisesRegex(PressError, '赛前名次'):
                self.prose(text)

    def test_week_start_and_cited_pre_match_origins_are_both_supported(self):
        self.prose('Northern Lords 从周初第19名升至第18名')
        self.prose('Northern Lords 从第20升至第18名')
        with self.assertRaisesRegex(PressError, '周初名次'):
            self.prose('Northern Lords 周初排名第20名')

    def test_chasing_gap_and_day_claims_are_not_just_headline_checks(self):
        self.prose('Crownfield Athletic 与榜首差距从3分缩小到0分')
        self.prose('Crownfield Athletic 与榜首差距零分')
        for text in ('Crownfield Athletic 与榜首差距从2分缩小到0分',
                     'Crownfield Athletic 与榜首差距从3分扩大到0分',
                     'Crownfield Athletic 与榜首差距两分'):
            with self.subTest(text=text), self.assertRaisesRegex(PressError, '分差'):
                self.prose(text)
        with self.assertRaisesRegex(PressError, '只有周次'):
            self.prose('两支球队在同一天失分')
        self.prose('两支球队在同周失分')

    def test_no_season_proof_fails_closed_without_rewriting_old_material(self):
        refs = ['match:m01-00084', 'before:m01-00084:team-celestial-vanguard']
        a = {'title': 'Celestial Vanguard 本赛季联赛首场失利', 'title_fact_ids': refs,
             'summary': '', 'summary_fact_ids': [], 'sections': [], 'used_topic_ids': []}
        with self.assertRaisesRegex(PressError, '缺少完整赛季证据'):
            validate_narrative(a, self.packet)

    def test_self_consistent_forged_prematch_rank_is_rejected_against_raw_order(self):
        p, s = deepcopy(self.packet), deepcopy(self.sources)
        f = next(f for f in p['facts'] if f['fact_id'] == 'before:m01-00469:team-northern-lords')
        f['data']['rank'] = 19
        s['records']['fact:' + f['fact_id']]['value']['result'] = deepcopy(f['data'])
        with self.assertRaisesRegex(PressError, '赛前名次'):
            validate_packet(p, s)

    def test_minimum_factual_edits_pass_whole_existing_draft_without_network(self):
        before = repository.canonical([self.packet, self.sources, self.response])
        with patch('socket.socket', side_effect=AssertionError('No network in offline regression')):
            checks = validate_article(self.corrected_copy(), self.packet, self.sources)
        self.assertEqual(checks['narrative_policy'], 'weekly-narrative/3')
        self.assertEqual(repository.canonical([self.packet, self.sources, self.response]), before)

    def test_style_budget_and_cliches_are_advisory_and_do_not_erase_distinct_streaks(self):
        advice = style_advice(self.response, self.packet)
        self.assertGreater(advice['match_results_cited_in_body'], 8)
        self.assertGreaterEqual(len(advice['advisory_flags']), 4)
        self.prose('Celestial Vanguard 联赛三连胜与七场不败同时终结')
        self.prose('Zero Returners 四连败终结，但九场不胜仍在延续')


class CompleteSeasonProofTests(PressSaveCase):
    fixture = refinement.WeeklyComparisonTests.fixture
    capture = refinement.WeeklyComparisonTests.capture
    scenario = refinement.WeeklyComparisonTests.scenario
    table = refinement.WeeklyComparisonTests.table
    leader_article = refinement.WeeklyComparisonTests.leader_article
    def test_complete_season_records_cannot_be_forged_even_with_matching_derived_source(self):
        m, (a, b, c, d) = self.scenario(changed=True)
        row = self.table(m, a)['data']
        self.assertEqual((row['season_record']['wins'], row['season_record']['losses']), (1, 1))
        self.assertEqual(row['season_record']['first_loss_match_id'], 'current-a')
        for key, value in (('losses', 0), ('first_loss_match_id', 'before-a'), ('first_lead_week', 2)):
            bad = deepcopy(m); f = self.table(bad, a)
            f['data']['season_record'][key] = value
            bad['sources']['records']['fact:' + f['fact_id']]['value']['result'] = deepcopy(f['data'])
            with self.subTest(key=key), self.assertRaisesRegex(PressError, '赛季胜平负'):
                validate_packet(bad['packet'], bad['sources'])

    def test_actual_first_loss_is_accepted_and_cup_loss_does_not_change_league_first(self):
        a, b, c, d = self.world()['season']['league']['premier'][:4]
        m = self.capture([self.fixture('cup-loss', 1, a, b, 0, 1, 'confluence'),
            self.fixture('league-win', 1, a, c, 1, 0), self.fixture('first-league-loss', 2, a, b, 0, 1)])
        p = m['packet']; refs = [f['fact_id'] for f in p['facts']]
        validate_article(article(p, p['entities']['teams'][a]['name_en']+' 本赛季联赛首场失利', refs), p, m['sources'])
        bad = p['entities']['teams'][a]['name_en']+' 本赛季各项赛事首败'
        with self.assertRaisesRegex(PressError, '各项赛事'):
            validate_article(article(p, bad, refs), p, m['sources'])

    def test_points_chinese_gap_static_and_direction_checks(self):
        m, (a, b, c, d) = self.scenario(); name = m['packet']['entities']['teams'][a]['name_en']
        for suffix in ('继续领跑，领先优势扩大至三分', '周初领先优势零分，目前领先第二名3分', '领先优势由零分扩大至三分'):
            validate_article(self.leader_article(m, a, name+suffix), m['packet'])
        for suffix in ('领先第二名两分', '领先优势扩大至两分', '领先优势由两分扩大至三分'):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(PressError, '分差'):
                validate_article(self.leader_article(m, a, name+suffix), m['packet'])
