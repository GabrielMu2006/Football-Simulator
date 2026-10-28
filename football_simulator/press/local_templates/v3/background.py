"""Frozen third-version expression helpers."""

def paragraph(team,data):
    if not data or not data.get('label'):return ''
    years=data['recent_seasons'];span=f"第{years[0]}至第{years[-1]}赛季" if len(years)>1 else f"第{years[0]}赛季"
    total=data['historical_honor_points'];recent=data['recent_honor_points']
    if data['label']=='传统强队':
        return (f"老牌劲旅{team}的队史荣誉积分已累积到{total}分，{span}合计收获{recent}分，"
                '在历史与近期的荣誉榜上都位居前列。')
    if data['label']=='处于低谷的传统强队':
        return (f"{team}的队史荣誉积分累计{total}分，{span}则拿到{recent}分，"
                '未能跻身同期前列。这支老牌劲旅正经历一段低谷。')
    if data['label']=='新锐':
        return (f"{team}凭借{span}合计{recent}分的荣誉成绩跻身同期前列，成为近年的一股新锐力量。"
                f"球队的队史荣誉积分累计为{total}分。")
    # A short history cannot establish tradition. Avoid repeating the same total
    # as both a career and recent figure when these cover identical seasons.
    return f"{span}，{team}的荣誉积分合计{recent}分，跻身同期强队行列。"


def comparison_sentence(comparison):
    """Express only frozen adverse axes; do not re-parse numeric source prose."""
    clauses=[];team=comparison['winner'];opponent=comparison['opponent']
    axes=comparison.get('adverse_axes',[])
    if 'previous' in axes:clauses.append('上季联赛排名不及'+opponent)
    if 'current' in axes:clauses.append('赛前在同级联赛中的排名'+('也' if clauses else '')+'落后于'+opponent)
    if 'value' in axes:clauses.append('赛前阵容总身价也更低' if clauses else '赛前阵容总身价低于'+opponent)
    return team+'，'.join(clauses)+'。' if clauses else ''


def surprise_paragraphs(rows,*,campaign=False):
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
                if evidence:result.append(evidence+comparison['opponent']+('则未能夺冠。' if campaign else '则未能过关。'))
    return result
