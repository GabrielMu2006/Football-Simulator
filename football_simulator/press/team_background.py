"""Editorial club context from official settled honor totals, never fame."""
from math import ceil


def backgrounds(world):
    editions={int(a['season_number']):a for a in world.get('archives',[])
              if int(a['season_number'])<int(world['season_number']) or world['season'].get('season_complete')}
    years=sorted(editions)
    if not years or years!=list(range(1,years[-1]+1)):return {}
    recent=years[-3:];totals={};last={}
    for tid in world['teams']:
        if any(editions[y].get('honor_totals',{}).get(tid) is None for y in years):continue
        totals[tid]=sum(editions[y]['honor_totals'][tid] for y in years)
        last[tid]=sum(editions[y]['honor_totals'][tid] for y in recent)
    if not totals:return {}
    index=max(0,ceil(len(totals)/4)-1)
    old_cut=sorted(totals.values(),reverse=True)[index];new_cut=sorted(last.values(),reverse=True)[index]
    result={}
    for tid,total in totals.items():
        old=total>=old_cut and total>0;new=last[tid]>=new_cut and last[tid]>0
        # At least one older edition must sit outside the recent window before
        # a long-term club identity can be distinguished from recent strength.
        enough=len(years)>3
        label=('传统强队' if old and new else '处于低谷的传统强队' if old else '新锐' if new else None) if enough else ('近年表现突出的球队' if new else None)
        result[tid]={'label':label,'historical_honor_points':total,'recent_honor_points':last[tid],
                     'history_seasons':years,'recent_seasons':recent,'historically_high':old,'recently_high':new,
                     'method':'honors-quartile/1','full_history':True}
    return result


def paragraph(team,data):
    if not data or not data.get('label'):return ''
    years=data['recent_seasons'];span=f"第{years[0]}至第{years[-1]}赛季" if len(years)>1 else f"第{years[0]}赛季"
    return (team+'属于'+data['label']+f"，已结束赛季累计球队荣誉分{data['historical_honor_points']}分，"
            +span+f"合计{data['recent_honor_points']}分。")
