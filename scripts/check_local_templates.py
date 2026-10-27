"""Seeded real game boundaries for local template acceptance, never user saves."""
from pathlib import Path
import argparse,json,os,random,sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.game import rules
from football_simulator.press import repository
from football_simulator.press.local_library import activate,LocalLibrary
from football_simulator.press.local_templates.engine import freeze_plan,render
from football_simulator.press.local_templates import TEMPLATE_VERSION
from football_simulator.press.validation import validate_article_basics


def write_sample(output,label,packet,sources=None):
    plan=freeze_plan(packet,sources)
    article,mapping=render(packet,plan)
    validate_article_basics(article,packet)
    for suffix,value in [('article',article),('plan',plan),('mapping',mapping),('packet',packet)]:
        (output/(label+'.'+suffix+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    if sources:(output/(label+'.sources.json')).write_text(json.dumps(sources,ensure_ascii=False))
    lines=['# '+article['title'],'','来源：本地模板 '+TEMPLATE_VERSION+'；本轮独立模拟存档的真实冻结材料。','',article['summary'],'']
    for section in article['sections']:
        lines+=['## '+section['heading'],'']
        for block in section['blocks']:lines += [block['text'],'']
    (output/(label+'.md')).write_text('\n'.join(lines))
    print(label,article['title'],flush=True)
    return article


def step(service,name):
    result=service.advance_week(name)
    for _ in range(10):
        pending=store.load_world(name,readonly=True)['season']['pending']
        if pending.get('ability_review'):
            service.apply_ability_review(name,{r['player_id']:True for r in pending['ability_review']})
        elif pending.get('transfer_review'):
            service.apply_transfer_reviews(name,{r['trade_id']:True for r in pending['transfer_review']})
        elif pending.get('draft',{}).get('status')=='awaiting_input':service.apply_draft(name,None)
        else:break
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--data-name',default='sample-data-final')
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    root=output/args.data_name;os.environ['FS_DATA_ROOT']=str(root);runtime.set_data_root_override(root)
    service=GameService(rng_factory=lambda:random.Random(871),edition_archives=True,honor_profile=rules.HONOR_PROFILE)
    name='LocalSamples'
    service.create_save(name);service.initialize_save(name)
    # First two full seasons provide actual title and Top20/draft histories.
    for season in (1,2):
        for week in range(1,53):step(service,name)
        print('completed season',season,flush=True);service.start_next_season(name)
    store.export_package(name,output/(args.data_name+'-before-local.fsarchive'))
    library=activate(name)
    for week in range(1,53):
        step(service,name)
        if week in (21,42):
            material=repository.load_week(name,3,week)
            write_sample(output,'weekly-swiss' if week==21 else 'weekly-closure',material['packet'],material['sources'])
    # The ordinary fixture is an existing production-export package, not an
    # old generated article. Read only its weekly/source input, never responses.
    inputs=ROOT/'tests/fixtures_press/second_weekly'
    write_sample(output,'weekly-ordinary',json.loads((inputs/'weekly.json').read_text()),json.loads((inputs/'sources.json').read_text()))
    # Use the exact frozen event packets, never rebuild a past event from the
    # final world. These samples also exercise real persistence, not fake API.
    from tests.test_press_local_templates import offline_guards
    import zlib
    targets=[('phase','phase','starpath'),('phase-reign','phase','aureole'),
             ('competition','competition','confluence'),('transfer','transfer',None),
             ('draft','draft',None),('top20','top20',None)]
    receipts=[]
    with offline_guards():
        for label,kind,comp in targets:
            row=next(r for r in library.feature_rows() if r['kind']==kind and (comp is None or r['competition']==comp))
            job=library.reserve_feature(row['report_id'])
            assert library.generate_local(job),label
            saved=library.read_feature(row['report_id'],mark_read=False)['article']
            preview=write_sample(output,label,job['packet'])
            assert preview==saved
            with library.connection() as conn:
                source=conn.execute('SELECT * FROM press_feature_sources WHERE source_version=?',(job['packet']['meta']['source_world_version'],)).fetchone()
                (output/'sources').mkdir(exist_ok=True)
                source_file='sources/source-'+str(source['source_version'])+'.json.zlib'
                (output/source_file).write_bytes(source['compressed_json'])
                metadata=json.loads(conn.execute('SELECT metadata_json FROM press_features WHERE report_id=?',(row['report_id'],)).fetchone()[0])
            receipts.append({'label':label,'report_id':row['report_id'],'source_file':source_file,'metadata':metadata})
        for label,week in [('weekly-swiss',21),('weekly-closure',42)]:
            weekly=LocalLibrary(library.path);job=weekly.reserve(3,week)
            assert weekly.generate_local(job)
            assert weekly.read(3,week,mark_read=False)==write_sample(output,label,job['packet'],job['sources'])
        # Drain the common scheduler to prove automatic local completion.
        published=0
        while library.next_local():
            item=library.next_local()
            target=LocalLibrary(library.path) if item['kind']=='weekly' else library
            job=(target.reserve(item['season'],item['week']) if item['kind']=='weekly' else target.reserve_feature(item['report_id']))
            assert target.generate_local(job),item
            published+=1
        store.export_package(name,output/(args.data_name+'-complete.fsarchive'))
    (output/'local-sample-receipts.json').write_text(json.dumps({'six_kinds':True,'network_and_keychain_fail_if_called':True,
        'additional_queued_reports_published':published,'reports':receipts},ensure_ascii=False,indent=2))
    print('sample save',store.database_path(name),flush=True)


if __name__=='__main__':main()
