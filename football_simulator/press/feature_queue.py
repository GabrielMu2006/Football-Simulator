"""Opt-in, transaction-bound feature material. Never dispatches network calls."""
import json
import zlib
from datetime import datetime, timezone

from football_simulator.game import rules
from football_simulator.press import PressError
from football_simulator.press.repository import canonical, digest

KINDS = {'phase':'阶段报','competition':'赛事总报','transfer':'转会窗报','draft':'选秀报','top20':'Top20专题'}


def installed(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='press_feature_activation'").fetchone() is not None


def boundary(world):
    return 52 if world['season'].get('season_complete') else int(world['season']['week'])-1


def events(world):
    """Completed events only; used both for enable-time baseline and new writes."""
    year=int(world['season_number']);season=world['season'];week=boundary(world);result=[]
    from football_simulator.game.calendar import league_week_for_round
    half_week=league_week_for_round(19)
    def add(kind,key,label,**kwargs):
        result.append({'event_id':f'{year}:{kind}:{key}','kind':kind,'label':label,**kwargs})
    for comp in rules.LEAGUE_COMPETITIONS:
        first_half=[f for f in season['fixtures'] if f['competition']==comp and int(f['week'])<=half_week]
        if len(first_half)==190 and all(f['status']=='completed' for f in first_half):
            add('phase',comp+'-halfway',rules.display_name(comp)+' · 半程回顾',competition=comp,phase='halfway')
    for comp,phase,label in [('aureole','groups','小组赛收官'),('starpath','swiss','瑞士轮收官'),
                             ('ascendant','groups','组外循环收官'),('ascendant','playin','突围赛收官')]:
        if season.get('cups',{}).get(comp,{}).get('stage_done',{}).get(phase):
            add('phase',comp+'-'+phase,rules.display_name(comp)+' · '+label,competition=comp,phase=phase)
    for comp in rules.ALL_COMPETITION_IDS:
        edition=world.get('edition_archives',{}).get(f'{year}:{comp}')
        if edition and edition['completed_week']<=week:
            add('competition',comp,rules.display_name(comp)+' · 赛事总报',competition=comp)
    for window,closed,label in [('winter',27,'冬窗总结'),('summer',52,'夏窗总结')]:
        if week>=closed and not season['pending'].get('transfer_review'):
            add('transfer',window,label,window=window)
    if any(int(log['season'])==year for log in season.get('draft_log',[])) and season['pending'].get('draft',{}).get('status')!='awaiting_input':
        add('draft','draft','选秀回顾')
    if season.get('finalized') and len(season.get('awards',{}).get('top20',[]))==20:
        add('top20','season-top20','赛季 Top20')
    return result


def install(conn):
    from football_simulator.press.repository import enabled
    if not enabled(conn):raise PressError('请先启用报刊。')
    if installed(conn):
        from football_simulator.press.local_templates import install_columns
        install_columns(conn, 'press_features')
        return
    world=json.loads(conn.execute('SELECT data_json FROM world').fetchone()[0])
    conn.execute('CREATE TABLE press_feature_activation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), world_id TEXT NOT NULL, enabled_version INTEGER NOT NULL)')
    conn.execute('CREATE TABLE press_feature_seen (event_id TEXT PRIMARY KEY, baseline INTEGER NOT NULL)')
    conn.execute('''CREATE TABLE press_feature_sources (
        world_id TEXT NOT NULL, source_version INTEGER NOT NULL, source_sha TEXT NOT NULL,
        compressed_json BLOB NOT NULL, PRIMARY KEY(world_id,source_version))''')
    conn.execute('''CREATE TABLE press_features (
        report_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
        label TEXT NOT NULL, competition TEXT, phase TEXT, window TEXT,
        season INTEGER NOT NULL, week INTEGER NOT NULL, world_id TEXT NOT NULL, source_version INTEGER NOT NULL,
        packet_json TEXT, packet_sha TEXT, source_sha TEXT NOT NULL,
        status TEXT NOT NULL, owner TEXT, article_json TEXT, article_sha TEXT, metadata_json TEXT,
        title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, read_at TEXT, error TEXT)''')
    conn.execute('''CREATE TABLE press_feature_attempts (
        token TEXT PRIMARY KEY, season INTEGER NOT NULL, week INTEGER NOT NULL,
        status TEXT NOT NULL, request_json TEXT, request_sha TEXT,
        started_at TEXT NOT NULL, finished_at TEXT, error TEXT)''')
    conn.execute('INSERT INTO press_feature_activation VALUES(1,?,?)',(world['world_id'],world['version']))
    conn.executemany('INSERT INTO press_feature_seen VALUES(?,1)',[(e['event_id'],) for e in events(world)])
    from football_simulator.press.local_templates import install_columns
    install_columns(conn, 'press_features')


def capture(conn,world):
    if not installed(conn):return
    activation=conn.execute('SELECT * FROM press_feature_activation WHERE singleton=1').fetchone()
    if not activation or activation['world_id']!=world['world_id']:raise PressError('专题记录与世界不符。')
    from football_simulator.press.expectations import capture_next
    capture_next(conn,world)
    seen={r[0] for r in conn.execute('SELECT event_id FROM press_feature_seen')}
    fresh=[e for e in events(world) if e['event_id'] not in seen]
    if not fresh:return
    from football_simulator.press.features import capture_snapshot,build_feature
    source=capture_snapshot(conn,boundary(world),history_from=max(1,world['season_number']-2))
    sha=digest(source)
    conn.execute('INSERT OR IGNORE INTO press_feature_sources VALUES(?,?,?,?)',
                 (world['world_id'],world['version'],sha,zlib.compress(canonical(source).encode('utf-8'))))
    for event in fresh:
        # A material failure is isolated from the game command. Its exact source
        # remains available for an explicit retry after repair.
        packet=None;error=None
        try:
            packet=build_feature(source,event['kind'],**{k:event[k] for k in ('competition','phase','window') if k in event})
        except (PressError,ValueError,KeyError,TypeError):
            error='专题材料未完整整理，未调用API。可在修复后重试。'
        rid=packet['meta']['report_id'] if packet else f"feature:{world['world_id']}:{event['event_id']}"
        conn.execute('''INSERT INTO press_features(report_id,event_id,kind,label,competition,phase,window,
          season,week,world_id,source_version,packet_json,packet_sha,source_sha,status,created_at,error)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (rid,event['event_id'],event['kind'],event['label'],event.get('competition'),event.get('phase'),event.get('window'),
           world['season_number'],boundary(world),world['world_id'],world['version'],canonical(packet) if packet else None,
           digest(packet) if packet else None,sha,'queued' if packet else 'failed',datetime.now(timezone.utc).isoformat(),error))
        conn.execute('INSERT INTO press_feature_seen VALUES(?,0)',(event['event_id'],))
        activation_columns = {r[1] for r in conn.execute('PRAGMA table_info(press_activation)')}
        if 'engine_id' in activation_columns:
            active = conn.execute('SELECT engine_id,template_version FROM press_activation WHERE singleton=1').fetchone()
            conn.execute('UPDATE press_features SET engine_id=?,template_version=? WHERE report_id=?', (*active, rid))
