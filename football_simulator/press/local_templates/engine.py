"""Freeze editorial choices separately from provider-specific request plans."""
from football_simulator.press import PressError,repository
from football_simulator.press.local_templates import ENGINE_ID,TEMPLATE_VERSION

SUPPORTED_VERSIONS=('local-news/1','local-news/2','local-news/3','local-news/4')


def freeze_plan(packet,sources=None,version=TEMPLATE_VERSION):
    if version not in SUPPORTED_VERSIONS:raise PressError('本地模板版本不可用。')
    if packet['meta'].get('report_type','weekly')!='weekly':
        kind=packet['meta']['report_type']
        if kind not in ('phase','competition','transfer','draft','top20'):raise PressError('该类专题模板尚未启用。')
        units=[kind]
        if kind=='top20':
            data=packet['facts'][0]['data']
            units=['top20-overview']+['top20-'+str(r['rank']) for r in data['rankings']]
            if data['changes'].get('dropped_out'):units.append('top20-dropped')
        return {'engine_id':ENGINE_ID,'template_version':version,'kind':kind,
                'content':packet['facts'][0]['data'],'fact_ids':[f['fact_id'] for f in packet['facts']],
                'packet_sha256':repository.digest(packet),'source_sha256':packet['meta']['source_sha256'],
                'sections':[{'competition_id':u} for u in units]}
    from football_simulator.press.planning import build_plan
    editorial=build_plan(packet,sources)
    return {'engine_id':ENGINE_ID,'template_version':version,'kind':'weekly','editorial':editorial,
            'packet_sha256':repository.digest(packet),'source_sha256':repository.digest(sources),
            'sections':[{'competition_id':s['id']} for s in editorial['stories']]}


def render(packet,plan):
    if (plan['engine_id']!=ENGINE_ID or plan['template_version'] not in SUPPORTED_VERSIONS
            or plan['packet_sha256']!=repository.digest(packet)):
        raise PressError('模板版本或事实身份不符。')
    if plan['kind'] not in ('weekly','phase','competition','transfer','draft','top20'):
        raise PressError('该类专题模板尚未启用。')
    # A paused job must retain its original language even after a template update.
    # Static imports keep each frozen version visible to the desktop bundler.
    if plan['template_version']=='local-news/1':
        from football_simulator.press.local_templates.v1 import weekly,phase,competition,transfer,draft,top20
    elif plan['template_version']=='local-news/2':
        from football_simulator.press.local_templates.v2 import weekly,phase,competition,transfer,draft,top20
    elif plan['template_version']=='local-news/3':
        from football_simulator.press.local_templates.v3 import weekly,phase,competition,transfer,draft,top20
    else:
        from football_simulator.press.local_templates import weekly,phase,competition,transfer,draft,top20
    renderer={'weekly':weekly,'phase':phase,'competition':competition,
              'transfer':transfer,'draft':draft,'top20':top20}[plan['kind']]
    return renderer.render(packet,plan)
