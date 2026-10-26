"""Independent controlled sporting cases, not the prompt's development examples.

Expected outcomes below are hand-derived from scores/state, not snapshots of
the exporter's output. These test facts; they are NOT model writing acceptance.
"""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from football_simulator.game import rules
from football_simulator.persistence_v2 import store
from football_simulator.press import PressError
from football_simulator.press import repository
from football_simulator.press.builder import strict_title
from football_simulator.press.validation import validate_packet, validate_article
from tests.test_press_export import PressSaveCase


class SportingHoldoutTests(PressSaveCase):
    def fixture(self, mid, week, home, away, hg=1, ag=0, comp="premier", stage="league", **extra):
        w=self.world()
        result={"match_id":mid,"season_number":w["season_number"],"competition":comp,"stage":stage,"week":week,"status":"completed",
                "home_team_id":home,"away_team_id":away,"stadium_id":w["teams"][home]["stadium_id"],"venue_mode":"home","home_goals":hg,"away_goals":ag,
                "home_goals_et":0,"away_goals_et":0,"home_pk":None,"away_pk":None,"extra_time_played":False,"events":[],"shootout":[],"player_facts":{},
                "winner_team_id":home if hg>ag else away if ag>hg else None}
        result.update(extra)
        return result

    def ids(self):
        return self.world()["season"]["league"]["premier"][:4]

    def capture(self, fixtures, week, cups=None, mutate=None):
        repository.enable("Press")
        w=self.world();w["season"]["fixtures"]=fixtures;w["season"]["week"]=min(52,week+1)
        if week==52:w["season"]["season_complete"]=True
        if cups is not None:w["season"]["cups"]=cups
        if mutate:mutate(w)
        store.write_world("Press",w,completed_week=week)
        return repository.load_week("Press",w["season_number"],week)

    @staticmethod
    def facts(material,kind):
        return [f for f in material['packet']['facts'] if f['kind']==kind]

    def test_shootout_win_is_field_draw_and_extends_unbeaten_and_winless(self):
        a,b,c,_=self.ids()
        previous=[self.fixture(f"early-{i}",i,a,c,1,1) for i in range(1,6)]
        pk=self.fixture("pk",6,a,b,1,1,"starpath","swiss_5",home_pk=5,away_pk=4,winner_team_id=a,extra_time_played=True)
        state={'records':{a:[3,0],b:[0,3]},'qualified':[a],'eliminated':[b],'rounds':[]}
        m=self.capture(previous+[pk],6,{'starpath':state})
        match=next(f['data'] for f in self.facts(m,'match_result') if f['data']['match_id']=='pk')
        self.assertIsNone(match['on_field_winner_team_id']);self.assertEqual(match['shootout_winner_team_id'],a)
        self.assertEqual(m['packet']['week_summary']['goals_excluding_shootouts'],2)
        for kind in ('unbeaten','winless'):
            row=next(f['data'] for f in self.facts(m,'streak') if f['data']['team_id']==a and f['data']['kind']==kind)
            self.assertEqual((row['before'],row['after'],row['ended']),(5,6,False))
        status={f['data']['team_id']:f['data']['status'] for f in self.facts(m,'competition_status')}
        self.assertEqual(status[a],'qualified');self.assertEqual(status[b],'eliminated')
        qualification=next(f['data'] for f in self.facts(m,'swiss_record') if f['data']['team_id']==a)
        self.assertEqual((qualification['wins'],qualification['losses']),(3,0))
        self.assertEqual(qualification['basis'],'swiss_ties_including_shootout_wins')

    def test_draw_ends_wins_but_not_unbeaten_and_same_week_order_is_engine_order(self):
        a,b,c,_=self.ids()
        prior=[self.fixture(f"prior-{i}",i,a,c) for i in range(1,6)]
        # League is processed before the cup regardless of these IDs or ordinal.
        league=self.fixture('zzz-league',6,a,b,0,0,ordinal=99)
        cup=self.fixture('aaa-cup',6,a,b,0,1,'confluence','quarterfinal',ordinal=0)
        m=self.capture(prior+[league,cup],6)
        rows=[f['data'] for f in self.facts(m,'streak') if f['data']['team_id']==a and f['data']['scope']=='all_official']
        win=next(r for r in rows if r['affected_match_id']=='zzz-league' and r['kind']=='wins')
        unbeaten=next(r for r in rows if r['affected_match_id']=='aaa-cup' and r['kind']=='unbeaten')
        self.assertEqual((win['before'],win['after'],win['ended']),(5,0,True))
        self.assertEqual((unbeaten['before'],unbeaten['after'],unbeaten['ended']),(6,0,True))
        league_unbeaten=next(r for r in rows if r['affected_match_id']=='zzz-league' and r['kind']=='unbeaten')
        self.assertEqual(league_unbeaten['after'],6)

    def test_losing_return_leg_can_advance_and_winning_leg_can_exit(self):
        a,b,_,_=self.ids()
        first=self.fixture('leg-1',31,a,b,2,0,'aureole','quarterfinal_leg_1',leg=1,tie_id='tie-ab')
        second=self.fixture('leg-2',32,b,a,1,0,'aureole','quarterfinal_leg_2',leg=2,tie_id='tie-ab',winner_team_id=a)
        tie={'tie_id':'tie-ab','stage':'quarterfinal','teams':[a,b],'legs':['leg-1','leg-2'],'winner':a}
        future={'tie_id':'future','stage':'semifinal','teams':[a,self.ids()[2]],'legs':['future-1','future-2'],'winner':None}
        state={'quarterfinals':[tie],'semifinals':[future]}
        m=self.capture([first,second],32,{'aureole':state})
        agg=self.facts(m,'tie_result')[0]['data']
        self.assertEqual((agg['team_a_goals'],agg['team_b_goals'],agg['advancing_team_id']),(2,1,a))
        field=next(f['data'] for f in self.facts(m,'match_result') if f['data']['match_id']=='leg-2')
        self.assertEqual(field['on_field_winner_team_id'],b)
        status={f['data']['team_id']:f['data']['status'] for f in self.facts(m,'competition_status')}
        self.assertEqual(status[a],'qualified');self.assertEqual(status[b],'eliminated')
        self.assertEqual(m['packet']['week_summary']['completed_matches'],1)

    def test_first_leg_lead_never_qualifies(self):
        a,b,_,_=self.ids();first=self.fixture('leg-1',31,a,b,4,0,'starpath','quarterfinal_leg_1',leg=1,tie_id='tie-ab')
        state={'quarterfinals':[{'tie_id':'tie-ab','stage':'quarterfinal','teams':[a,b],'legs':['leg-1','leg-2'],'winner':None}]}
        m=self.capture([first],31,{'starpath':state})
        self.assertIsNone(self.facts(m,'tie_result')[0]['data']['advancing_team_id'])
        self.assertTrue(all(f['data']['status']=='awaiting_tie_second_leg' for f in self.facts(m,'competition_status')))

    def test_aggregate_tied_can_need_shootout_after_an_unequal_return_leg(self):
        a,b,_,_=self.ids()
        first=self.fixture('leg-1',31,a,b,2,1,'aureole','quarterfinal_leg_1',leg=1,tie_id='ab')
        second=self.fixture('leg-2',32,b,a,1,0,'aureole','quarterfinal_leg_2',leg=2,tie_id='ab',home_pk=3,away_pk=4,winner_team_id=a,extra_time_played=True)
        state={'quarterfinals':[{'tie_id':'ab','stage':'quarterfinal','teams':[a,b],'legs':['leg-1','leg-2'],'winner':a}]}
        m=self.capture([first,second],32,{'aureole':state})
        result=self.facts(m,'tie_result')[0]['data']
        self.assertEqual((result['team_a_goals'],result['team_b_goals'],result['advancing_team_id']),(2,2,a))
        second=next(f['data'] for f in self.facts(m,'match_result') if f['data']['match_id']=='leg-2')
        self.assertEqual(second['on_field_winner_team_id'],b)
        self.assertEqual(second['shootout_winner_team_id'],a)

    def test_double_elimination_first_loss_and_second_loss_differ(self):
        a,b,c,_=self.ids()
        first=self.fixture('wb',35,a,b,0,1,'ember','wb_quarterfinal')
        first_tie={'stage':'wb_quarterfinal','teams':[a,b],'match_id':'wb','winner':b}
        state={'loss_stage':{a:1,b:None},'wb_quarterfinal':[first_tie],'eliminated':{}}
        m=self.capture([first],35,{'ember':state})
        status=next(f['data'] for f in self.facts(m,'competition_status') if f['data']['team_id']==a)
        self.assertEqual(status['status'],'entered_losers_bracket')
        final=self.fixture('lb',37,a,c,0,2,'ember','lb_round_1')
        state['lb_round_1']=[{'stage':'lb_round_1','teams':[a,c],'match_id':'lb','winner':c}]
        w=self.world();w['season']['fixtures']=[first,final];w['season']['cups']={'ember':state};w['season']['week']=38
        store.write_world('Press',w,completed_week=37)
        m=repository.load_week('Press',1,37)
        self.assertEqual(next(f['data']['status'] for f in self.facts(m,'competition_status') if f['data']['team_id']==a),'eliminated')

    def test_raw_article_cannot_turn_first_double_elimination_loss_into_exit(self):
        a,b,_,_=self.ids();first=self.fixture('wb',35,a,b,0,1,'ember','wb_quarterfinal')
        state={'loss_stage':{a:1},'wb_quarterfinal':[{'stage':'wb_quarterfinal','teams':[a,b],'match_id':'wb','winner':b}]}
        m=self.capture([first],35,{'ember':state});p=m['packet']
        fid=next(f['fact_id'] for f in self.facts(m,'competition_status') if f['data']['team_id']==a)
        name=p['entities']['teams'][a]['name_en'];text=name+'出局'
        article={'schema_version':'press.article/0.1-draft','report_id':p['meta']['report_id'],'title':text,'title_fact_ids':[fid], 'summary':text,'summary_fact_ids':[fid],
                 'used_topic_ids':[p['editorial']['topics'][0]['topic_id']],'sections':[{'heading':text,'heading_fact_ids':[fid],'blocks':[{'kind':'body','label':None,'text':text,'fact_ids':[fid]}]}]}
        with self.assertRaises(PressError):validate_article(article,p)

    def test_negative_packet_future_scores_refs_and_missing_history(self):
        a,b,_,_=self.ids();m=self.capture([self.fixture('one',1,a,b,2,1)],1)
        for mutate in (
            lambda p:p['facts'][0]['time'].update(week=2),
            lambda p:p['facts'][0]['data']['regulation'].update(home=9),
            lambda p:p['week_summary'].update(goals_excluding_shootouts=8),
            lambda p:p['facts'][0]['team_ids'].append('unknown-team'),
            lambda p:p['editorial']['topics'][0]['required_fact_ids'].append('missing'),
        ):
            bad=deepcopy(m['packet']);mutate(bad)
            with self.assertRaises(PressError):validate_packet(bad,m['sources'])

    def test_no_matches_week_26_and_week_52_freeze_explicit_completed_week(self):
        m=self.capture([],26)
        self.assertEqual(m['packet']['meta']['input_validity'],'no_matches')
        self.assertEqual(m['packet']['meta']['completed_week'],26)
        w=self.world();w['season']['week']=52;w['season']['season_complete']=True
        store.write_world('Press',w,completed_week=52)
        self.assertEqual(repository.load_week('Press',1,52)['packet']['meta']['completed_week'],52)

    def test_premature_or_unfinished_boundary_is_rejected(self):
        repository.enable('Press')
        with self.assertRaises(PressError):store.write_world('Press',self.world(),completed_week=1)
        w=self.world();w['season']['week']=2
        with self.assertRaises(PressError):store.write_world('Press',w,completed_week=1)

    def test_upset_uses_pre_match_table_not_winner_post_match_points(self):
        a,b,c,d=self.ids()
        old=[]
        remaining=[t for t in self.world()['season']['league']['premier'] if t not in (a,b,c,d)]
        for week in range(1,6):
            old += [self.fixture(f'aa-{week}',week,a,c,2,0),self.fixture(f'bb-{week}',week,b,d,0,2)]
            old += [self.fixture(f'other-{week}-{i}',week,remaining[i],remaining[i+1],1,1) for i in range(0,16,2)]
        current=self.fixture('upset',6,b,a,2,0)
        material=self.capture(old+[current],6)
        judgement=next(j for j in material['packet']['editorial']['judgements'] if j['label']=='upset')
        referenced={f['fact_id']:f for f in material['packet']['facts']}
        rows=[referenced[fid]['data'] for fid in judgement['basis_fact_ids'] if referenced[fid]['kind']=='table_row']
        before_b=next(r for r in rows if r['team_id']==b)
        self.assertEqual((before_b['points'],before_b['played'],before_b['timing']),(0,5,'before_match'))
        after_b=next(f['data'] for f in material['packet']['facts'] if f['kind']=='table_row' and f['data']['team_id']==b and f['data']['timing']=='end_of_week')
        self.assertEqual(after_b['points'],3)

    def test_strict_title_checks_all_rivals_and_refuses_possible_points_tie(self):
        teams=[f't{i}' for i in range(20)]
        schedule=[{'home_team_id':teams[i],'away_team_id':teams[j]} for i in range(20) for j in range(20) if i!=j]
        rows=[{'team_id':t,'points':60 if i==0 else 3,'played':30} for i,t in enumerate(teams)]
        self.assertTrue(strict_title(rows,schedule,rows[0]))
        rows[-1].update(points=36,played=30) # 36 + 8*3 == 60, not clinched.
        self.assertFalse(strict_title(rows,schedule,rows[0]))
        rows[-1]['points']=37
        self.assertFalse(strict_title(rows,schedule,rows[0]))

    def test_actual_transfer_direction_old_club_and_goals_come_from_sql(self):
        w=self.world();pid=next(pid for pid,p in w['players'].items() if p['is_real']);new=w['players'][pid]['team_id'];old=next(t for t in w['season']['league']['premier'] if t!=new)
        f=self.fixture('return',2,old,new,0,2,'confluence','round_16')
        f['player_facts']={pid:{'team_id':new,'appeared':True,'goals':2,'assists':0,'chances_created':0,'successful_defenses':0,'successful_saves':0,'clean_sheets':0}}
        def change(w):
            w['transfer_history']=[{'trade_id':'move-1','season':1,'week':1,'team_a':old,'team_b':new,'players_a':[pid],'players_b':[],'status':'approved'}]
        m=self.capture([f],2,mutate=change)
        former=self.facts(m,'former_club')[0]['data']
        self.assertEqual((former['former_team_id'],former['current_team_id']),(old,new))
        self.assertEqual(self.facts(m,'player_match')[0]['data']['goals'],2)
        self.assertTrue(any(t['event_type'].startswith('former_club:') for t in m['packet']['editorial']['topics']))

    def test_missing_archives_cannot_be_reported_as_first_ever(self):
        a,b,c,_=self.ids();f=self.fixture('semi',28,a,b,2,0,'confluence','semifinal')
        def change(w):w['season_number']=2;w['season']['number']=2;f['season_number']=2;w['season']['fixtures']=[f]
        state={'rounds':[{'ties':[{'stage':'semifinal','teams':[a,b],'match_id':'semi','winner':a}]}],'final':{'stage':'final','teams':[a,c],'match_id':'future','winner':None}}
        m=self.capture([f],28,{'confluence':state},change)
        self.assertEqual(self.facts(m,'record'),[])
        self.assertTrue(any('历史' in s for s in m['packet']['meta']['missing_material']))

    def test_former_club_does_not_invent_direct_transfer_after_multiple_clubs(self):
        w=self.world();pid=next(pid for pid,p in w['players'].items() if p['is_real']);current=w['players'][pid]['team_id']
        old,middle=[t for t in w['teams'] if t!=current][:2]
        f=self.fixture('multi-hop',3,old,current,0,2,'confluence','round_16')
        f['player_facts']={pid:{'team_id':current,'appeared':True,'goals':2,'assists':0,'chances_created':0,'successful_defenses':0,'successful_saves':0,'clean_sheets':0}}
        def change(w):
            w['transfer_history']=[{'trade_id':'old-to-middle','season':1,'week':1,'team_a':old,'team_b':middle,'players_a':[pid],'players_b':[],'status':'approved'},
                                   {'trade_id':'middle-to-new','season':1,'week':2,'team_a':middle,'team_b':current,'players_a':[pid],'players_b':[],'status':'approved'}]
        m=self.capture([f],3,mutate=change);data=self.facts(m,'former_club')[0]['data']
        self.assertFalse(data['is_immediate_previous_club'])
        self.assertEqual(data['departure_destination_team_id'],middle)
        self.assertEqual(data['current_team_id'],current)

    def test_source_changed_qualification_and_aggregate_are_rejected(self):
        a,b,_,_=self.ids()
        f1=self.fixture('l1',31,a,b,2,0,'aureole','quarterfinal_leg_1',leg=1,tie_id='ab')
        f2=self.fixture('l2',32,b,a,1,0,'aureole','quarterfinal_leg_2',leg=2,tie_id='ab')
        state={'quarterfinals':[{'tie_id':'ab','stage':'quarterfinal','teams':[a,b],'legs':['l1','l2'],'winner':a}]}
        m=self.capture([f1,f2],32,{'aureole':state})
        bad=deepcopy(m['packet']);f=next(f for f in bad['facts'] if f['kind']=='tie_result');f['data']['team_a_goals']=9
        with self.assertRaises(PressError):validate_packet(bad,m['sources'])
