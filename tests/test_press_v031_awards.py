"""Award reporting must reflect frozen results and preserve old templates."""
import unittest

from football_simulator.press.features import edition_award_status
from football_simulator.press.local_templates import competition, top20, weekly
from football_simulator.press.local_templates.v5 import top20 as top20_v5


class V031AwardReportingTests(unittest.TestCase):
    def test_award_status_distinguishes_unset_vacant_and_shortfall(self):
        self.assertEqual(edition_award_status({'awards': {}})['status'], 'unknown')
        self.assertEqual(edition_award_status({'awards': {'rule_version': '0.3.1', 'evp_slots': 0}})['status'], 'not_offered')
        self.assertEqual(edition_award_status({'awards': {'rule_version': '0.3.1', 'evp_slots': 5}})['status'], 'incomplete')
        status = edition_award_status({'awards': {'rule_version': '0.3.1', 'evp_slots': 5, 'mvp': [], 'evp': [{'player_id': 'x'}]}})
        self.assertEqual(status['mvp_status'], 'vacant')
        self.assertEqual(status['evp_count'], 1)
        self.assertTrue(status['evp_shortfall'])

    def test_competition_report_names_real_evp_without_inventing_mvp(self):
        data = {'competition': 'Test Cup', 'champion': 'Club A', 'edition': {'game_season': 3},
                'awards': {'赛事MVP': [], '赛事EVP': [
                    {'player': 'Player A', 'position': 'DF', 'season_performance': {'position': 'DF', 'appearances': 3, 'goals': 0, 'assists': 0, 'successful_defenses': 7}},
                    {'player': 'Player B', 'position': 'GK', 'season_performance': {'position': 'GK', 'appearances': 3, 'successful_saves': 8, 'clean_sheets': 1}},
                ]}, 'award_status': {'status': 'awarded', 'mvp_status': 'vacant', 'evp_count': 2, 'evp_slots': 5, 'evp_shortfall': True}}
        packet = {'meta': {'report_id': 'test'}}
        article, _ = competition.render(packet, {'content': data, 'fact_ids': ['f']})
        text = ''.join(b['text'] for s in article['sections'] for b in s['blocks'])
        self.assertIn('MVP空缺', text)
        self.assertIn('Player A', text)
        self.assertIn('Player B', text)
        self.assertIn('2名EVP', text)
        self.assertNotIn('获评赛事MVP', text)

    def test_top20_distinguishes_mvp_from_evp_without_rescoring(self):
        card = {'rank': 2, 'player': 'Player A', 'position': 'DF', 'selection_history': {},
                'previous_rank': None, 'actual_teams': [], 'representative_matches': [],
                'personal_awards': [{'competition': 'Cup A', 'award': '赛事MVP'},
                                    {'competition': 'Cup B', 'award': '赛事EVP'},
                                    {'competition': 'Cup C', 'award': '赛事EVP'}],
                'team_titles': [], 'team_honor_summaries': [], 'teams_season_results': []}
        text = top20.player_paragraph(card)
        self.assertIn('1次获评赛事MVP（Cup A）', text)
        self.assertIn('2次入选赛事EVP（Cup B、Cup C）', text)
        self.assertNotIn('综合分', text)
        self.assertIn('个人奖杯中还有', top20_v5.player_paragraph(card))

    def test_weekly_reports_vacancy_and_only_actual_evp(self):
        text = weekly.award_paragraph({'mvp': None, 'evp': [{'player': 'Player A', 'team': 'Club A'}], 'evp_slots': 5})
        self.assertIn('MVP空缺', text)
        self.assertIn('Player A入选EVP', text)
        self.assertNotIn('获评本届赛事MVP', text)
        self.assertIn('没有评出EVP', weekly.award_paragraph({'mvp': None, 'evp': [], 'evp_slots': 5}))


if __name__ == '__main__':
    unittest.main()
