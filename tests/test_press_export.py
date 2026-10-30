"""Offline reporting integration against newly created, isolated game saves."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.press import PressError
from football_simulator.press import repository
from football_simulator.press.editorial import request_preview, render_markdown
from football_simulator.press.validation import contract, validate_article, validate_packet

ROOT = Path(__file__).resolve().parents[1]


class PressSaveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fs_press_test_")
        self.old_root = runtime._DATA_ROOT_OVERRIDE
        runtime.set_data_root_override(Path(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(runtime.set_data_root_override, self.old_root)
        self.service = GameService(rng_factory=lambda: random.Random(871))
        self.service.create_save("Press")
        self.service.initialize_save("Press")

    def world(self):
        return store.load_world("Press", readonly=True)

    def advance(self, count=1):
        for _ in range(count):
            self.service.advance_week("Press")


class WeeklyExportTests(PressSaveCase):
    def test_compressed_sources_preserve_hash_and_old_text_remains_readable(self):
        repository.enable("Press"); self.advance()
        material = repository.load_week("Press", 1, 1)
        with store._connect(store.database_path("Press")) as conn:
            row = conn.execute("SELECT source_json,source_sha256 FROM press_weeks WHERE season=1 AND week=1").fetchone()
            raw = repository.canonical(material["sources"])
            self.assertIsInstance(row["source_json"], bytes)
            self.assertLess(len(row["source_json"]), len(raw.encode("utf-8")))
            self.assertEqual(row["source_sha256"], repository.digest(material["sources"]))
            conn.execute("UPDATE press_weeks SET source_json=? WHERE season=1 AND week=1", (raw,))
        self.assertEqual(repository.load_week("Press", 1, 1), material)
        with store._connect(store.database_path("Press")) as conn:
            conn.execute("UPDATE press_weeks SET source_json=? WHERE season=1 AND week=1", (b"FSZ1broken",))
        with self.assertRaises(PressError):
            repository.load_week("Press", 1, 1)

    def test_disabled_saves_get_no_press_tables_or_material(self):
        with patch("football_simulator.press.builder.build_packet") as builder:
            self.advance()
            builder.assert_not_called()
        conn = store.open_readonly("Press")
        try:
            self.assertFalse(repository.enabled(conn))
        finally:
            conn.close()
        self.assertEqual(repository.list_weeks("Press"), [])

    def test_enable_is_explicit_idempotent_and_does_not_change_world_or_pointer(self):
        before = self.world()
        pointer = runtime.current_save_path().read_bytes()
        first = repository.enable("Press")
        self.assertEqual(first, repository.enable("Press"))
        self.assertEqual(self.world(), before)
        self.assertEqual(pointer, runtime.current_save_path().read_bytes())
        self.assertEqual(repository.list_weeks("Press"), [])

    def test_only_new_weeks_capture_without_backfill(self):
        self.advance(2)
        repository.enable("Press")
        store.write_world("Press", self.world())
        with self.assertRaises(PressError):
            repository.load_week("Press", 1, 2)
        self.advance()
        rows = repository.list_weeks("Press")
        self.assertEqual([(r["season"], r["week"]) for r in rows], [(1, 3)])
        m = repository.load_week("Press", 1, 3)
        self.assertEqual(m["packet"]["meta"]["source_mode"], "production_export")
        self.assertEqual(m["packet"]["meta"]["source_world_version"], self.world()["version"])

    def test_complete_raw_week_includes_all_real_appearances_and_stadiums(self):
        repository.enable("Press"); self.advance()
        material = repository.load_week("Press", 1, 1); packet = material["packet"]
        self.assertEqual(validate_packet(packet, material["sources"])["matches_this_week"], 20)
        conn = store.open_readonly("Press")
        try:
            expected = {r[0:4] for r in conn.execute("SELECT p.match_id,p.player_id,p.goals,p.assists FROM player_match_stats p JOIN players x ON x.player_id=p.player_id WHERE p.appeared=1 AND x.is_real=1")}
            goals = conn.execute("SELECT SUM(home_goals+away_goals+COALESCE(home_goals_et,0)+COALESCE(away_goals_et,0)) FROM matches").fetchone()[0]
        finally:
            conn.close()
        actual = {(f["data"]["match_id"], f["data"]["player_id"], f["data"]["goals"], f["data"]["assists"]) for f in packet["facts"] if f["kind"] == "player_match"}
        self.assertEqual(actual, expected)
        self.assertEqual(packet["week_summary"]["goals_excluding_shootouts"], goals)
        self.assertTrue(all(p["is_real"] for p in packet["entities"]["players"].values()))
        self.assertTrue(packet["entities"]["stadiums"])
        self.assertNotIn("save_name", packet["meta"])
        self.assertNotIn(str(runtime.data_root()), repository.canonical(packet))
        from football_simulator.game.world import league_tables
        for comp, rows in league_tables(self.world()).items():
            exported={f['data']['team_id']:f['data']['rank'] for f in packet['facts'] if f['kind']=='table_row' and f['competition_id']==comp}
            self.assertEqual(exported,{r['team_id']:i+1 for i,r in enumerate(rows)})

    def test_late_export_after_changes_reads_exact_old_bytes(self):
        repository.enable("Press"); self.advance()
        old = repository.load_week("Press", 1, 1)
        self.advance(2)
        w = self.world(); pid = next(p for p,v in w["players"].items() if v["is_real"])
        w["players"][pid]["ability"] = 99
        # Current data changes must never be reinterpreted as old match data.
        w["players"][pid]["market_value"] = 999
        store.write_world("Press", w)
        self.assertEqual(repository.load_week("Press", 1, 1), old)
        db_before = hashlib.sha256(store.database_path("Press").read_bytes()).hexdigest()
        out = repository.export_week("Press", 1, 1, Path(self.tmp.name)/"export")
        self.assertEqual(json.loads((out/"weekly.json").read_text()), old["packet"])
        self.assertEqual(hashlib.sha256(store.database_path("Press").read_bytes()).hexdigest(), db_before)
        with self.assertRaises(FileExistsError): repository.export_week("Press",1,1,out)
        with self.assertRaises(PressError): repository.export_week("Press",1,1,store.save_dir("Press")/"out")

    def test_repeated_capture_is_immutable_and_world_writes_do_not_delete_reports(self):
        repository.enable("Press"); self.advance()
        old = repository.load_week("Press",1,1)
        store.write_world("Press", self.world(), completed_week=1)
        self.assertEqual(repository.load_week("Press",1,1), old)

    def test_capture_failure_rolls_back_world_and_match_projections(self):
        repository.enable("Press")
        old = self.world()
        with patch("football_simulator.press.builder.build_packet", side_effect=PressError("injected failure")):
            with self.assertRaises(PressError): self.advance()
        self.assertEqual(old, self.world())
        self.assertEqual(repository.list_weeks("Press"), [])
        self.advance()
        self.assertEqual(len(repository.list_weeks("Press")), 1)

    def test_package_export_import_preserves_snapshots_without_backfill(self):
        repository.enable("Press"); self.advance()
        old = repository.load_week("Press",1,1)
        archive = store.export_package("Press",Path(self.tmp.name)/"portable.fsarchive")
        store.import_package(archive,"Copy")
        self.assertEqual(repository.load_week("Copy",1,1),old)
        with self.assertRaises(PressError): repository.load_week("Copy",1,2)
        original_instance=repository.enable('Press')['instance_id']
        copy_instance=repository.enable('Copy')['instance_id']
        self.assertNotEqual(original_instance,copy_instance)
        self.service.advance_week('Copy')
        self.assertEqual(repository.load_week('Copy',1,2)['packet']['meta']['save_instance_id'],copy_instance)
        self.assertEqual(repository.load_week('Press',1,1),old)

    def test_two_saves_same_season_are_separate_and_no_current_pointer_lookup(self):
        repository.enable("Press"); self.advance()
        old=repository.load_week("Press",1,1)
        self.service.create_save("Other"); self.service.initialize_save("Other");repository.enable("Other")
        self.service.advance_week("Other")
        other=repository.load_week("Other",1,1)
        self.assertNotEqual(old["packet"]["meta"]["world_id"],other["packet"]["meta"]["world_id"])
        self.assertNotEqual(old["packet"]["meta"]["save_instance_id"],other["packet"]["meta"]["save_instance_id"])
        self.assertEqual(repository.load_week("Press",1,1),old)

    def test_opt_in_does_not_change_game_results_or_rng(self):
        # Independent equal RNG streams, with only one world opting into press.
        for name in ("With", "Without"):
            rng=random.Random(544)
            service=GameService(rng_factory=lambda rng=rng:rng)
            service.create_save(name);service.initialize_save(name)
            if name=="With": repository.enable(name)
            for _ in range(3): service.advance_week(name)
        def sporting(name):
            w=store.load_world(name,readonly=True)
            return {k:v for k,v in w.items() if k not in ("world_id","save_name","created_at","_fixture_index")}
        self.assertEqual(sporting("With"),sporting("Without"))

    def test_snapshot_integrity_detects_local_corruption(self):
        repository.enable("Press");self.advance()
        conn=store._connect(store.database_path("Press"))
        with conn:conn.execute("UPDATE press_weeks SET packet_json='{}'")
        conn.close()
        with self.assertRaises(PressError):repository.load_week("Press",1,1)

    def test_request_preview_only_and_no_match_stops_before_request(self):
        repository.enable("Press");self.advance()
        material=repository.load_week("Press",1,1)
        with patch("socket.socket",side_effect=AssertionError("Network forbidden")):
            request=request_preview(material["packet"],material["sources"])
        self.assertTrue(request["offline_preview"])
        self.assertEqual(request["request"]["model"],"deepseek-flash")
        self.assertEqual(len(request["request"]["messages"]),2)
        self.assertNotIn("{{WEEKLY_PACKAGE_JSON}}",request["request"]["messages"][1]["content"])
        self.assertNotIn("api_key",request)
        empty=json.loads((ROOT/'docs/press/examples/weekly-no-matches.json').read_text())
        with self.assertRaises(PressError):request_preview(empty)

    def test_cli_and_export_reject_protected_or_unmarked_roots(self):
        from football_simulator.press.__main__ import sandbox
        with self.assertRaises(PressError):sandbox(runtime.user_data_root(),create=True)
        with self.assertRaises(PressError):sandbox(Path(self.tmp.name),create=False)
        with self.assertRaises(PressError):repository.protect_output(ROOT/'release/macos/press-export')
        with self.assertRaises(PressError):repository.protect_output(runtime.data_root()/'saves/Other/export')


class ContractTests(unittest.TestCase):
    def test_bundled_schemas_match_documented_contracts(self):
        for name in ("weekly-input.schema.json","article-output.schema.json"):
            self.assertEqual((ROOT/'docs/press'/name).read_bytes(),(ROOT/'football_simulator/press/contracts'/name).read_bytes())

    def test_development_samples_and_manual_article_remain_labelled(self):
        root=ROOT/'docs/press/examples'
        for name in ("weekly-feature","weekly-ordinary","weekly-no-matches"):
            p=json.loads((root/(name+'.json')).read_text());validate_packet(p)
            self.assertEqual(p["meta"]["source_mode"],"synthetic_example")
        p=json.loads((root/'weekly-feature.json').read_text());a=json.loads((root/'weekly-feature.article.json').read_text())
        text,checks=render_markdown(a,p)
        self.assertIn("非 DeepSeek 实测",text)
        self.assertIn("看台声音（虚构）",text)
        self.assertEqual(checks["prose_semantics"],"requires_independent_reading")

    def test_boolean_is_not_integer_and_unknown_fields_fail(self):
        p=json.loads((ROOT/'docs/press/examples/weekly-feature.json').read_text())
        p['meta']['completed_week']=True
        with self.assertRaises(PressError):contract(p,'weekly-input.schema.json')
        p=json.loads((ROOT/'docs/press/examples/weekly-feature.json').read_text());p['api_key']='forbidden'
        with self.assertRaises(PressError):validate_packet(p)

    def test_output_wrong_scores_status_and_missing_fiction_labels_fail(self):
        root=ROOT/'docs/press/examples'
        p=json.loads((root/'weekly-feature.json').read_text());a=json.loads((root/'weekly-feature.article.json').read_text())
        bad=deepcopy(a);bad['title']='Hannya Masks 9–8 Kingsmeadow Rovers'
        with self.assertRaises(PressError):validate_article(bad,p)
        bad=deepcopy(a);bad['title']='Hannya Masks 2–1 Kingsmeadow Rovers'
        with self.assertRaises(PressError):validate_article(bad,p)
        bad=deepcopy(a);bad['sections'][-1]['blocks'][-1]['label']=None
        with self.assertRaises(PressError):validate_article(bad,p)
        bad=deepcopy(a);bad['title_fact_ids']=['does-not-exist']
        with self.assertRaises(PressError):validate_article(bad,p)
        bad=deepcopy(a)
        for section in bad['sections']:
            for block in section['blocks']:
                block['fact_ids']=[fid for fid in block['fact_ids'] if fid!='f-former-club']
        bad['title_fact_ids']=[fid for fid in bad['title_fact_ids'] if fid!='f-former-club']
        bad['summary_fact_ids']=[fid for fid in bad['summary_fact_ids'] if fid!='f-former-club']
        for section in bad['sections']:section['heading_fact_ids']=[fid for fid in section['heading_fact_ids'] if fid!='f-former-club']
        with self.assertRaises(PressError):validate_article(bad,p)


class LaterSeasonIntegrationTests(PressSaveCase):
    """Existing TEST fixture copied into our sandbox, never a user's save."""
    def resolve(self, service, name):
        pending=service.load_world(name)['season']['pending']
        if pending.get('ability_review'):
            service.apply_ability_review(name,{});return True
        if pending.get('draft',{}).get('status')=='awaiting_input':
            service.apply_draft(name);return True
        if pending.get('transfer_review'):
            service.apply_transfer_reviews(name,{});return True
        return False

    def test_real_cup_progress_and_next_season_keep_all_captured_material(self):
        store.import_package(ROOT/'tests/fixtures_vnext/s2-week25.fsarchive','CupTrace')
        service=GameService(rng_factory=lambda:random.Random(692),edition_archives=True)
        service.prepare_save('CupTrace')
        repository.enable('CupTrace')
        retained={}
        for _ in range(100):
            if self.resolve(service,'CupTrace'):continue
            w=service.load_world('CupTrace')
            if w['season']['season_complete']:break
            week=service.advance_week('CupTrace')['week']
            if week in (28,30,31,32,35,37,42,47,48,49,52):
                material=repository.load_week('CupTrace',2,week)
                validate_packet(material['packet'],material['sources'])
                retained[week]=deepcopy(material)
        self.assertEqual(set(retained),{28,30,31,32,35,37,42,47,48,49,52})
        # Real engine paths have correct state even after a whole season moves on.
        for week,material in retained.items():
            self.assertEqual(repository.load_week('CupTrace',2,week),material)
        self.assertTrue(any(f['kind']=='tie_result' for f in retained[32]['packet']['facts']))
        self.assertTrue(any(f['kind']=='competition_status' and f['data']['status']=='champion' for f in retained[30]['packet']['facts']))
        self.assertTrue(any(f['kind']=='record' for f in retained[28]['packet']['facts']))
        service.start_next_season('CupTrace')
        service.advance_week('CupTrace')
        m=repository.load_week('CupTrace',3,1)
        self.assertEqual(m['packet']['meta']['season'],3)
        self.assertEqual(m['packet']['meta']['completed_week'],1)
        self.assertEqual(repository.load_week('CupTrace',2,52),retained[52])
        # Opt-in began at S2W25, so historical S1/S2W24 remain unavailable.
        with self.assertRaises(PressError):repository.load_week('CupTrace',2,24)

    def test_cross_season_league_streak_and_record_comparison_from_real_history(self):
        store.import_package(ROOT/'tests/fixtures_vnext/s3-finalized.fsarchive','History')
        service=GameService(rng_factory=lambda:random.Random(981),edition_archives=True)
        service.prepare_save('History')
        repository.enable('History')
        while self.resolve(service,'History'):pass
        w=service.load_world('History')
        # Keep the archived S1-S3 SQL facts; move the isolated world to S4.
        for _ in range(10):
            if w['season']['season_complete']:break
            service.advance_week('History')
            while self.resolve(service,'History'):pass
            w=service.load_world('History')
        service.start_next_season('History')
        w=service.load_world('History')
        # Choose a team with a known completed league suffix >=4 in S3.
        conn=store.open_readonly('History')
        try:old=[dict(r) for r in conn.execute("SELECT * FROM matches WHERE season_number=3 AND competition IN ('premier','second') ORDER BY week,match_id")]
        finally:conn.close()
        candidate=None
        for tid in w['teams']:
            tail=[]
            for m in reversed(old):
                if tid not in (m['home_team_id'],m['away_team_id']):continue
                gf,ga=(m['home_goals'],m['away_goals']) if tid==m['home_team_id'] else (m['away_goals'],m['home_goals'])
                if gf<ga:break
                tail.append(m)
            if len(tail)>=4:candidate=(tid,len(tail));break
        self.assertIsNotNone(candidate)
        tid,prior=candidate;comp=next(c for c,ids in w['season']['league'].items() if tid in ids)
        old_comp=next(m['competition'] for m in reversed(old) if tid in (m['home_team_id'],m['away_team_id']))
        # Force a level change in this controlled branch, independent of playoffs.
        target='second' if old_comp=='premier' else 'premier'
        if comp!=target:
            swap=w['season']['league'][target][0]
            w['season']['league'][comp].remove(tid);w['season']['league'][target].remove(swap)
            w['season']['league'][target].append(tid);w['season']['league'][comp].append(swap)
            comp=target
        opponent=next(t for t in w['season']['league'][comp] if t!=tid)
        f={'match_id':'holdout-cross-season','season_number':4,'competition':comp,'stage':'league','week':1,'status':'completed','home_team_id':tid,'away_team_id':opponent,
           'stadium_id':w['teams'][tid]['stadium_id'],'venue_mode':'home','home_goals':0,'away_goals':0,'home_pk':None,'away_pk':None,'events':[],'player_facts':{}}
        w['season']['fixtures']=[f];w['season']['week']=2
        store.write_world('History',w,completed_week=1)
        material=repository.load_week('History',4,1)
        suffix=next(f['data'] for f in material['packet']['facts'] if f['kind']=='streak' and f['data']['team_id']==tid and f['data']['kind']=='unbeaten' and f['data']['scope']=='league_all_levels')
        self.assertEqual((suffix['before'],suffix['after']),(prior,prior+1))
        self.assertTrue(suffix['crosses_seasons']);self.assertTrue(suffix['history_complete'])
        self.assertEqual(set(suffix['competition_ids']),{'premier','second'})

    def test_first_equalled_and_broken_finishes_use_completed_archive_results(self):
        store.import_package(ROOT/'tests/fixtures_vnext/s3-finalized.fsarchive','Records')
        service=GameService(rng_factory=lambda:random.Random(872),edition_archives=True)
        service.prepare_save('Records')
        while self.resolve(service,'Records'):pass
        for _ in range(10):
            if service.load_world('Records')['season']['season_complete']:break
            service.advance_week('Records')
            while self.resolve(service,'Records'):pass
        service.start_next_season('Records');repository.enable('Records')
        w=service.load_world('Records');teams=list(w['teams'])
        best={t:min((a.get('cup_placements',{}).get('aureole',{}).get(t,999) for a in w['archives']),default=999) for t in teams}
        cases=[('first',lambda n:n==999),('equalled',lambda n:n==3),('broken',lambda n:3<n<999)]
        for i,(expected,condition) in enumerate(cases):
            a=next(t for t,n in best.items() if condition(n));b=next(t for t in teams if t!=a);c=next(t for t in teams if t not in (a,b));week=32+i*2
            def f(mid,when,home,away,hg,ag,leg):
                return {'match_id':mid,'season_number':4,'competition':'aureole','stage':f'quarterfinal_leg_{leg}','week':when,'status':'completed','home_team_id':home,'away_team_id':away,
                        'stadium_id':w['teams'][home]['stadium_id'],'venue_mode':'home','home_goals':hg,'away_goals':ag,'events':[],'player_facts':{},'leg':leg,'tie_id':f'tie-{i}'}
            one,two=f'record-{i}-1',f'record-{i}-2'
            w['season']['fixtures']=[f(one,week-1,b,a,0,2,1),f(two,week,a,b,1,0,2)];w['season']['week']=week+1
            w['season']['cups']={'aureole':{'quarterfinals':[{'tie_id':f'tie-{i}','stage':'quarterfinal','teams':[a,b],'legs':[one,two],'winner':a}],
                                          'semifinals':[{'tie_id':f'future-{i}','stage':'semifinal','teams':[a,c],'legs':[f'future-{i}-1',f'future-{i}-2'],'winner':None}]}}
            store.write_world('Records',w,completed_week=week)
            material=repository.load_week('Records',4,week)
            result=next(f['data'] for f in material['packet']['facts'] if f['kind']=='record' and f['data']['entity_id']==a)
            self.assertEqual(result['comparison'],expected)
            self.assertTrue(result['complete_history'])
