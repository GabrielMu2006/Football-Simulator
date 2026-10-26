"""Explicit offline UI sample transport; never used by the normal API path."""
import json


def simulated_transport(request, _key):
    # The material JSON is the final object in the user prompt.
    text = request['messages'][1]['content']
    if text.startswith('{') and json.loads(text).get('report_type') in ('phase','competition','transfer','draft','top20'):
        payload=json.loads(text)
        article={'title':'模拟专题 · '+payload['focus'],'deck':'以下为离线模拟专题，不调用网络。',
                 'body':'## '+payload['focus']+'\n\n模拟专题已按冻结材料完成，用于验证后台、缓存和阅读流程。'}
        return {'id':'offline-feature-sample','model':'deepseek-flash','system_fingerprint':'simulated-only',
                'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(article,ensure_ascii=False)}}],
                'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}
    if text.startswith('{') and 'stories' in json.loads(text):
        payload = json.loads(text)
        article = {'title': '模拟周报 · 本周观察', 'deck': '以下为离线模拟内容。', 'sections': [
            {'heading': story['angle'], 'story_ids': [story['id']], 'paragraphs': ['模拟周报 · ' + story['angle'] + '。']}
            for story in payload['stories']]}
        return {'id': 'offline-stories-sample', 'model': 'deepseek-flash', 'system_fingerprint': 'simulated-only',
                'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(article, ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
    if text.startswith('{') and 'story_cards' in json.loads(text):
        payload = json.loads(text)
        card = payload['story_cards']; statements = card['verified_statements']
        first = next((row for row in statements if row['fact_id'].startswith('match:')), statements[0])
        refs = [first['fact_id']]
        body = '模拟周报 · ' + first['text'].split('；主队=', 1)[0] + '。'
        article = {'schema_version': 'press.article/0.1-draft', 'report_id': payload['meta']['report_id'],
                   'title': '模拟周报', 'summary': body, 'title_fact_ids': refs, 'summary_fact_ids': refs,
                   'used_topic_ids': [], 'sections': [{'heading': card['competition_name'], 'heading_fact_ids': refs,
                     'blocks': [{'kind': 'body', 'label': None, 'text': body, 'fact_ids': refs}]}]}
        return {'id': 'offline-section-sample', 'model': 'deepseek-flash', 'system_fingerprint': 'simulated-only',
                'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(article, ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
    start = text.index('{"editorial":')
    packet, _ = json.JSONDecoder().raw_decode(text[start:])
    topic = packet['editorial']['topics'][0]
    refs = list(topic['required_fact_ids'])
    match = next(f for f in packet['facts'] if f['kind'] == 'match_result')
    refs = list(dict.fromkeys(refs + [match['fact_id']]))
    data, teams = match['data'], packet['entities']['teams']
    home, away = (teams[data[k]]['name_en'] for k in ('home_team_id', 'away_team_id'))
    score = data['regulation']
    body = f"{home} 与 {away} 在常规时间踢成 {score['home']}–{score['away']}。新一周的故事已经留下了第一笔。"
    article = {'schema_version': 'press.article/0.1-draft', 'report_id': packet['meta']['report_id'],
               'title': '模拟周报 · 新一周的球场故事', 'summary': body,
               'title_fact_ids': refs, 'summary_fact_ids': refs, 'used_topic_ids': [topic['topic_id']],
               'sections': [{'heading': '赛场一瞥', 'heading_fact_ids': refs,
                             'blocks': [{'kind': 'body', 'label': None, 'text': body, 'fact_ids': refs}]}]}
    return {'id': 'offline-ui-sample', 'model': 'deepseek-flash', 'system_fingerprint': 'simulated-only',
            'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(article, ensure_ascii=False)}}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
