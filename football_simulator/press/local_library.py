"""Local jobs reuse the save library, ownership checks and article contract.

Legacy provider classes remain available for reading/diagnosing V0.2.0. The
V0.3.0 controller uses only these explicit local entry points.
"""
import json
import sqlite3

from football_simulator.press import PressError, repository
from football_simulator.press.library import Library, now
from football_simulator.press.feature_library import FeatureLibrary
from football_simulator.press.local_templates import ENGINE_ID, TEMPLATE_VERSION, install_columns, engine_of
from football_simulator.press.validation import validate_article_basics
from football_simulator.persistence_v2 import store


def enqueue_week(conn, packet, packet_sha, source_sha):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(press_activation)')}
    if 'engine_id' not in columns:
        return
    active = conn.execute('SELECT engine_id,template_version FROM press_activation WHERE singleton=1').fetchone()
    if active['engine_id'] != ENGINE_ID or packet['meta']['input_validity'] != 'ready':
        return
    meta = packet['meta']
    conn.execute('''INSERT OR IGNORE INTO press_articles
        (season,week,world_id,report_id,packet_sha,source_sha,status,created_at,engine_id,template_version)
        VALUES(?,?,?,?,?,?,'queued',?,?,?)''',
        (meta['season'],meta['completed_week'],meta['world_id'],meta['report_id'],packet_sha,source_sha,
         now(),ENGINE_ID,active['template_version']))


class LocalMixin:
    def _owns(self,conn,job):
        super()._owns(conn,job)
        row=conn.execute(f'SELECT engine_id,template_version FROM {self.record_table} WHERE owner=?',(job['token'],)).fetchone()
        if not row or engine_of(row)!=ENGINE_ID or row['template_version']!=job.get('template_version'):
            raise PressError('本地任务的引擎或模板身份已改变。')

    def generate(self,*args,**kwargs):
        raise PressError('本地模板请使用 generate_local，不接受云请求或模拟 API 返回。')

    def overview(self):
        data=super().overview()
        if not data['enabled']:return data
        with self.connection() as conn:
            saved={(r['season'],r['week']):r for r in conn.execute('SELECT * FROM press_articles')}
            for item in data['weeks']:
                row=saved.get((item['season'],item['week']))
                item['engine_id']=engine_of(row) if row else ENGINE_ID
                if row and row['status']=='ready' and item['engine_id']==ENGINE_ID:
                    item['sections_done']=item['section_total']
        return data

    def install(self):
        super().install()
        with self.connection(write=True) as conn:
            install_columns(conn, 'press_activation')
            # Select the engine for FUTURE boundaries. Existing jobs keep their
            # nullable legacy markers, frozen plans, status and original text.
            conn.execute('UPDATE press_activation SET engine_id=?,template_version=? WHERE singleton=1',
                         (ENGINE_ID,TEMPLATE_VERSION))
            from football_simulator.press.expectations import capture_next
            capture_next(conn,json.loads(conn.execute('SELECT data_json FROM world').fetchone()[0]))

    def reserve(self, season, week, *, retry=False):
        return super().reserve(season,week,retry=retry,engine_id=ENGINE_ID,template_version=TEMPLATE_VERSION)

    def recover(self, live_tokens=()):
        # Local interrupted jobs are safe to resume. Legacy incomplete jobs
        # keep their exact state: the offline edition must never dispatch them.
        with self.connection(write=True) as conn:
            for table, attempts in [('press_articles','press_article_attempts'),('press_features','press_feature_attempts')]:
                if not conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
                    continue
                for row in conn.execute(f"SELECT owner FROM {table} WHERE status='generating' AND engine_id=?",(ENGINE_ID,)).fetchall():
                    if row['owner'] not in live_tokens:
                        conn.execute(f"UPDATE {table} SET status='paused',error=NULL WHERE owner=?",(row['owner'],))
                        conn.execute(f"UPDATE {attempts} SET status='paused',finished_at=? WHERE token=?",(now(),row['owner']))

    def source(self, *, season=None, week=None, report_id=None):
        with self.connection() as conn:
            row = (conn.execute('SELECT * FROM press_features WHERE report_id=?',(report_id,)).fetchone()
                   if report_id else conn.execute('SELECT * FROM press_articles WHERE season=? AND week=?',(season,week)).fetchone())
            return {'engine_id':engine_of(row),'template_version':row['template_version']} if row else None

    def generate_local(self, job, *, progress=None, should_stop=None):
        from football_simulator.press.local_templates.engine import freeze_plan, render
        try:
            if job.get('engine_id') != ENGINE_ID:
                raise PressError('拒绝转换原引擎任务。')
            # Planning/rendering own no database transaction. A normal close
            # during a short local render commits the complete result atomically.
            with self.connection() as conn:
                self._owns(conn,job)
                saved = conn.execute('SELECT * FROM press_article_plans WHERE report_id=?',
                                     (job['packet']['meta']['report_id'],)).fetchone()
            packet_sha=repository.digest(job['packet'])
            source_sha=job.get('source_sha') or repository.digest(job['sources'])
            if saved:
                plan=json.loads(saved['plan_json'])
                if (repository.digest(plan)!=saved['plan_sha'] or saved['packet_sha']!=packet_sha
                        or saved['source_sha']!=source_sha):
                    raise PressError('本地冻结计划校验失败。')
            else:
                plan=freeze_plan(job['packet'],job.get('sources'),job['template_version'])
                with self.connection(write=True) as conn:
                    self._owns(conn,job)
                    conn.execute('INSERT INTO press_article_plans VALUES(?,?,?,?,?)',
                                 (job['packet']['meta']['report_id'],packet_sha,source_sha,
                                  repository.canonical(plan),repository.digest(plan)))
            if plan['engine_id']!=ENGINE_ID or plan['template_version']!=job['template_version']:
                raise PressError('引擎或模板版本不符。')
            with self.connection(write=True) as conn:
                self._owns(conn,job)
                conn.execute(f'UPDATE {self.attempt_table} SET request_json=?,request_sha=? WHERE token=?',
                             (repository.canonical(plan),repository.digest(plan),job['token']))
                if should_stop and should_stop():
                    conn.execute(f"UPDATE {self.record_table} SET status='paused' WHERE owner=?",(job['token'],))
                    conn.execute(f"UPDATE {self.attempt_table} SET status='paused',finished_at=? WHERE token=?",(now(),job['token']))
                    return False
            article,mapping=render(job['packet'],plan)
            validate_article_basics(article,job['packet'])
            metadata={'engine_id':ENGINE_ID,'template_version':plan['template_version'],
                      'packet_sha256':packet_sha,'source_sha256':source_sha,'plan_sha256':repository.digest(plan),
                      'story_templates':mapping}
            with self.connection(write=True) as conn:
                self._owns(conn,job)
                conn.execute(f"UPDATE {self.record_table} SET status='ready',article_json=?,article_sha=?,metadata_json=?,error=NULL WHERE owner=?",
                             (repository.canonical(article),repository.digest(article),repository.canonical(metadata),job['token']))
                conn.execute(f"UPDATE {self.attempt_table} SET status='ready',finished_at=? WHERE token=?",(now(),job['token']))
                self._published(conn,job,article)
            if progress:progress()
            return True
        except Exception:
            # No provider payloads, arbitrary exceptions or keys enter a save.
            try:
                with self.connection(write=True) as conn:
                    self._owns(conn,job)
                    error='本地模板未完成，请检查冻结材料或模板版本后重试。'
                    conn.execute(f"UPDATE {self.record_table} SET status='failed',error=? WHERE owner=?",(error,job['token']))
                    conn.execute(f"UPDATE {self.attempt_table} SET status='failed',error=?,finished_at=? WHERE token=?",(error,now(),job['token']))
            except (PressError,OSError,sqlite3.Error):pass
            return False


class LocalLibrary(LocalMixin,Library):
    pass


class LocalFeatureLibrary(LocalMixin,FeatureLibrary):
    def feature_rows(self):
        rows=super().feature_rows()
        if not rows:return rows
        with self.connection() as conn:
            saved={r['report_id']:r for r in conn.execute('SELECT * FROM press_features')}
            for item in rows:
                item['engine_id']=engine_of(saved[item['report_id']])
                if item['status']=='ready' and item['engine_id']==ENGINE_ID:
                    item['sections_done']=item['section_total']
        return rows

    def reserve_feature(self, report_id, *, retry=False):
        return super().reserve_feature(report_id,retry=retry,engine_id=ENGINE_ID,template_version=TEMPLATE_VERSION)

    def next_local(self):
        """One scheduler for weekly and feature jobs; no legacy cloud dispatch."""
        with self.connection() as conn:
            rows=conn.execute('''SELECT 'weekly' kind,season,week,report_id,created_at FROM press_articles
                WHERE engine_id=? AND status IN ('queued','paused')
                UNION ALL SELECT kind,season,week,report_id,created_at FROM press_features
                WHERE engine_id=? AND status IN ('queued','paused')
                ORDER BY season,week,created_at,report_id LIMIT 1''',(ENGINE_ID,ENGINE_ID)).fetchone()
            return dict(rows) if rows else None


def activate(save_name):
    repository.enable(save_name)
    library=LocalFeatureLibrary(store.database_path(save_name))
    library.install()
    return library
