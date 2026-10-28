"""Frozen local-news/4 contextual phrasing."""

def comparison_sentence(comparison):
    """Express only frozen adverse axes; do not re-parse numeric source prose."""
    clauses=[];team=comparison['winner'];opponent=comparison['opponent']
    axes=comparison.get('adverse_axes',[])
    if 'previous' in axes:clauses.append('上季联赛排名不及'+opponent)
    if 'current' in axes:clauses.append('赛前在同级联赛中的排名'+('也' if clauses else '')+'落后于'+opponent)
    if 'value' in axes:clauses.append('赛前阵容总身价也更低' if clauses else '赛前阵容总身价低于'+opponent)
    return team+'，'.join(clauses)+'。' if clauses else ''


def surprise_paragraphs(rows,*,campaign=False,opponent_exits_reported=()):
    result=[]
    for r in rows:
        text=(r['team']+'最终捧起冠军奖杯' if campaign else
              f"以第{r['pot']}档身份进入{r['group']}组的{r['team']}，最终从小组突围" if r.get('pot') else r['team']+'拿到了晋级名额')
        if r.get('is_dark_horse'):text+=('，写下了本届赛事的黑马故事' if campaign else '，成为这一阶段的黑马')
        elif any(c['is_upset'] for c in r.get('comparisons',[])):text+='，带来了出线冷门'
        result.append(text+'。')
        for comparison in r.get('comparisons',[]):
            if comparison['is_upset']:
                evidence=comparison_sentence(comparison)
                if evidence:
                    outcome=('' if not campaign and comparison['opponent'] in opponent_exits_reported
                             else comparison['opponent']+('则未能夺冠。' if campaign else '则未能过关。'))
                    result.append(evidence+outcome)
    return result
