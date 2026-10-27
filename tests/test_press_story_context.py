"""Evidence boundaries for connected local news; no provider or prose scoring."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from football_simulator.press import repository
from football_simulator.press.story_context import (career_snapshot,arrival,history_events,performance_events,
    game_notes,swiss_turnarounds,transfer_notes,enrich_feature)
from football_simulator.press.local_templates.narrative import arrival_line,history_lines
from football_simulator.press.local_templates.engine import freeze_plan,render


def world():
    return {'teams':{t:{'club_en':t} for t in 'ABCD'},
            'players':{'p':{'name':'Player','position':'FW','is_real':True,'team_id':'A'},
                       'q':{'name':'Peer','position':'GK','is_real':True,'team_id':'B'}},
            'archives':[],'season':{'transfers':[],'draft_log':[],'fixtures':[]},'transfer_history':[],'edition_archives':{}}


def trade(name,season,week,a,b,pa,pb=()):
    return {'trade_id':name,'season':season,'week':week,'team_a':a,'team_b':b,'players_a':list(pa),'players_b':list(pb),'status':'approved'}


def game(name,week,home='A',away='B',h=1,a=0,season=1,competition='premier',stage='league',tie_id=None):
    return {'match_id':name,'season_number':season,'week':week,'home_team_id':home,'away_team_id':away,
            'home_goals':h,'away_goals':a,'competition':competition,'stage':stage,'tie_id':tie_id,
            'winner_team_id':home if h>a else away if a>h else None}


def stat(mid,pid='p',team='A',goals=1,assists=0,clean=0):
    return {'match_id':mid,'player_id':pid,'team_id':team,'appeared':1,'goals':goals,'assists':assists,'clean_sheets':clean}


class CareerStoryTests(unittest.TestCase):
    def test_sparse_service_and_final_history_cover_older_than_three_seasons(self):
        w=world();w['players']['q']['team_id']='A';w['transfer_history']=[trade('leave',8,52,'B','A',['p'])]
        games=[game('now',1,season=8)];rows=[stat('now',team='B')]
        service={'complete':True,'team_seasons':[{'player_id':'p','team_id':'B','season_number':y,'first_week':1} for y in range(1,9)]}
        final=game('old-final',48,home='B',away='A',season=2,competition='aureole',stage='final_leg_2')
        historical={'matches':[final],'player_match_stats':[stat('old-final',team='B'),stat('old-final','q','A',goals=0)]}
        move={'player_id':'p','from_team_id':'B','to_team_id':'A','season':8,'week':52}
        note=transfer_notes(w,games,rows,move,8,52,service_history=service,final_history=historical)
        self.assertEqual(note['first_departure_after_seasons'],8);self.assertEqual(note['former_final_opponent']['season'],2)
        limited=transfer_notes(w,games,rows,move,8,52)
        self.assertNotIn('first_departure_after_seasons',limited);self.assertNotIn('former_final_opponent',limited)

    def test_legacy_missing_status_is_completed_but_undated_moves_are_unknown(self):
        w=world();legacy=trade('old',1,26,'A','B',['p']);legacy.pop('status');legacy['season_number']=legacy.pop('season')
        w['transfer_history']=[legacy];c=career_snapshot(w,1,27)
        self.assertIsNotNone(arrival(c,'p','B',1,27))
        w['transfer_history'].append({'team_a':'B','team_b':'C','players_a':['p'],'players_b':None})
        c=career_snapshot(w,1,27);self.assertIsNone(arrival(c,'p','B',1,27))
        self.assertEqual(c['uncertain_players'],['p'])

    def test_long_first_departure_requires_coverage_and_no_earlier_move(self):
        w=world();w['transfer_history']=[trade('leave',3,52,'A','B',['p'])]
        games=[game(str(y),1,season=y) for y in (1,2,3)];rows=[stat(str(y)) for y in (1,2,3)]
        move={'player_id':'p','from_team_id':'A','to_team_id':'B','season':3,'week':52}
        with patch('football_simulator.press.story_context.complete_match_history',return_value=True):
            self.assertEqual(transfer_notes(w,games,rows,move,3,52)['first_departure_after_seasons'],3)
            w['transfer_history'].insert(0,trade('earlier',1,52,'A','C',['p']))
            self.assertNotIn('first_departure_after_seasons',transfer_notes(w,games,rows,move,3,52))
        w['transfer_history']=w['transfer_history'][-1:]
        self.assertNotIn('first_departure_after_seasons',transfer_notes(w,games,rows,move,3,52))

    def test_rolling_52_weeks_and_historical_team_ownership(self):
        w=world();w['transfer_history']=[trade('join',1,52,'A','B',['p']),trade('future',3,1,'B','C',['p'])]
        c=career_snapshot(w,3,2)
        self.assertIsNotNone(arrival(c,'p','B',2,52))
        self.assertIsNone(arrival(c,'p','B',3,1)) # 53 weeks; the future move must not be imported early
        self.assertIsNone(arrival(c,'p','C',2,51))
        self.assertIsNone(arrival(c,'p','B',1,52)) # matches occur before that week's window review

    def test_return_to_former_club_and_actual_draft_club(self):
        w=world();w['archives']=[{'season_number':1,'draft':[{'season':1,'results':[{'player_id':'p','team_id':'A','pick':4}]}]}]
        w['transfer_history']=[trade('leave',2,26,'A','B',['p']),trade('return',2,52,'B','A',['p'])]
        c=career_snapshot(w,3,1);a=arrival(c,'p','A',3,1)
        self.assertTrue(a['return_to_former_club']);self.assertTrue(a['return_to_draft_club']);self.assertEqual(a['draft_origin']['pick'],4)
        a.update(from_team='B',to_team='A');text=arrival_line('Player',a)
        self.assertIn('重返A',text);self.assertIn('第4顺位',text)
        w['archives']=[];a=arrival(career_snapshot(w,3,1),'p','A',3,1)
        self.assertTrue(a['return_to_former_club']);self.assertFalse(a['return_to_draft_club'])

    def test_rejected_future_and_duplicate_moves_do_not_change_return_identity(self):
        w=world();move=trade('join',1,26,'A','B',['p']);w['transfer_history']=[move,move,{**trade('no',1,27,'B','A',['p']),'status':'rejected'},trade('late',2,26,'B','A',['p'])]
        before=deepcopy(w);c=career_snapshot(w,1,27)
        self.assertEqual(len(c['moves']),1);self.assertFalse(arrival(c,'p','B',1,27)['return_to_former_club']);self.assertEqual(w,before)

    def test_exchange_duel_requires_actual_appearances_for_both_new_teams(self):
        w=world();w['transfer_history']=[trade('swap',1,1,'A','B',['p'],['q'])]
        g=game('m',2);rows=[stat('m','p','B'),stat('m','q','A',goals=0)]
        notes=game_notes(w,[g],rows,1,2)['m'];self.assertEqual(len(notes['exchange_duels']),1)
        rows[1]['appeared']=0
        self.assertEqual(game_notes(w,[g],rows,1,2)['m']['exchange_duels'],[])

    def test_final_opponents_become_teammates_only_from_real_final_appearances(self):
        w=world();w['players']['q']['team_id']='A';w['players']['p']['team_id']='A'
        w['transfer_history']=[trade('swap',2,52,'B','A',['p'])]
        g=game('f',48,'B','A',season=2,competition='aureole',stage='final_leg_2')
        rows=[stat('f','p','B'),stat('f','q','A',goals=0)]
        note=transfer_notes(w,[g],rows,{'player_id':'p','from_team_id':'B','to_team_id':'A','season':2,'week':52},2,52)
        self.assertEqual(note['former_final_opponent']['player'],'Peer')
        rows[1]['appeared']=0
        self.assertNotIn('former_final_opponent',transfer_notes(w,[g],rows,{'player_id':'p','from_team_id':'B','to_team_id':'A','season':2,'week':52},2,52))


class HistoryStoryTests(unittest.TestCase):
    def context(self,w,games=(),rows=(),season=1,week=6):
        return SimpleNamespace(world=w,season=season,week=week,matches={g['match_id']:g for g in games},stats=list(rows),
            archives={a['season_number']:a for a in w['archives']},team=lambda tid:tid,player=lambda pid:w['players'][pid]['name'],
            games=lambda comp=None,year=None:[g for g in games if g['season_number']==(season if year is None else year) and (comp is None or g['competition']==comp)])

    def test_transfer_comparison_uses_declared_before_after_observations(self):
        from football_simulator.game import rules
        w=world();w['transfer_history']=[trade('swap',1,3,'A','B',['p'],['q'])]
        games=[game(str(i),i,h=int(i!=2),a=int(i==2)) for i in range(1,7)]
        c=self.context(w,games);content={'competition':rules.display_name('premier')};before=deepcopy(w)
        enrich_feature(c,'phase',content);r=next(t for t in content['trade_followups'][0]['teams'] if t['team']=='A')
        self.assertEqual((r['before']['matches'],r['after']['matches']),(3,3))
        self.assertEqual((r['before']['wins'],r['after']['wins']),(2,3));self.assertEqual(w,before)

    def test_losers_bracket_title_and_low_seed_promotion_are_proved_separately(self):
        from football_simulator.game import rules
        w=world();w['season']['cups']={'ember':{'loss_stage':{'A':'wb_round_1'}}}
        c=self.context(w);data={'competition':rules.display_name('ember'),'champion':'A'}
        enrich_feature(c,'competition',data);self.assertIn('losers_champion',[r['kind'] for r in data['narrative_history']])
        w['season']['final_tables']={'second':[{'team_id':t} for t in ('B','C','D','B','C','A')]}
        data={'competition':rules.display_name('playoff'),'champion':'A'};enrich_feature(c,'competition',data)
        self.assertEqual(next(r for r in data['narrative_history'] if r['kind']=='playoff_low_seed')['league_rank'],6)

    def test_different_draft_classes_are_named_from_actual_roster(self):
        w=world();w['players']['q']['team_id']='A'
        w['archives']=[{'season_number':1,'draft':[{'season':1,'results':[{'player_id':'q','team_id':'A','pick':5}]}]}]
        w['season']['draft_log']=[{'season':2,'results':[{'player_id':'p','team_id':'A','pick':1}]}]
        c=self.context(w,season=2,week=49);data={'picks':[{'player':'Player','team':'A','actual_pick':1}]}
        enrich_feature(c,'draft',data);self.assertEqual(data['picks'][0]['high_pick_teammates'][0]['pick'],5)
        w['players']['q']['team_id']='B';data={'picks':[{'player':'Player','team':'A','actual_pick':1}]};enrich_feature(c,'draft',data)
        self.assertEqual(data['picks'][0]['high_pick_teammates'],[])

    def test_late_pick_contrast_and_club_group_preserve_official_top20_order(self):
        w=world()
        for n in range(1,22):w['players']['r'+str(n)]={'name':'R'+str(n),'position':'FW','is_real':True,'team_id':'A'}
        official=[{'rank':n,'player_id':'r'+str(n+1)} for n in range(1,21)]
        w['season']['awards']={'top20':official}
        w['archives']=[{'season_number':1,'draft':[{'season':1,'results':[{'player_id':'r1','team_id':'A','pick':1},{'player_id':'r7','team_id':'B','pick':7}]}]}]
        cards=[{'rank':r['rank'],'player':'R'+r['player_id'][1:],'actual_teams':[{'team':'A','appearances':2}]} for r in official]
        data={'rankings':cards,'changes':{}};before=deepcopy(cards)
        enrich_feature(self.context(w,season=2,week=49),'top20',data)
        self.assertEqual(data['rankings'],before);self.assertEqual(len(data['club_top20_groups'][0]['players']),20)
        low=next(r for r in data['draft_class_contrasts'] if r['player']=='R7')
        self.assertEqual((low['pick'],low['rank'],low['first_rank']),(7,6,None))

    def test_final_champion_takes_precedence_over_eliminated_defender(self):
        from football_simulator.press.local_templates.weekly import heading_for
        g={'competition':'Ember Cup','stage':'总决赛','home':'A','away':'B','field_score_home_away':{'home':0,'away':1},
           'qualification_outcomes':[{'team':'A','status':'eliminated'},{'team':'B','status':'champion'}],
           'defending_champions':['A']}
        self.assertEqual(heading_for({'kind':'match_story','content':{'matches':[g]}}),'B赢得Ember Cup')
        g['stage']='八强';g['qualification_outcomes']=[{'team':'A','status':'eliminated'}]
        self.assertIn('提前出局',heading_for({'kind':'match_story','content':{'matches':[g]}}))

    def test_defense_ended_consecutive_titles_drought_and_rotating_champions(self):
        w=world();w['edition_archives']={f'{y}:premier':{'championship':{'champion_team_id':team}} for y,team in [(1,'A'),(2,'A')]}
        events=history_events(w,3,'premier',champion='B')
        ended=next(e for e in events if e['kind']=='defense_ended');self.assertEqual(ended['previous_consecutive_titles'],2)
        self.assertIn('2连冠也到此为止',history_lines(events))
        self.assertEqual(next(e for e in history_events(w,3,'premier',champion='A') if e['kind']=='title')['consecutive_titles'],3)
        w['edition_archives']={f'{y}:premier':{'championship':{'champion_team_id':team}} for y,team in [(1,'A'),(2,'B'),(3,'C')]}
        self.assertEqual(next(e for e in history_events(w,4,'premier',champion='D') if e['kind']=='different_champions')['editions'],4)
        self.assertEqual(next(e for e in history_events(w,4,'premier',champion='A') if e['kind']=='drought_ended')['seasons_without_title'],2)

    def test_missing_title_edition_does_not_prove_first_or_exact_run(self):
        w=world();w['edition_archives']={'2:premier':{'championship':{'champion_team_id':'A'}}}
        events=history_events(w,3,'premier',champion='B')
        self.assertEqual([e['kind'] for e in events],['defense_ended']);self.assertIsNone(events[0]['previous_consecutive_titles'])
        self.assertEqual(history_events(w,3,'premier'),[]) # losing one game or entering losers' bracket is not elimination

    def test_swiss_stories_use_the_order_of_actual_qualification_wins(self):
        w=world();games=[game('m'+str(i),i,h=0 if i<=2 else 1,a=1 if i<=2 else 0,competition='starpath',stage='swiss_'+str(i)) for i in range(1,6)]
        stories=swiss_turnarounds(w,games)
        self.assertTrue(next(s for s in stories if s['team']=='A')['qualified'])
        self.assertFalse(next(s for s in stories if s['team']=='B')['qualified'])
        self.assertEqual(swiss_turnarounds(w,games[:4]),[])

    def test_firsts_and_centuries_require_complete_stats_history(self):
        w=world();games=[game('m'+str(i),i%38+1,season=i//38+1) for i in range(100)]
        rows=[stat(g['match_id'],goals=3 if i==99 else 1) for i,g in enumerate(games)]
        complete=performance_events(w,games,rows,3,complete=True)['m99']
        self.assertTrue({'appearances','goals','first_hat_trick'}<={r['kind'] for r in complete})
        partial=performance_events(w,games,rows,3,complete=False)['m99']
        self.assertFalse({'appearances','goals','first_hat_trick'} & {r['kind'] for r in partial})

    def test_personal_streaks_are_per_appearance_and_preserve_zero_as_an_end(self):
        w=world();games=[game('m'+str(i),i+1) for i in range(4)]
        rows=[stat(g['match_id'],goals=int(i<3)) for i,g in enumerate(games)]
        events=performance_events(w,games,rows,1,complete=True)
        self.assertEqual(next(e for e in events['m3'] if e['kind']=='scoring_streak')['count'],3)
        self.assertTrue(next(e for e in events['m3'] if e['kind']=='scoring_streak')['ended'])

    def test_two_leg_contribution_and_club_scoring_are_not_confused(self):
        w=world();games=[game('l1',1,competition='aureole',stage='quarterfinal_leg_1',tie_id='t'),game('l2',2,competition='aureole',stage='quarterfinal_leg_2',tie_id='t')]
        rows=[stat('l1',goals=0,assists=1),stat('l2',goals=1)]
        notes=game_notes(w,games,rows,1,2)
        self.assertEqual(notes['l1']['two_leg_contributors'],[])
        self.assertEqual(notes['l2']['two_leg_contributors'][0]['first_assists'],1)
        games=[game('a',1,home='A',away='B'),game('b',2,home='A',away='B'),game('c',3,home='A',away='B')]
        rows=[stat('a',team='B'),stat('b',team='A'),stat('c',team='A')]
        self.assertEqual(game_notes(w,games,rows,1,3)['c']['opponent_scoring_runs'],[])
        rows[0]['team_id']='A'
        self.assertEqual(game_notes(w,games,rows,1,3)['c']['opponent_scoring_runs'][0]['last_meetings'],3)

    def test_same_week_later_game_cannot_extend_earlier_game_streak(self):
        w=world();games=[game('a',1),game('b',2),game('c',2,competition='aureole',stage='final')]
        rows=[stat(g['match_id']) for g in games]
        self.assertEqual(game_notes(w,games,rows,1,2)['b']['opponent_scoring_runs'],[])

    def test_frozen_version_two_does_not_use_rewritten_renderers(self):
        content={'window':'summer','completed_trades':0,'focus_trades':[],'other_trades':[]}
        packet={'meta':{'report_id':'test','report_type':'transfer','source_sha256':'source','season':3},'facts':[{'fact_id':'feature:test','data':content}]}
        plan=freeze_plan(packet,version='local-news/2');expected=render(packet,plan)
        with patch('football_simulator.press.local_templates.transfer.render',side_effect=AssertionError('new renderer called')):
            self.assertEqual(render(packet,plan),expected)
