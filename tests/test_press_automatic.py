"""Necessary queue, snapshot, resumability and save-identity checks; no network."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from unittest.mock import patch

from football_simulator.persistence_v2 import store
from football_simulator.press import provider,repository
from football_simulator.press.library import activate
from football_simulator.press.feature_library import FeatureLibrary
from football_simulator.press.demo import simulated_transport
from tests.test_press_export import PressSaveCase


class AutomaticFeatureTests(PressSaveCase):
    def enabled(self):
        activate('Press',features=True)
        return FeatureLibrary(store.database_path('Press'))

    def test_activation_baselines_finished_events_and_is_opt_in(self):
        self.advance(19)
        with store.open_readonly('Press') as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='press_features'").fetchone())
        before=self.world();lib=self.enabled()
        self.assertEqual(self.world(),before)
        self.assertEqual(lib.feature_rows(),[])
        self.advance();store.write_world('Press',self.world())
        self.assertEqual(lib.feature_rows(),[])
        lib.install();self.assertEqual(lib.feature_rows(),[])

    def test_same_week_dedup_background_snapshot_archive_and_interruption(self):
        self.advance(18);lib=self.enabled();self.advance()
        rows=lib.feature_rows();self.assertEqual(len(rows),2)
        self.assertEqual({r['kind'] for r in rows},{'phase'})
        first,second=rows[0]['report_id'],rows[1]['report_id']
        store.write_world('Press',self.world());self.assertEqual(len(lib.feature_rows()),2)
        with lib.connection() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM press_feature_sources').fetchone()[0],1)
        job=lib.reserve_feature(first);version=job['packet']['meta']['source_world_version']
        entered=threading.Event();release=threading.Event();calls=[]
        def slow(request,key):
            calls.append(request);entered.set();release.wait(10)
            return simulated_transport(request,key)
        with ThreadPoolExecutor(max_workers=1) as pool:
            task=pool.submit(lib.generate,job,'offline-auto-test',transport=slow)
            try:
                self.assertTrue(entered.wait(5))
                self.advance()  # API wait owns no database transaction.
                self.assertGreater(self.world()['version'],version)
            finally:release.set()
            self.assertTrue(task.result(timeout=10))
        self.assertEqual(len(calls),1);self.assertIsNone(lib.reserve_feature(first))
        article=lib.read_feature(first,mark_read=False)['article']
        self.assertEqual(lib.read_feature(first)['article'],article)
        self.assertEqual(lib.unread_count(),0)
        stale=lib.reserve_feature(second)
        package=Path(self.tmp.name)/'automatic.fsarchive';store.export_package('Press',package)
        store.import_package(package,'Imported');copy=FeatureLibrary(store.database_path('Imported'));copy.install()
        with copy.connection() as conn:before=dict(conn.execute('SELECT * FROM press_features WHERE report_id=?',(second,)).fetchone())
        self.assertFalse(copy.generate(stale,'offline-auto-test',transport=lambda *_:self.fail('stale instance called API')))
        with copy.connection() as conn:self.assertEqual(dict(conn.execute('SELECT * FROM press_features WHERE report_id=?',(second,)).fetchone()),before)
        copy.recover()
        self.assertIsNone(copy.next_queued())
        self.assertIsNone(copy.reserve_feature(second))
        self.assertEqual(copy.read_feature(first,mark_read=False)['article'],article)

    def test_normal_close_pauses_at_boundary_and_reopen_reuses_completed_units(self):
        self.service.set_mode('Press','auto');self.advance(48)
        lib=self.enabled();self.advance()
        row=next(r for r in lib.feature_rows() if r['kind']=='top20')
        job=lib.reserve_feature(row['report_id']);calls=[]
        def transport(request,key):
            calls.append(request);return simulated_transport(request,key)
        self.assertFalse(lib.generate(job,'offline-auto-test',transport=transport,should_stop=lambda:bool(calls)))
        paused=next(r for r in lib.feature_rows() if r['report_id']==row['report_id'])
        self.assertEqual((paused['status'],paused['sections_done']),('paused',1))
        lib.recover()
        with lib.connection() as conn:
            first=conn.execute("SELECT fragment_sha FROM press_article_sections WHERE report_id=? AND status='ready'",(row['report_id'],)).fetchone()[0]
        resumed=lib.reserve_feature(row['report_id'])
        self.assertTrue(lib.generate(resumed,'offline-auto-test',transport=transport))
        self.assertEqual(len(calls),21)
        with lib.connection() as conn:
            self.assertEqual(conn.execute("SELECT fragment_sha FROM press_article_sections WHERE owner=? AND status='ready'",(job['token'],)).fetchone()[0],first)
        self.assertIsNone(lib.reserve_feature(row['report_id']))

    def test_top20_partial_resume_reuses_success_and_frozen_plan(self):
        self.service.set_mode('Press','auto');self.advance(48)
        lib=self.enabled();self.advance()
        row=next(r for r in lib.feature_rows() if r['kind']=='top20')
        job=lib.reserve_feature(row['report_id']);calls=[]
        def interrupted(request,key):
            calls.append(request)
            if len(calls)==2:raise provider.ProviderError('rate_limited')
            return simulated_transport(request,key)
        before=repository.digest(self.world())
        self.assertFalse(lib.generate(job,'offline-auto-test',transport=interrupted))
        self.assertIsNone(lib.reserve_feature(row['report_id']))
        with lib.connection() as conn:
            saved=dict(conn.execute("SELECT * FROM press_article_sections WHERE report_id=? AND status='ready'",(row['report_id'],)).fetchone())
        retry=lib.reserve_feature(row['report_id'],retry=True)
        with patch('football_simulator.press.feature_planning.generation_plan',side_effect=AssertionError('must reuse original plan')):
            self.assertTrue(lib.generate(retry,'offline-auto-test',transport=lambda req,key:(calls.append(req) or simulated_transport(req,key))))
        self.assertEqual(len(calls),22)
        self.assertIsNone(lib.reserve_feature(row['report_id'],retry=True))
        with lib.connection() as conn:
            kept=conn.execute('SELECT fragment_sha FROM press_article_sections WHERE owner=? AND competition_id=?',(saved['owner'],saved['competition_id'])).fetchone()[0]
            metadata=json.loads(conn.execute('SELECT metadata_json FROM press_features WHERE report_id=?',(row['report_id'],)).fetchone()[0])
        self.assertEqual(kept,saved['fragment_sha']);self.assertEqual(metadata['api_requests_this_attempt'],20)
        self.assertEqual(len(lib.read_feature(row['report_id'])['article']['sections']),21)
        self.assertEqual(repository.digest(self.world()),before)
