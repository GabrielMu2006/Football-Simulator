"""Shared save storage and retained V0.2.0 DeepSeek generation.

V0.3.0's UI uses local_library's explicit local entry points. This provider
path remains for legacy compatibility tests and is never the offline fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
import uuid

from football_simulator.persistence_v2 import store
from football_simulator.press import PressError, repository, provider
from football_simulator.press.storytelling import compose_sections, prepare_requested_fragment, section_preview
from football_simulator.press.planning import generation_plan, prepare_story_article, assemble_story_articles, VERSION
from football_simulator.press.validation import validate_article_basics


def now():
    return datetime.now(timezone.utc).isoformat()


class Library:
    record_table = 'press_articles'
    attempt_table = 'press_article_attempts'
    def __init__(self, path):
        # Captured before dispatch: a later save switch cannot redirect a worker.
        self.path = Path(path).absolute()

    @contextmanager
    def connection(self, *, write=False):
        if not self.path.is_file():
            raise PressError("原存档已移走，未写入其他存档。")
        conn = store._connect(self.path, readonly=not write)
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            if write:
                conn.commit()
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def initialized(self, conn):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name='press_articles'").fetchone() is not None

    def install(self):
        # Only opt-in newspaper saves use WAL. The UI keeps reading while a
        # background writer commits sections; rollback journals can fail during
        # that overlap on macOS. No connection/transaction spans an API call.
        if not self.path.is_file():
            raise PressError("原存档已移走，未写入其他存档。")
        conn = store._connect(self.path)
        try:
            if not repository.enabled(conn):
                raise PressError("请先启用报刊。")
            if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != 'wal':
                raise PressError("无法启用周报后台保存，请关闭该开发存档的其他实例后重试。")
        finally:
            conn.close()
        with self.connection(write=True) as conn:
            if not repository.enabled(conn):
                raise PressError("请先启用报刊。")
            conn.execute('''CREATE TABLE IF NOT EXISTS press_articles (
                season INTEGER NOT NULL, week INTEGER NOT NULL, world_id TEXT NOT NULL,
                report_id TEXT NOT NULL UNIQUE, packet_sha TEXT NOT NULL, source_sha TEXT NOT NULL,
                status TEXT NOT NULL, owner TEXT, article_json TEXT, article_sha TEXT,
                metadata_json TEXT, created_at TEXT NOT NULL, read_at TEXT, error TEXT,
                PRIMARY KEY(season,week))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS press_article_attempts (
                token TEXT PRIMARY KEY, season INTEGER NOT NULL, week INTEGER NOT NULL,
                status TEXT NOT NULL, request_json TEXT, request_sha TEXT,
                started_at TEXT NOT NULL, finished_at TEXT, error TEXT)''')
            self._install_sections(conn)
            from football_simulator.press.local_templates import install_columns
            install_columns(conn, 'press_articles')

    @staticmethod
    def _install_sections(conn):
        # Per-attempt raw returns remain available, including failed attempts.
        conn.execute('''CREATE TABLE IF NOT EXISTS press_article_sections (
            owner TEXT NOT NULL, report_id TEXT NOT NULL, competition_id TEXT NOT NULL,
            packet_sha TEXT NOT NULL, request_json TEXT NOT NULL, request_sha TEXT NOT NULL,
            status TEXT NOT NULL, raw_json TEXT, raw_sha TEXT, fragment_json TEXT,
            fragment_sha TEXT, metadata_json TEXT, PRIMARY KEY(owner,competition_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS press_article_plans (
            report_id TEXT PRIMARY KEY, packet_sha TEXT NOT NULL, source_sha TEXT NOT NULL,
            plan_json TEXT NOT NULL, plan_sha TEXT NOT NULL)''')
        columns = {r[1] for r in conn.execute('PRAGMA table_info(press_article_sections)')}
        for name in ('provider_return_json', 'provider_return_sha'):
            if name not in columns:
                conn.execute(f'ALTER TABLE press_article_sections ADD COLUMN {name} TEXT')

    def overview(self):
        with self.connection() as conn:
            active = repository.enabled(conn)
            if not active:
                return {"enabled": False, "weeks": [], "unread": 0}
            articles = {}
            if self.initialized(conn):
                articles = {(r['season'], r['week']): dict(r) for r in conn.execute("SELECT * FROM press_articles")}
            rows = []
            completed = {}
            totals = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='press_article_sections'").fetchone():
                completed = {r['report_id']: r['n'] for r in conn.execute("SELECT report_id,COUNT(DISTINCT competition_id) AS n FROM press_article_sections WHERE status='ready' GROUP BY report_id")}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='press_article_plans'").fetchone():
                totals = {r['report_id']: len(json.loads(r['plan_json'])['sections']) for r in conn.execute('SELECT report_id,plan_json FROM press_article_plans')}
            for row in conn.execute("SELECT season,week,json_extract(packet_json,'$.meta.input_validity') AS validity,json_extract(packet_json,'$.meta.report_id') AS report_id,json_array_length(json_extract(packet_json,'$.week_summary.competitions')) AS section_total FROM press_weeks ORDER BY season DESC,week DESC"):
                saved = articles.get((row['season'], row['week']))
                item = {"season": row['season'], "week": row['week'],
                        "status": saved['status'] if saved else "available",
                        "ready": row['validity'] == 'ready',
                        "unread": bool(saved and saved['status'] == 'ready' and not saved['read_at']),
                        "title": "", "summary": "", "error": saved['error'] if saved else None,
                        "section_total": totals.get(row['report_id'], row['section_total']), "sections_done": completed.get(row['report_id'], 0)}
                if saved and saved['article_json']:
                    article = self._article(saved)
                    item.update(title=article['title'], summary=article['summary'])
                rows.append(item)
            return {"enabled": True, "weeks": rows, "unread": sum(r['unread'] for r in rows)}

    def unread_count(self):
        with self.connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM press_articles WHERE status='ready' AND read_at IS NULL").fetchone()[0] if self.initialized(conn) else 0

    def latest(self):
        with self.connection() as conn:
            if not self.initialized(conn):
                return None
            row = conn.execute("SELECT * FROM press_articles WHERE status='ready' ORDER BY season DESC,week DESC LIMIT 1").fetchone()
            return {"season": row['season'], "week": row['week'], **self._article(row)} if row else None

    @staticmethod
    def _article(row):
        value = json.loads(row['article_json'])
        if repository.digest(value) != row['article_sha']:
            raise PressError("文章文件损坏，请从备份恢复。")
        return value

    def read(self, season, week, *, mark_read=True):
        with self.connection(write=mark_read) as conn:
            if not self.initialized(conn):
                raise PressError("这期周报尚未生成。")
            row = conn.execute("SELECT * FROM press_articles WHERE season=? AND week=?", (season, week)).fetchone()
            if not row or row['status'] != 'ready':
                raise PressError("这期周报尚未生成。")
            article = self._article(row)
            if mark_read:
                conn.execute("UPDATE press_articles SET read_at=COALESCE(read_at,?) WHERE season=? AND week=?", (now(), season, week))
            return article

    def recover(self, live_tokens=()):
        """No silent resubmission after a process exit; outcome may have been billed."""
        with self.connection(write=True) as conn:
            if not self.initialized(conn):
                return
            for row in conn.execute("SELECT owner FROM press_articles WHERE status='generating'").fetchall():
                if row['owner'] not in live_tokens:
                    error = "上次生成已中断，未自动重试；服务端可能已计费。"
                    conn.execute("UPDATE press_articles SET status='failed',error=? WHERE owner=?", (error, row['owner']))
                    conn.execute("UPDATE press_article_attempts SET status='failed',error=?,finished_at=? WHERE token=?", (error, now(), row['owner']))

    def reserve(self, season, week, *, retry=False, engine_id='deepseek', template_version=None):
        with self.connection(write=True) as conn:
            world_id = conn.execute("SELECT world_id FROM world").fetchone()[0]
            material = conn.execute("SELECT * FROM press_weeks WHERE world_id=? AND season=? AND week=?", (world_id, season, week)).fetchone()
            if not material:
                raise PressError("该周没有启用后冻结的材料，不补生成旧周报。")
            packet, sources = json.loads(material['packet_json']), json.loads(material['source_json'])
            if repository.digest(packet) != material['packet_sha256'] or repository.digest(sources) != material['source_sha256']:
                raise PressError("周报材料校验和不符。")
            if packet['meta']['input_validity'] != 'ready':
                raise PressError("本周没有可报道的比赛。")
            previous = conn.execute("SELECT * FROM press_articles WHERE season=? AND week=?", (season, week)).fetchone()
            if previous and (previous['world_id'] != world_id or previous['report_id'] != packet['meta']['report_id']
                             or previous['packet_sha'] != material['packet_sha256'] or previous['source_sha'] != material['source_sha256']):
                raise PressError("该周已有不同来源的报道，未覆盖或重新请求。")
            if previous and previous['status'] == 'ready':
                return None
            from football_simulator.press.local_templates import engine_of
            if previous and engine_of(previous) != engine_id:
                raise PressError('原 DeepSeek 任务未完成，V0.3.0 保留原状态，不自动转换或调用 API。')
            if previous and (previous['status'] == 'generating' or (previous['status'] not in ('paused', 'queued') and not retry)):
                raise PressError("正在生成，或需要手动确认失败重试。")
            token = str(uuid.uuid4())
            if previous:
                conn.execute("UPDATE press_articles SET status='generating',owner=?,error=NULL WHERE season=? AND week=?", (token, season, week))
            else:
                conn.execute("INSERT INTO press_articles(season,week,world_id,report_id,packet_sha,source_sha,status,owner,created_at,engine_id,template_version) VALUES(?,?,?,?,?,?,'generating',?,?,?,?)", (season, week, world_id, packet['meta']['report_id'], material['packet_sha256'], material['source_sha256'], token, now(), engine_id, template_version))
            conn.execute("INSERT INTO press_article_attempts(token,season,week,status,started_at) VALUES(?,?,?,'generating',?)", (token, season, week, now()))
            instance = conn.execute('SELECT instance_id FROM press_activation WHERE singleton=1').fetchone()[0]
            return {"token": token, "world_id": world_id, "season": season, "week": week, "packet": packet, "sources": sources,
                    'instance_id': instance, 'engine_id': engine_id,
                    'template_version': previous['template_version'] if previous else template_version}

    def _frozen_plan(self, conn, job, *, grouped=False):
        """Freeze before dispatch. A failed job resumes its original requests."""
        self._owns(conn, job)
        self._install_sections(conn)
        rid = job['packet']['meta']['report_id']
        packet_sha, source_sha = repository.digest(job['packet']), repository.digest(job['sources'])
        saved = conn.execute('SELECT * FROM press_article_plans WHERE report_id=?', (rid,)).fetchone()
        if saved:
            plan = json.loads(saved['plan_json'])
            if (repository.digest(plan) != saved['plan_sha'] or saved['packet_sha'] != packet_sha or saved['source_sha'] != source_sha):
                raise PressError('冻结的编辑计划身份或内容不符。')
        else:
            old = conn.execute('''SELECT request_json,request_sha FROM press_article_attempts
                WHERE season=? AND week=? AND request_json IS NOT NULL ORDER BY started_at LIMIT 1''', (job['season'], job['week'])).fetchone()
            if old:
                plan = json.loads(old['request_json'])
                if repository.digest(plan) != old['request_sha'] or plan.get('strategy') != 'per-competition/1':
                    raise PressError('旧任务的冻结请求不符。')
                # Historical retries may already contain a usable paid section
                # from an earlier prompt. Keep that section's original request.
                for unit in plan['sections']:
                    prior = conn.execute("SELECT request_json,request_sha FROM press_article_sections WHERE report_id=? AND competition_id=? AND packet_sha=? AND status IN ('ready','returned') ORDER BY rowid LIMIT 1", (rid, unit['competition_id'], packet_sha)).fetchone()
                    if prior:
                        request = json.loads(prior['request_json'])
                        if repository.digest(request) != prior['request_sha']: raise PressError('旧分节请求损坏。')
                        unit['request'] = request
            else:
                plan = generation_plan(job['packet'], job['sources'], grouped=grouped)
            for unit in plan['sections']:
                unit['request'] = provider.build_request(unit['request'])
            conn.execute('INSERT INTO press_article_plans VALUES(?,?,?,?,?)', (rid, packet_sha, source_sha, repository.canonical(plan), repository.digest(plan)))
        if plan.get('strategy') not in ('per-competition/1', VERSION):
            raise PressError('不支持该冻结任务的生成方式。')
        conn.execute('UPDATE press_article_attempts SET request_json=?,request_sha=? WHERE token=?', (repository.canonical(plan), repository.digest(plan), job['token']))
        return plan

    @staticmethod
    def _prepare(raw, job, plan, unit):
        if plan['strategy'] == 'per-competition/1':
            return prepare_requested_fragment(raw, job['packet'], unit['competition_id'], unit['request'])
        return prepare_story_article(raw, job['packet'], plan, unit)

    def _cached_fragment(self, conn, job, competition, plan, unit):
        rows = conn.execute("SELECT * FROM press_article_sections WHERE report_id=? AND competition_id=? AND status IN ('ready','returned','failed','sending') ORDER BY rowid DESC",
                            (job['packet']['meta']['report_id'], competition)).fetchall()
        for row in rows:
            if row['packet_sha'] != repository.digest(job['packet']):
                raise PressError('分段材料身份不符。')
            if row['request_sha'] != repository.digest(unit['request']):
                continue
            if repository.digest(json.loads(row['request_json'])) != row['request_sha']:
                raise PressError('分段请求损坏。')
            metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}
            if row['raw_json']:
                raw = json.loads(row['raw_json'])
                if repository.digest(raw) != row['raw_sha']:
                    raise PressError('分段返回内容损坏。')
            elif row['provider_return_json']:
                response = json.loads(row['provider_return_json'])
                if repository.digest(response) != row['provider_return_sha']:
                    raise PressError('原始调用回执损坏。')
                try:
                    metadata = provider.response_metadata(response)
                    raw = provider.parse_article(response, format_receipt=metadata)
                except provider.ProviderError:
                    continue
                conn.execute("UPDATE press_article_sections SET status='returned',raw_json=?,raw_sha=?,metadata_json=? WHERE owner=? AND competition_id=?",
                             (repository.canonical(raw), repository.digest(raw), repository.canonical(metadata), row['owner'], competition))
            else:
                continue
            if row['status'] == 'ready':
                fragment = json.loads(row['fragment_json'])
                if repository.digest(fragment) != row['fragment_sha']:
                    raise PressError('已完成分段损坏。')
            else:
                try:
                    fragment = self._prepare(raw, job, plan, unit)
                except (PressError, KeyError, TypeError, ValueError):
                    # The old return stays intact; an explicit failed-job retry
                    # can request just this unusable section again.
                    continue
                conn.execute("UPDATE press_article_sections SET status='ready',fragment_json=?,fragment_sha=? WHERE owner=? AND competition_id=?",
                             (repository.canonical(fragment), repository.digest(fragment), row['owner'], competition))
            return fragment, {'competition_id': competition, 'cached': True, 'source_owner': row['owner'],
                              'request_sha256': row['request_sha'], 'provider': metadata}
        return None

    def _assemble(self,job,plan,fragments):
        return compose_sections(job['packet'],fragments) if plan['strategy']=='per-competition/1' else assemble_story_articles(job['packet'],fragments,plan=plan)

    def _published(self,conn,job,article):
        pass

    def generate(self, job, key, *, transport=provider.send_once, progress=None, grouped=False, should_stop=None):
        try:
            with self.connection(write=True) as conn:
                plan = self._frozen_plan(conn, job, grouped=grouped)
            fragments, receipts = [], []
            for unit in plan['sections']:
                competition = unit['competition_id']
                with self.connection(write=True) as conn:
                    self._owns(conn, job)
                    cached = self._cached_fragment(conn, job, competition, plan, unit)
                if cached:
                    fragment, receipt = cached
                else:
                    # A normal close stops between requests. Paid returns and
                    # complete sections are already durable before this point.
                    if should_stop and should_stop():
                        with self.connection(write=True) as conn:
                            self._owns(conn, job)
                            conn.execute(f"UPDATE {self.record_table} SET status='paused',error=NULL WHERE owner=?", (job['token'],))
                            conn.execute(f"UPDATE {self.attempt_table} SET status='paused',finished_at=? WHERE token=?", (now(),job['token']))
                        if progress:progress()
                        return False
                    request = unit['request']
                    with self.connection(write=True) as conn:
                        self._owns(conn, job)
                        conn.execute("INSERT INTO press_article_sections(owner,report_id,competition_id,packet_sha,request_json,request_sha,status) VALUES(?,?,?,?,?,?,'sending')",
                                     (job['token'], job['packet']['meta']['report_id'], competition, repository.digest(job['packet']), repository.canonical(request), repository.digest(request)))
                    def audited_transport(payload, secret):
                        response = transport(payload, secret)
                        encoded = repository.canonical(response)
                        if secret in encoded:
                            raise provider.ProviderError('credential_in_response')
                        # Keep even billed malformed article JSON for diagnosis;
                        # never silently regenerate or discard its receipt.
                        with self.connection(write=True) as conn:
                            self._owns(conn, job)
                            conn.execute('UPDATE press_article_sections SET provider_return_json=?,provider_return_sha=? WHERE owner=? AND competition_id=?',
                                         (encoded, repository.digest(response), job['token'], competition))
                        return response
                    raw, metadata = provider.complete(request, key, transport=audited_transport)
                    # Persist a billed, complete return before normalization.
                    with self.connection(write=True) as conn:
                        self._owns(conn, job)
                        conn.execute("UPDATE press_article_sections SET status='returned',raw_json=?,raw_sha=?,metadata_json=? WHERE owner=? AND competition_id=?",
                                     (repository.canonical(raw), repository.digest(raw), repository.canonical(metadata), job['token'], competition))
                    fragment = self._prepare(raw, job, plan, unit)
                    with self.connection(write=True) as conn:
                        self._owns(conn, job)
                        conn.execute("UPDATE press_article_sections SET status='ready',fragment_json=?,fragment_sha=? WHERE owner=? AND competition_id=?",
                                     (repository.canonical(fragment), repository.digest(fragment), job['token'], competition))
                    receipt = {'competition_id': competition, 'cached': False, 'source_owner': job['token'],
                               'request_sha256': repository.digest(request), 'provider': metadata}
                fragments.append((competition, fragment)); receipts.append(receipt)
                if progress:
                    progress()
            article = self._assemble(job,plan,fragments)
            validate_article_basics(article, job['packet'])
            metadata = {'strategy': plan['strategy'], 'plan_sha256': repository.digest(plan), 'sections': receipts,
                        'api_requests_this_attempt': sum(not r['cached'] for r in receipts),
                        'assembly_api_requests': 0}
            with self.connection(write=True) as conn:
                self._owns(conn, job)
                conn.execute(f"UPDATE {self.record_table} SET status='ready',article_json=?,article_sha=?,metadata_json=?,error=NULL WHERE owner=?", (repository.canonical(article), repository.digest(article), repository.canonical(metadata), job['token']))
                conn.execute(f"UPDATE {self.attempt_table} SET status='ready',finished_at=? WHERE token=?", (now(), job['token']))
                self._published(conn,job,article)
            return True
        except Exception as exc:
            # No arbitrary exceptions/provider bodies (which could include credentials) in UI/DB.
            error = "文章未完整生成，请稍后手动重试。"
            if isinstance(exc, provider.ProviderError):
                error = {"authentication": "密钥未通过认证，请重新设置。", "insufficient_balance": "API 余额不足。",
                         "rate_limited": "请求过于频繁，请稍后重试。"}.get(exc.code, error)
                if 'outcome_unknown' in exc.code:
                    error = "连接中断，服务端可能已计费；未自动重试。"
            try:
                with self.connection(write=True) as conn:
                    self._owns(conn, job)
                    conn.execute(f"UPDATE {self.record_table} SET status='failed',error=? WHERE owner=?", (error, job['token']))
                    conn.execute(f"UPDATE {self.attempt_table} SET status='failed',error=?,finished_at=? WHERE token=?", (error, now(), job['token']))
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='press_article_sections'").fetchone():
                        conn.execute("UPDATE press_article_sections SET status='failed' WHERE owner=? AND status='sending'", (job['token'],))
            except (PressError, OSError, sqlite3.Error):
                pass
            return False

    def _owns(self, conn, job):
        world_id = conn.execute("SELECT world_id FROM world").fetchone()[0]
        row = conn.execute("SELECT world_id,status FROM press_articles WHERE owner=?", (job['token'],)).fetchone()
        instance = conn.execute('SELECT instance_id FROM press_activation WHERE singleton=1').fetchone()
        if (world_id != job['world_id'] or not row or row['world_id'] != world_id or row['status'] != 'generating'
                or (job.get('instance_id') and (not instance or instance[0] != job['instance_id']))):
            raise PressError("生成任务已失效，未写入其他存档。")


def activate(save_name, *, features=False):
    repository.enable(save_name)
    library = Library(store.database_path(save_name))
    library.install()
    if features:
        from football_simulator.press.feature_library import FeatureLibrary
        FeatureLibrary(library.path).install()
    return library
