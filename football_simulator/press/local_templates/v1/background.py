"""Original background expressions used only by local-news/1."""

def paragraph(team,data):
    if not data or not data.get('label'):return ''
    years=data['recent_seasons'];span=f"第{years[0]}至第{years[-1]}赛季" if len(years)>1 else f"第{years[0]}赛季"
    return (team+'属于'+data['label']+f"，已结束赛季累计球队荣誉分{data['historical_honor_points']}分，"
            +span+f"合计{data['recent_honor_points']}分。")


def surprise_paragraphs(rows):
    result=[]
    for r in rows:
        label='本阶段的黑马' if r.get('is_dark_horse') else '制造出线冷门' if any(c['is_upset'] for c in r.get('comparisons',[])) else '晋级'
        prefix=(f"{r['group']}组第{r['pot']}档球队" if r.get('pot') else '')
        result.append(prefix+r['team']+('成为'+label if label=='本阶段的黑马' else label)+'。')
        for comparison in r.get('comparisons',[]):
            if comparison['is_upset']:result.append('；'.join(b['text'] for b in comparison['basis'])+'。')
    return result
