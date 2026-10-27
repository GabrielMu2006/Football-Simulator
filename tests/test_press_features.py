"""Essential feature fact contracts. No network, production save or prose bans."""
from copy import deepcopy
import json
from football_simulator.game import rules
from football_simulator.press import PressError,feature_planning,feature_briefing
from football_simulator.press.features import (capture_snapshot,FeatureContext,phase_packet,competition_packet,
    draft_packet,top20_packet,transfer_packet,bracket_view,points_gap_basis)
from football_simulator.press.repository import canonical,digest
from football_simulator.persistence_v2 import store
from tests.test_press_export import PressSaveCase


class FeatureTests(PressSaveCase):
    def source(self):
        self.advance();conn=store.open_readonly('Press')
        try:return capture_snapshot(conn,1)
        finally:conn.close()

    def test_snapshot_reads_completed_real_facts_and_is_stable(self):
        world=self.world();source=self.source()
        self.assertEqual(len(source['matches']),20)
        self.assertNotIn('draft_pool',source['world'])
        self.assertTrue(all(source['world']['players'][r['player_id']]['is_real'] for r in source['player_match_stats']))
        after=self.world();FeatureContext(source);self.assertEqual(self.world(),after)
        self.assertGreater(after['version'],world['version'])
        keeper=next(r['player_id'] for r in source['player_match_stats'] if source['world']['players'][r['player_id']]['position']=='GK' and r['successful_saves'])
        card=FeatureContext(source).player_card(keeper)
        self.assertTrue(card['representative_matches']);self.assertIn('successful_saves',card['representative_matches'][0])

    def test_future_results_and_unfinished_events_are_rejected(self):
        source=self.source();bad=deepcopy(source);bad['matches'][0]['week']=2
        with self.assertRaises(PressError):FeatureContext(bad)
        late=deepcopy(source);late['world']['season']['week']=10
        with self.assertRaises(PressError):FeatureContext(late)
        for builder,args in [(phase_packet,('premier','halfway')),(competition_packet,('aureole',)),(top20_packet,()),(draft_packet,())]:
            with self.assertRaises(PressError):builder(source,*args)

    def test_transfer_directions_estimates_and_future_filter(self):
        source=self.source();world=source['world'];source['identity']['week']=27;world['season']['week']=28
        real=[p for p,v in world['players'].items() if v.get('is_real')];a=real[0];b=next(p for p in real if world['players'][p]['team_id']!=world['players'][a]['team_id'])
        ta,tb=world['players'][a]['team_id'],world['players'][b]['team_id']
        row={'trade_id':'actual','season':1,'week':26,'window':'winter','status':'approved','team_a':ta,'team_b':tb,'players_a':[a],'players_b':[b],'value_a':40,'value_b':45,'value_gap':5}
        world['transfer_history']=[row,{**row,'trade_id':'future','week':28}]
        before=digest(source);data=transfer_packet(source,'winter')['facts'][0]['data'];moves=data['focus_trades'][0]['moves']
        self.assertEqual(data['completed_trades'],1)
        self.assertEqual((moves[0]['from_team'],moves[0]['to_team']),(world['teams'][ta]['club_en'],world['teams'][tb]['club_en']))
        self.assertIn('估值',data['transaction_type']);self.assertEqual(digest(source),before)

    def test_top20_history_statuses_and_no_scoring_components_in_requests(self):
        source=self.source();world=source['world'];ids=[p for p,v in world['players'].items() if v.get('is_real')][:22]
        def rows(pids):return [{'player_id':p,'rank':i,'score':1234,'components':{'ability':80}} for i,p in enumerate(pids,1)]
        world['season_number']=3;world['season']['number']=3;world['season']['week']=50;world['season']['finalized']=True
        source['identity'].update(season=3,week=49)
        world['archives']=[{'season_number':1,'top20':rows(ids[:20])},{'season_number':2,'top20':rows(ids[1:21])}]
        world['season']['awards']={'top20':rows([ids[0],*ids[2:20],ids[21]])}
        packet=top20_packet(source);cards=packet['facts'][0]['data']['rankings']
        self.assertEqual(cards[0]['history_status'],'重返Top20');self.assertEqual(cards[-1]['history_status'],'首次入选Top20')
        plan=feature_planning.generation_plan(packet);outgoing=canonical([u['request'] for u in plan['sections']])
        self.assertNotIn('"score":',outgoing);self.assertNotIn('"components":',outgoing)
        self.assertEqual(len(plan['sections']),21)
        fragments=[]
        for u in plan['sections']:
            raw={'title':'测试标题','deck':'摘要','body':'完整正文。'}
            fragments.append((u['competition_id'],feature_planning.prepare(raw,packet,plan,u)))
        article=feature_planning.assemble(packet,plan,fragments)
        self.assertEqual([s['heading'] for s in article['sections'][1:]],[f"第{r['rank']}名 · {r['player']}" for r in cards])
        self.assertEqual(len(article['sections']),21)
        # A career collection across seasons is distinct from a season sweep.
        tid=next(iter(world['teams']));all_titles=['premier',*rules.CUP_IDS]
        world['edition_archives']={f'3:{c}':{'season_number':3,'competition_id':c,'championship':{'champion_team_id':tid}} for c in all_titles}
        self.assertIn('season_grand_slam',FeatureContext(source).title_history(tid,'premier'))
        world['edition_archives']['2:aureole']={**world['edition_archives'].pop('3:aureole'),'season_number':2}
        history=FeatureContext(source).title_history(tid,'premier')
        self.assertIn('career_grand_slam',history);self.assertNotIn('season_grand_slam',history)

    def test_top20_counts_distinguish_fourth_selection_return_and_unknown_editions(self):
        from football_simulator.press.features import top20_selection_history
        source=self.source();w=source['world'];ids=[p for p,v in w['players'].items() if v.get('is_real')][:3]
        def rows(pids):return [{'player_id':pid,'rank':n} for n,pid in enumerate(pids,1)]
        w['season_number']=4;w['season']['number']=4;w['season']['week']=50;source['identity'].update(season=4,week=49)
        w['archives']=[{'season_number':1,'top20':rows(ids[:2])},{'season_number':2,'top20':rows(ids[:1])},
                       {'season_number':3,'top20':rows(ids[:1])},{'season_number':4,'top20':rows(ids)}]
        ctx=FeatureContext(source)
        continuous,returned,first=[top20_selection_history(ctx,p,rows(ids)) for p in ids]
        self.assertEqual((continuous['total_selections'],continuous['consecutive_selections']),(4,4))
        self.assertEqual((returned['status'],returned['total_selections'],returned['consecutive_selections']),('重返Top20',2,1))
        self.assertEqual((first['status'],first['total_selections'],first['consecutive_selections']),('首次入选Top20',1,1))
        card={'history_status':continuous['status'],'selection_history':continuous}
        brief=feature_briefing.selection_history(card)
        self.assertEqual(brief['累计入选'],'4次');self.assertEqual(brief['截至本届连续入选'],'4届')
        self.assertEqual(len(brief['历届名次']),4)
        # Missing is unknown, even if the archive exists without its Top20.
        w['archives'][1].pop('top20')
        missing=top20_selection_history(FeatureContext(source),ids[0],rows(ids))
        self.assertIsNone(missing['total_selections']);self.assertIsNone(missing['consecutive_selections'])
        self.assertEqual((missing['missing_seasons'],missing['known_consecutive_selections']),([2],2))
        self.assertNotEqual(top20_selection_history(FeatureContext(source),ids[2],rows(ids))['status'],'首次入选Top20')

    def test_rank_milestones_separate_first_number_one_and_return_to_list(self):
        from football_simulator.press.features import top20_selection_history,stage_name
        source=self.source();w=source['world'];ids=[p for p,v in w['players'].items() if v['is_real']][:2]
        w['season_number']=4;w['season']['number']=4;w['season']['week']=50;source['identity'].update(season=4,week=49)
        w['archives']=[{'season_number':y,'top20':[{'player_id':ids[0],'rank':r}]+([{'player_id':ids[1],'rank':17}] if y==2 else [])}
                       for y,r in [(1,2),(2,3),(3,11)]]
        current=[{'player_id':ids[0],'rank':1},{'player_id':ids[1],'rank':3}]
        first=top20_selection_history(FeatureContext(source),ids[0],current)
        returned=top20_selection_history(FeatureContext(source),ids[1],current)
        self.assertIn('首次登顶Top20',first['rank_milestones'])
        self.assertEqual(first['prior_number_one_seasons'],[])
        self.assertEqual(returned['status'],'重返Top20')
        self.assertEqual(returned['previous_best_rank'],17)
        self.assertIn('刷新个人历史最佳名次',returned['rank_milestones'][0])
        w['archives'][0]['top20'][0]['rank']=1
        again=top20_selection_history(FeatureContext(source),ids[0],current)
        self.assertIn('第2次登顶Top20',again['rank_milestones'])
        self.assertEqual(stage_name('group_6'),'小组赛第6轮')

    def test_player_honors_follow_championship_roster_not_old_club_finish_or_current_club(self):
        from football_simulator.press.features import player_titles,personal_awards,team_results_for_player
        source=self.source();w=source['world']
        eligible=[t for t in w['teams'] if sum(p['is_real'] and p['team_id']==t for p in w['players'].values())>=2]
        a,b=eligible[:2]
        match=next(m for m in source['matches'] if a in (m['home_team_id'],m['away_team_id']))
        old=[pid for pid,p in w['players'].items() if p['is_real'] and p['team_id']==a]
        other=[pid for pid,p in w['players'].items() if p['is_real'] and p['team_id']==b]
        leaver,after_champion=old[:2];arriver,filler=other[:2]
        source['identity']['week']=52;w['season'].update(week=52,season_complete=True)
        w['players'][leaver]['team_id']=b;w['players'][arriver]['team_id']=a;w['players'][after_champion]['team_id']=b
        w['transfer_history']=[{'season':1,'week':27,'status':'approved','team_a':a,'team_b':b,'players_a':[leaver],'players_b':[arriver]},
                               {'season':1,'week':50,'status':'approved','team_a':a,'team_b':b,'players_a':[after_champion],'players_b':[filler]}]
        post={**match,'match_id':'post-transfer','week':35,'home_team_id':a,'away_team_id':b};source['matches'].append(post)
        for pid,tid in [(leaver,b),(arriver,a),(after_champion,a)]:
            stat=next(r for r in source['player_match_stats'] if r['player_id']==pid)
            source['player_match_stats'].append({**stat,'match_id':post['match_id'],'team_id':tid})
        edition={'season_number':1,'competition_id':'premier','completed_week':42,
                 'tables':{'premier':[{'team_id':a},{'team_id':b}]},
                 'championship':{'champion_team_id':a,'roster_player_ids':[arriver,after_champion]},
                 'awards':{'mvp':[{'player_id':leaver,'team_id':b}]}}
        w['edition_archives']={'1:premier':edition};ctx=FeatureContext(source)
        def champion_result(pid):
            return next(r for t in team_results_for_player(ctx,pid) if t['team']==ctx.team(a) for r in t['season_results'])
        before=digest(source)
        self.assertEqual(player_titles(ctx,leaver),[])
        self.assertEqual(personal_awards(ctx,leaver),[{'competition':'Premier Division','award':'赛事MVP'}])
        self.assertFalse(champion_result(leaver)['player_championship_credit']['awarded_to_player'])
        self.assertTrue(champion_result(arriver)['player_championship_credit']['awarded_to_player'])
        self.assertTrue(champion_result(after_champion)['player_championship_credit']['awarded_to_player'])
        self.assertEqual(player_titles(ctx,after_champion)[0]['team_at_championship'],ctx.team(a))
        def card(pid):
            return {**ctx.player_card(pid),'rank':1,'history_status':'首届榜单成员','previous_rank':None,'origin':{'origin':'初始球员'},
                    'personal_awards':personal_awards(ctx,pid),'team_titles':player_titles(ctx,pid),'teams_season_results':team_results_for_player(ctx,pid)}
        brief=feature_briefing.top20_player(card(leaver))
        self.assertEqual(brief['本季球员已获荣誉']['实际随队冠军'],['无'])
        self.assertIn(ctx.player(leaver)+'获得Premier Division赛事MVP',brief['本季球员已获荣誉']['个人奖项'][0])
        team=next(t for t in brief['曾效力球队的最终成绩（球队背景）'] if t['球队']==ctx.team(a))
        self.assertIn('未获得此项随队冠军',team['球队最终成绩'][0]);self.assertIn('第27周',team['本季实际转会'][0])
        self.assertEqual(digest(source),before)
        # An absent roster means unavailable, rather than a negative membership.
        edition['championship'].pop('roster_player_ids');ctx=FeatureContext(source)
        self.assertIsNone(champion_result(leaver)['player_championship_credit']['awarded_to_player'])
        self.assertIn('尚无法确认',feature_briefing.top20_player(card(leaver))['本季球员已获荣誉']['实际随队冠军'][0])

    def test_transfer_material_has_actual_window_rosters_and_package_positions(self):
        self.advance(23);opening=self.world()
        def counts(w,t):
            return {p:sum(w['players'][r['player_id']]['is_real'] and w['players'][r['player_id']]['position']==p for r in w['teams'][t]['roster'])
                    for p in ('GK','DF','MF','FW')}
        while self.world()['season']['week']<=27:
            self.advance()
            pending=self.world()['season']['pending'].get('transfer_review',[])
            if pending:self.service.apply_transfer_reviews('Press',{r['trade_id']:True for r in pending})
        closed=self.world()
        with store.open_readonly('Press') as conn:source=capture_snapshot(conn,27)
        packet=transfer_packet(source,'winter');data=packet['facts'][0]['data']
        self.assertGreater(data['completed_trades'],0)
        names={v['club_en']:tid for tid,v in closed['teams'].items()}
        for r in data['window_team_rosters']:
            tid=names[r['team']]
            self.assertEqual(r['window_open_real_counts'],counts(opening,tid))
            self.assertEqual(r['window_closed_real_counts'],counts(closed,tid))
        for r in data['focus_trades']:
            for impact in r['position_exchange']:
                outgoing=[p for p in r['moves'] if p['from_team']==impact['team']]
                incoming=[p for p in r['moves'] if p['to_team']==impact['team']]
                self.assertEqual(sum(impact['outgoing'].values()),len(outgoing))
                self.assertEqual(sum(impact['incoming'].values()),len(incoming))
        plan=feature_planning.generation_plan(packet)
        content=json.loads(plan['sections'][0]['request']['messages'][1]['content'])['content']
        self.assertIn('整个窗口',content['焦点球队整个窗口的阵容变化']['统计对象'])
        self.assertIn('年龄',content['未记录的人物与交易信息'])
        self.assertTrue(content['焦点交易'][0]['本笔交换的位置构成'])

    def test_unselected_has_no_actual_pick_and_chosen_pick_is_not_enumeration(self):
        source=self.source();w=source['world'];pid=next(p for p,v in w['players'].items() if v.get('is_real'));p=w['players'][pid]
        source['identity']['week']=49;w['season']['week']=50
        w['season']['draft_log']=[{'season':1,'results':[{'player_id':pid,'pick':7,'team_id':p['team_id'],'position':p['position'],'ability':70}],
                                 'undrafted':[{'name':'Unselected Rookie','position':'FW','reason':'user_skipped'}]}]
        packet=draft_packet(source);data=packet['facts'][0]['data'];self.assertEqual(data['picks'][0]['actual_pick'],7)
        self.assertIn('实际第7顺位',data['picks'][0]['selection_result'])
        self.assertIsInstance(data['picks'][0]['team_season_finish'],str)
        self.assertNotIn('actual_pick',data['unselected'][0]);self.assertEqual(data['previous_two_cohorts'],[])
        plan=feature_planning.generation_plan(packet)
        article=feature_planning.prepare({'title':'选秀','deck':'摘要','body':'正文。'},packet,plan,plan['sections'][0])
        self.assertEqual(article['report_id'],packet['meta']['report_id'])

    def test_draft_cohort_review_uses_the_historical_playoff_season(self):
        source=self.source();world=source['world']
        keeper=next(r for r in source['player_match_stats']
                    if world['players'][r['player_id']]['position']=='GK' and r['successful_saves'])
        match=next(m for m in source['matches'] if m['match_id']==keeper['match_id'])
        home,away=match['home_team_id'],match['away_team_id']
        match.update(season_number=2,competition='playoff',stage='promotion_final_leg_2')
        keeper['season_number']=2
        source['matches']=[match];source['player_match_stats']=[keeper]
        world['season_number']=3;world['season'].update(number=3,week=50)
        source['identity'].update(season=3,week=49)
        world['season']['draft_log']=[{'season':3,'results':[],'undrafted':[]}]
        world['archives']=[{'season_number':1,'draft':[{'season':1,'results':[{
            'player_id':keeper['player_id'],'team_id':keeper['team_id'],'pick':1,'ability':70}]}]},
            {'season_number':2}]
        world['edition_archives']={
            '2:second':{'tables':{'second':[{'team_id':away},{'team_id':home}]}},
            '3:second':{'tables':{'second':[{'team_id':t} for t in world['teams'] if t not in (home,away)]}}}
        before=digest(source)
        from football_simulator.press.features import venue_basis
        basis=venue_basis(FeatureContext(source),match)
        self.assertIn('第2赛季Second Division',basis)
        self.assertIn(FeatureContext(source).team(home)+'第2',basis)
        packet=draft_packet(source)
        review=packet['facts'][0]['data']['previous_two_cohorts'][0]
        self.assertEqual([(r['season'],r['appearances']) for r in review['by_season']],[(2,1),(3,0)])
        self.assertEqual(len(feature_planning.generation_plan(packet)['sections']),1)
        self.assertEqual(digest(source),before)

    def test_historical_venue_uses_its_own_previous_ranks_and_handles_missing_rows(self):
        from football_simulator.press.features import venue_basis
        source=self.source();world=source['world'];home,away,other=list(world['teams'])[:3]
        world['season_number']=3;world['season'].update(number=3,week=50)
        source['identity'].update(season=3,week=49)
        world['season']['previous']={'premier_order':[home,away],'second_order':[]}
        world['archives']=[{'season_number':1,'premier_order':[away,home],'second_order':[]}]
        fixture={'season_number':2,'competition':'aureole','stage':'quarterfinal_leg_2',
                 'home_team_id':home,'away_team_id':away,'venue_mode':'home'}
        ctx=FeatureContext(source);basis=venue_basis(ctx,fixture)
        self.assertIn('第1赛季总顺序',basis);self.assertIn(ctx.team(home)+'第2',basis)
        fixture['competition']='playoff'
        world['edition_archives']={'2:second':{'tables':{'second':[{'team_id':other}]}}}
        basis=venue_basis(ctx,fixture)
        self.assertIn('按第2赛季Second Division最终排名',basis)
        self.assertNotIn(ctx.team(home)+'第',basis)
        world['archives'].append({'season_number':2,'second_table':[{'team_id':away},{'team_id':home}]})
        world['edition_archives']={};ctx=FeatureContext(source)
        self.assertIn(ctx.team(home)+'第2',venue_basis(ctx,fixture))

    def test_award_counts_and_title_milestones_keep_their_scope(self):
        data={'awards':{'助攻王':[{'player':'Player A','value':14,'team_at_award':'New Club',
              'actual_teams_in_edition':[{'team':'Old Club','assists':11},{'team':'New Club','assists':3}]}]},
              'history':{'team':'New Club','competition':'Test Cup','known_title_seasons':[4],
                         'milestone':'首次赢得该赛事冠军'}}
        row=feature_briefing.awards(data)[0]
        self.assertIn('整届结果为14次助攻',row['正式奖项'])
        self.assertEqual(row['本届实际参赛球队及产出'],['Old Club：11次助攻','New Club：3次助攻'])
        self.assertIn('New Club在Test Cup',feature_briefing.title_history(data)[1])

    def test_competition_is_one_complete_request_not_two_incomplete_sections(self):
        source=self.source();ctx=FeatureContext(source);world=source['world']
        from football_simulator.press.features import make_packet
        champion=next(iter(world['teams']));team=ctx.team(champion)
        data={'competition':'Premier Division','edition':{'game_season':4,'edition_number':4,'first_game_season':1},
              'competition_format':'20队双循环38轮','completed_matches':380,'champion':team,
              'champion_campaign':{'matches':38,'W':19,'D':14,'L':5,'goals_for':58,'goals_against':31},
              'final_table':[{'rank':1,'team':team,'played':38,'wins':19,'draws':14,'losses':5,'points':71,
                              'goals_for':58,'goals_against':31,'goal_diff':27}],
              'winning_margin':3,'winning_margin_basis':'胜场积分57–63，平局积分14–5，合计领先3分。','champion_record':{'W':19,'D':14,'L':5},
              'champion_home_away_record':{'主场':{'W':10,'D':7,'L':2},'客场':{'W':9,'D':7,'L':3}},
              'qualification':{},'key_results':[], 'key_results_scope':'三场选材，非全部客场比赛',
              'awards':{'射手王':[{'player':'Player B','team_at_award':team,'value':32,'actual_teams_in_edition':[]}]},
              'history':{'team':team,'competition':'Premier Division','known_title_seasons':[4],
                         'milestone':'首次赢得该赛事冠军'}}
        packet=make_packet(ctx,'competition','premier',data);before=digest(packet)
        plan=feature_planning.generation_plan(packet);self.assertEqual(len(plan['sections']),1)
        content=json.loads(plan['sections'][0]['request']['messages'][1]['content'])['content']
        self.assertIn('9胜7平3负',content['冠军整季主客场纪录']['客场'])
        self.assertIn('正式个人奖项',content);self.assertIn('本届冠军的历史坐标',content)
        self.assertNotIn('other_section',content);self.assertEqual(digest(packet),before)
        other=next(t for t in world['teams'] if t!=champion)
        basis=points_gap_basis(ctx,{'team_id':champion,'wins':23,'draws':10,'points':79},
                              {'team_id':other,'wins':23,'draws':8,'points':77})
        self.assertIn('积分差完全来自平局积分',basis)

    def test_future_home_basis_is_previous_season_and_missing_stats_are_not_zero(self):
        source=self.source();world=source['world'];h,a=list(world['teams'])[:2]
        world['season']['previous']={'premier_order':[h,a],'second_order':[]}
        world['season']['fixtures'].append({'match_id':'future','competition':'ascendant','stage':'quarterfinal',
          'home_team_id':h,'away_team_id':a,'week':29,'leg':1,'venue_mode':'home'})
        bracket=bracket_view(FeatureContext(source),{'teams':[h,a],'stage':'quarterfinal','match_id':'future'})
        brief=feature_briefing.tie(bracket[0]);basis=brief['已确定的后续比赛'][0]['主场分配依据']
        self.assertIn('上季联赛排名',basis);self.assertIn('第1',basis)
        self.assertNotIn('已赛',brief)
        self.assertEqual(feature_briefing.statistics({'appearances':1,'goals':2,'assists':1},'MF'),
                         '1次出场、2球、1次助攻')
        played=next(m for m in source['matches'] if m['home_goals']!=m['away_goals'])
        winner=played['home_team_id'] if played['home_goals']>played['away_goals'] else played['away_team_id']
        decided={'teams':[played['home_team_id'],played['away_team_id']],'stage':'wb_semifinal',
                 'match_id':played['match_id'],'winner':winner}
        result=bracket_view(FeatureContext(source),decided)[0]
        self.assertIn('降入败者组，尚未出局',feature_briefing.tie(result)['结果意义'])
        self.assertEqual(feature_briefing.match(result['matches'][0])['赛事'],'Premier Division')
        decided['stage']='lb_round_2'
        self.assertIn('第二败，正式出局',bracket_view(FeatureContext(source),decided)[0]['outcome_meaning'])

    def test_shootout_after_level_aggregate_keeps_the_second_leg_defeat(self):
        source=self.source();base=source['matches'][0];w,l=base['home_team_id'],base['away_team_id']
        first={**base,'match_id':'leg-a','competition':'aureole','stage':'quarterfinal_leg_1',
               'home_team_id':l,'away_team_id':w,'home_goals':1,'away_goals':2,
               'home_goals_et':0,'away_goals_et':0,'home_pk':None,'away_pk':None,'extra_time_played':False}
        second={**first,'match_id':'leg-b','stage':'quarterfinal_leg_2','home_team_id':w,'away_team_id':l,
                'home_goals':2,'away_goals':3,'home_pk':8,'away_pk':7,'extra_time_played':True}
        source['matches'] += [first,second]
        tie={'teams':[w,l],'stage':'quarterfinal','legs':['leg-a','leg-b'],'winner':w}
        result=bracket_view(FeatureContext(source),tie)[0]
        self.assertIn('次回合场上2–3负于',result['decision_summary'])
        self.assertEqual([r['goals'] for r in result['aggregate']],[4,4])
        self.assertIn('通过点球决胜',result['decision_summary'])
