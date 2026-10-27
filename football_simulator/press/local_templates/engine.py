"""Freeze editorial choices separately from provider-specific request plans."""
from football_simulator.press import PressError,repository
from football_simulator.press.local_templates import ENGINE_ID,TEMPLATE_VERSION


def freeze_plan(packet,sources=None,version=TEMPLATE_VERSION):
    if version!='local-news/1':raise PressError('本地模板版本不可用。')
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
    if (plan['engine_id']!=ENGINE_ID or plan['template_version']!='local-news/1'
            or plan['packet_sha256']!=repository.digest(packet)):
        raise PressError('模板版本或事实身份不符。')
    if plan['kind']=='weekly':
        from football_simulator.press.local_templates.weekly import render as weekly
        return weekly(packet,plan)
    if plan['kind']=='phase':
        from football_simulator.press.local_templates.phase import render as phase
        return phase(packet,plan)
    if plan['kind']=='competition':
        from football_simulator.press.local_templates.competition import render as competition
        return competition(packet,plan)
    if plan['kind']=='transfer':
        from football_simulator.press.local_templates.transfer import render as transfer
        return transfer(packet,plan)
    if plan['kind']=='draft':
        from football_simulator.press.local_templates.draft import render as draft
        return draft(packet,plan)
    from football_simulator.press.local_templates.top20 import render as top20
    return top20(packet,plan)
