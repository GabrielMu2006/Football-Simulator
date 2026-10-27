"""Feature queue/reader using the existing audited, cached generation engine."""
import json
import uuid
import zlib

from football_simulator.press import PressError, feature_planning, provider, repository
from football_simulator.press import feature_queue
from football_simulator.press.library import Library,now


class FeatureLibrary(Library):
    record_table='press_features'
    attempt_table='press_feature_attempts'

    def install(self):
        super().install()
        with self.connection(write=True) as conn:feature_queue.install(conn)

    def feature_rows(self):
        with self.connection() as conn:
            if not feature_queue.installed(conn):return []
            counts={r['report_id']:r['n'] for r in conn.execute("SELECT report_id,COUNT(DISTINCT competition_id) n FROM press_article_sections WHERE status='ready' GROUP BY report_id")}
            plans={r['report_id']:len(json.loads(r['plan_json'])['sections']) for r in conn.execute('SELECT report_id,plan_json FROM press_article_plans')}
            result=[]
            for row in conn.execute('''SELECT report_id,kind,label,competition,phase,window,season,week,status,title,summary,read_at,error,created_at
                                      FROM press_features ORDER BY season DESC,week DESC,created_at DESC,report_id'''):
                item=dict(row);item['unread']=row['status']=='ready' and not row['read_at']
                item['sections_done']=counts.get(row['report_id'],0)
                item['section_total']=plans.get(row['report_id'],21 if row['kind']=='top20' else 1)
                result.append(item)
            return result

    def overview(self):
        data=super().overview();features=self.feature_rows() if data['enabled'] else []
        data['features']=features;data['unread']+=sum(r['unread'] for r in features)
        return data

    def unread_count(self):
        total=super().unread_count()
        with self.connection() as conn:
            if feature_queue.installed(conn):total+=conn.execute("SELECT COUNT(*) FROM press_features WHERE status='ready' AND read_at IS NULL").fetchone()[0]
        return total

    def latest(self):
        weekly=super().latest()
        with self.connection() as conn:
            row=conn.execute("SELECT * FROM press_features WHERE status='ready' ORDER BY season DESC,week DESC,created_at DESC LIMIT 1").fetchone() if feature_queue.installed(conn) else None
            if row and (not weekly or (row['season'],row['week'])>=(weekly['season'],weekly['week'])):
                return {'season':row['season'],'week':row['week'],'kind':row['kind'],'label':row['label'],**self._article(row)}
        return {**weekly,'kind':'weekly'} if weekly else None

    def read_feature(self,report_id,*,mark_read=True):
        with self.connection(write=mark_read) as conn:
            row=conn.execute('SELECT * FROM press_features WHERE report_id=?',(report_id,)).fetchone() if feature_queue.installed(conn) else None
            if not row or row['status']!='ready':raise PressError('这篇专题尚未刊发。')
            article=self._article(row)
            if mark_read:conn.execute('UPDATE press_features SET read_at=COALESCE(read_at,?) WHERE report_id=?',(now(),report_id))
            return {'season':row['season'],'week':row['week'],'kind':row['kind'],'label':row['label'],'article':article}

    def next_queued(self):
        with self.connection() as conn:
            if not feature_queue.installed(conn):return None
            row=conn.execute("SELECT report_id FROM press_features WHERE status IN ('queued','paused') ORDER BY season,week,created_at,report_id LIMIT 1").fetchone()
            return row[0] if row else None

    def recover(self,live_tokens=()):
        super().recover(live_tokens)
        with self.connection(write=True) as conn:
            if not feature_queue.installed(conn):return
            for row in conn.execute("SELECT owner FROM press_features WHERE status='generating'").fetchall():
                if row['owner'] not in live_tokens:
                    error='上次专题生成已中断，未自动重试；原请求可能已计费。'
                    conn.execute("UPDATE press_features SET status='failed',error=? WHERE owner=?",(error,row['owner']))
                    conn.execute("UPDATE press_feature_attempts SET status='failed',finished_at=?,error=? WHERE token=?",(now(),error,row['owner']))

    def reserve_feature(self,report_id,*,retry=False,engine_id='deepseek',template_version=None):
        with self.connection(write=True) as conn:
            row=conn.execute('SELECT * FROM press_features WHERE report_id=?',(report_id,)).fetchone()
            if not row:raise PressError('未找到启用后冻结的专题。')
            world_id=conn.execute('SELECT world_id FROM world').fetchone()[0]
            if row['world_id']!=world_id:raise PressError('专题与当前世界不符。')
            if row['status'] in ('ready','generating'):return None
            from football_simulator.press.local_templates import engine_of
            if engine_of(row)!=engine_id:
                raise PressError('原引擎任务保留，不自动转换或调用 API。')
            if row['status']=='failed' and not retry:return None
            if row['packet_json']:
                packet=json.loads(row['packet_json'])
                if repository.digest(packet)!=row['packet_sha']:raise PressError('专题材料校验和不符。')
            else:
                saved=conn.execute('SELECT * FROM press_feature_sources WHERE world_id=? AND source_version=?',(world_id,row['source_version'])).fetchone()
                source=json.loads(zlib.decompress(saved['compressed_json']))
                if repository.digest(source)!=saved['source_sha']:raise PressError('专题来源校验和不符。')
                from football_simulator.press.features import build_feature
                packet=build_feature(source,row['kind'],**{k:row[k] for k in ('competition','phase','window') if row[k] is not None})
                packet['meta']['report_id']=report_id
                conn.execute('UPDATE press_features SET packet_json=?,packet_sha=? WHERE report_id=?',
                             (repository.canonical(packet),repository.digest(packet),report_id))
            if packet['meta']['source_sha256']!=row['source_sha']:raise PressError('专题来源身份不符。')
            instance=conn.execute('SELECT instance_id FROM press_activation WHERE singleton=1').fetchone()[0]
            token=str(uuid.uuid4())
            conn.execute("UPDATE press_features SET status='generating',owner=?,error=NULL WHERE report_id=?",(token,report_id))
            conn.execute("INSERT INTO press_feature_attempts(token,season,week,status,started_at) VALUES(?,?,?,'generating',?)",(token,row['season'],row['week'],now()))
            return {'token':token,'world_id':world_id,'instance_id':instance,'season':row['season'],'week':row['week'],
                    'packet':packet,'source_sha':row['source_sha'],'engine_id':engine_id,
                    'template_version':row['template_version'] or template_version}

    def _frozen_plan(self,conn,job,*,grouped=False):
        self._owns(conn,job);self._install_sections(conn)
        rid=job['packet']['meta']['report_id'];sha=repository.digest(job['packet'])
        saved=conn.execute('SELECT * FROM press_article_plans WHERE report_id=?',(rid,)).fetchone()
        if saved:
            plan=json.loads(saved['plan_json'])
            if (repository.digest(plan)!=saved['plan_sha'] or saved['packet_sha']!=sha or saved['source_sha']!=job['source_sha']):
                raise PressError('冻结专题计划不符。')
        else:
            plan=feature_planning.generation_plan(job['packet'])
            for unit in plan['sections']:unit['request']=provider.build_request(unit['request'])
            conn.execute('INSERT INTO press_article_plans VALUES(?,?,?,?,?)',(rid,sha,job['source_sha'],repository.canonical(plan),repository.digest(plan)))
        conn.execute('UPDATE press_feature_attempts SET request_json=?,request_sha=? WHERE token=?',(repository.canonical(plan),repository.digest(plan),job['token']))
        return plan

    @staticmethod
    def _prepare(raw,job,plan,unit):return feature_planning.prepare(raw,job['packet'],plan,unit)

    def _assemble(self,job,plan,fragments):return feature_planning.assemble(job['packet'],plan,fragments)

    def _published(self,conn,job,article):
        conn.execute('UPDATE press_features SET title=?,summary=? WHERE owner=?',(article['title'],article['summary'],job['token']))

    def _owns(self,conn,job):
        world_id=conn.execute('SELECT world_id FROM world').fetchone()[0]
        row=conn.execute('SELECT world_id,status,report_id,packet_sha FROM press_features WHERE owner=?',(job['token'],)).fetchone()
        instance=conn.execute('SELECT instance_id FROM press_activation WHERE singleton=1').fetchone()
        if (not instance or instance[0]!=job['instance_id'] or world_id!=job['world_id'] or not row
            or row['world_id']!=world_id or row['status']!='generating' or row['report_id']!=job['packet']['meta']['report_id']
            or row['packet_sha']!=repository.digest(job['packet'])):
            raise PressError('专题任务已失效，未写入其他存档。')
