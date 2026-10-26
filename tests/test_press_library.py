"""Focused offline integration: saved article lifecycle, isolation and explicit retry."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

from football_simulator import runtime
from football_simulator.persistence_v2 import store
from football_simulator.press import PressError, repository
from football_simulator.press.demo import simulated_transport
from football_simulator.press.library import Library, activate
from football_simulator.press.planning import prepare_story_article
from tests.test_press_export import PressSaveCase

KEY = 'offline-test-credential-only-981'


class LibraryTests(PressSaveCase):
    def ready(self):
        library = activate('Press')
        self.advance()
        return library

    def test_only_enabled_library_uses_concurrent_read_write_journal(self):
        lib = self.ready()
        with lib.connection() as reader:
            self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            old = reader.execute('SELECT COUNT(*) FROM press_articles').fetchone()[0]
            # A live page snapshot must not prevent the worker reserving a job.
            job = lib.reserve(1, 1)
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM press_articles').fetchone()[0], old)
        self.assertIsNotNone(job)
        self.service.create_save('WithoutPress'); self.service.initialize_save('WithoutPress')
        conn = store.open_readonly('WithoutPress')
        try:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        finally:
            conn.close()

    def test_article_saved_once_read_and_archive_roundtrip(self):
        lib = self.ready()
        world = self.world()
        transport = Mock(side_effect=simulated_transport)
        job = lib.reserve(1, 1)
        self.assertTrue(lib.generate(job, KEY, transport=transport))
        self.assertEqual(lib.overview()['unread'], 1)
        article = lib.read(1, 1)
        self.assertEqual(lib.overview()['unread'], 0)
        self.assertIsNone(lib.reserve(1, 1))
        self.assertEqual(lib.read(1, 1), article)
        self.assertEqual(transport.call_count, 4)  # Four selected stories, no assembly call.
        self.assertEqual(lib.overview()['weeks'][0]['sections_done'], 4)
        self.assertEqual(self.world(), world)
        package = Path(self.tmp.name) / 'press.fsarchive'
        store.export_package('Press', package)
        store.import_package(package, 'Imported')
        self.assertEqual(Library(store.database_path('Imported')).read(1, 1), article)
        self.assertNotIn(KEY, lib.path.read_bytes().decode('latin1'))

    def test_retry_reuses_completed_section_and_keeps_raw_returns(self):
        lib = self.ready(); job = lib.reserve(1, 1)
        calls = []
        def interrupted(request, key):
            calls.append(request)
            if len(calls) == 2:
                raise RuntimeError(KEY)
            return simulated_transport(request, key)
        self.assertFalse(lib.generate(job, KEY, transport=interrupted, grouped=True))
        self.assertEqual(lib.overview()['weeks'][0]['sections_done'], 1)
        with lib.connection() as conn:
            first = dict(conn.execute("SELECT * FROM press_article_sections WHERE status='ready'").fetchone())
        retry = lib.reserve(1, 1, retry=True)
        transport = Mock(side_effect=simulated_transport)
        with patch('football_simulator.press.library.generation_plan', side_effect=AssertionError('must reuse frozen plan')):
            self.assertTrue(lib.generate(retry, KEY, transport=transport))
        transport.assert_called_once()
        with lib.connection() as conn:
            kept = dict(conn.execute("SELECT * FROM press_article_sections WHERE owner=? AND competition_id=?", (first['owner'], first['competition_id'])).fetchone())
            row = conn.execute("SELECT metadata_json FROM press_articles").fetchone()
        self.assertEqual(first, kept)
        self.assertEqual(json.loads(row[0])['api_requests_this_attempt'], 1)
        self.assertNotIn(KEY, lib.path.read_bytes().decode('latin1'))

    def test_duplicate_and_late_completion_cannot_write_another_save(self):
        lib = self.ready(); job = lib.reserve(1, 1)
        with self.assertRaises(PressError): lib.reserve(1, 1)
        self.service.create_save('Other'); self.service.initialize_save('Other')
        other = activate('Other')
        self.assertTrue(lib.generate(job, KEY, transport=simulated_transport))
        self.assertEqual(other.overview()['weeks'], [])
        self.assertEqual(lib.overview()['unread'], 1)

    def test_restart_recovers_returned_section_without_calling_provider_again(self):
        lib = self.ready(); job = lib.reserve(1, 1)
        normalized = []
        def stop_after_return(raw, packet, plan, unit):
            normalized.append(unit['competition_id'])
            if len(normalized) == 2:
                # Simulate exit after the paid raw return commits, before the
                # normalized section/whole article can be committed.
                raise SystemExit('controlled interruption')
            return prepare_story_article(raw, packet, plan, unit)
        transport = Mock(side_effect=simulated_transport)
        with patch('football_simulator.press.library.prepare_story_article', side_effect=stop_after_return):
            with self.assertRaises(SystemExit):
                lib.generate(job, KEY, transport=transport, grouped=True)
        self.assertEqual(transport.call_count, 2)
        self.advance()  # A world write must retain the committed partial report.
        world_after_advance = self.world()
        reopened = Library(lib.path)
        reopened.recover()
        self.assertEqual(reopened.overview()['weeks'][-1]['status'], 'failed')
        forbidden = Mock(side_effect=AssertionError('no new request is needed'))
        self.assertFalse(reopened.generate(job, KEY, transport=forbidden))
        forbidden.assert_not_called()  # The expired owner cannot dispatch.
        retry = reopened.reserve(1, 1, retry=True)
        self.assertTrue(reopened.generate(retry, KEY, transport=forbidden))
        forbidden.assert_not_called()
        self.assertEqual(self.world(), world_after_advance)
        with reopened.connection() as conn:
            metadata = json.loads(conn.execute('SELECT metadata_json FROM press_articles').fetchone()[0])
        self.assertEqual(metadata['api_requests_this_attempt'], 0)
        self.assertEqual(reopened.read(1, 1)['report_id'], job['packet']['meta']['report_id'])

    def test_failure_only_retries_explicitly_and_recovers_interrupted_job(self):
        lib = self.ready(); job = lib.reserve(1, 1)
        bad = Mock(side_effect=RuntimeError(KEY))
        self.assertFalse(lib.generate(job, KEY, transport=bad))
        self.assertEqual(lib.overview()['weeks'][0]['status'], 'failed')
        with self.assertRaises(PressError): lib.reserve(1, 1)
        retry = lib.reserve(1, 1, retry=True)
        lib.recover([retry['token']])
        self.assertEqual(lib.overview()['weeks'][0]['status'], 'generating')
        lib.recover()
        self.assertEqual(lib.overview()['weeks'][0]['status'], 'failed')
        retry = lib.reserve(1, 1, retry=True)
        self.assertTrue(lib.generate(retry, KEY, transport=simulated_transport))
        self.assertNotIn(KEY, lib.path.read_bytes().decode('latin1'))
        self.assertEqual(bad.call_count, 1)

    def test_old_failed_per_competition_job_resumes_original_contract(self):
        from football_simulator.press.storytelling import section_preview
        from football_simulator.press.provider import build_request
        lib = self.ready(); job = lib.reserve(1, 1)
        old = {'strategy': 'per-competition/1', 'sections': [
            {'competition_id': cid, 'request': build_request(section_preview(job['packet'], job['sources'], cid)['request']),
             'prompt_versions': ['weekly-section-draft-6']} for cid in ('premier', 'second')]}
        with lib.connection(write=True) as conn:
            conn.execute('UPDATE press_article_attempts SET request_json=?,request_sha=? WHERE token=?',
                         (repository.canonical(old), repository.digest(old), job['token']))
        calls = []
        def interrupted(request, key):
            calls.append(request)
            if len(calls) == 2: raise RuntimeError('controlled')
            return simulated_transport(request, key)
        self.assertFalse(lib.generate(job, KEY, transport=interrupted))
        retry = lib.reserve(1, 1, retry=True)
        transport = Mock(side_effect=simulated_transport)
        with patch('football_simulator.press.library.generation_plan', side_effect=AssertionError('legacy must not replan')):
            self.assertTrue(lib.generate(retry, KEY, transport=transport))
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[0], old['sections'][1]['request'])
        with lib.connection() as conn:
            self.assertEqual(json.loads(conn.execute('SELECT metadata_json FROM press_articles').fetchone()[0])['strategy'], 'per-competition/1')

    def test_complete_but_malformed_paid_return_is_retained_without_automatic_retry(self):
        lib = self.ready(); job = lib.reserve(1, 1)
        def malformed(request, key):
            result = simulated_transport(request, key)
            result['choices'][0]['message']['content'] = '{"title": "unfinished"'
            return result
        transport = Mock(side_effect=malformed)
        self.assertFalse(lib.generate(job, KEY, transport=transport))
        transport.assert_called_once()
        with lib.connection() as conn:
            row = conn.execute('SELECT status,provider_return_json,provider_return_sha FROM press_article_sections').fetchone()
            self.assertEqual(row['status'], 'failed')
            receipt = json.loads(row['provider_return_json'])
            self.assertEqual(repository.digest(receipt), row['provider_return_sha'])
            self.assertEqual(receipt['choices'][0]['message']['content'], '{"title": "unfinished"')
        self.assertNotIn(KEY, lib.path.read_bytes().decode('latin1'))

    def test_frozen_whole_format_return_recovers_from_receipt_without_new_api_call(self):
        from football_simulator.press import provider
        from football_simulator.press.planning import generation_plan
        lib = self.ready(); job = lib.reserve(1,1)
        # A saved whole-issue job predates the per-story default. Freeze that
        # mode explicitly; switching the new default must not split its retry.
        whole = generation_plan(job['packet'],job['sources'],storywise=False)
        with lib.connection(write=True) as conn:
            with patch('football_simulator.press.library.generation_plan',return_value=whole):
                plan = lib._frozen_plan(conn,job)
            unit = plan['sections'][0]
            request = unit['request']
            response = simulated_transport(request,KEY)
            article = {'title':'原始标题','deck':'原始导语','body':'## 原始小标题\n\n原始正文不改动。'}
            response['choices'][0]['message']['content'] = json.dumps(article,ensure_ascii=False)[:-1] + ',"}'
            conn.execute('''INSERT INTO press_article_sections(owner,report_id,competition_id,packet_sha,request_json,request_sha,status,provider_return_json,provider_return_sha)
                VALUES(?,?,?,?,?,?, 'failed',?,?)''', (job['token'],job['packet']['meta']['report_id'],unit['competition_id'],repository.digest(job['packet']),
                repository.canonical(request),repository.digest(request),repository.canonical(response),repository.digest(response)))
            conn.execute("UPDATE press_articles SET status='failed' WHERE owner=?",(job['token'],))
            conn.execute("UPDATE press_article_attempts SET status='failed' WHERE token=?",(job['token'],))
        retry = lib.reserve(1,1,retry=True); forbidden=Mock(side_effect=AssertionError('must not call API'))
        self.assertTrue(lib.generate(retry,KEY,transport=forbidden)); forbidden.assert_not_called()
        self.assertEqual(lib.read(1,1)['title'],article['title'])
        with lib.connection() as conn:
            metadata=json.loads(conn.execute('SELECT metadata_json FROM press_articles').fetchone()[0])
            self.assertEqual(metadata['api_requests_this_attempt'],0)
            self.assertIn('article_format_recovery',metadata['sections'][0]['provider'])
