"""Limited, versioned prose gates; ambiguous prose still needs independent reading."""
from __future__ import annotations

import re

from football_simulator.press import PressError

NUM = r"(?:[0-9]+|[零〇一二两三四五六七八九十百]+)"


def need(condition, message):
    if not condition:
        raise PressError(message)


def number(value):
    if value.isdigit():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits.update({"两": 2, "〇": 0})
    total, current = 0, 0
    for c in value:
        if c in ("十", "百"):
            total += (current or 1) * {"十": 10, "百": 100}[c]
            current = 0
        else:
            current = digits[c]
    return total + current


def speculative(prefix):
    return bool(re.search(r"有望|可能|若|或将|力争|争取|能否|尚未|未能|没能|并未|并非|不是|没有|无缘", prefix[-18:]))


def pieces(article):
    yield article["title"], article["title_fact_ids"]
    yield article["summary"], article["summary_fact_ids"]
    for section in article["sections"]:
        yield section["heading"], section["heading_fact_ids"]
        for block in section["blocks"]:
            yield block["text"], block["fact_ids"]


def subject_windows(sentence, names, rows):
    """Carry the subject past a match's object, not just the nearest team name.

    Explicit new subjects and 前者/后者 reset it. This is a bounded grammar,
    not a general dependency parser: unsupported ellipsis is left to reading.
    """
    mentions = sorted((m.start(), m.end(), tid) for tid, name in names
                      for m in re.finditer(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", sentence))
    boundaries, recent, subject = [], [], None
    for start, end, tid in mentions:
        prefix = sentence[:start]
        is_object = re.search(r"(?:击败|战胜|不敌|负于|输给|战平|逼平|击退|击溃|大胜|险胜|输给了|被|与|对阵|迎战|领先|落后)(?:了)?\s*$|(?:面对|对手是)[^，,：:]{0,24}$", prefix)
        if subject is None and is_object:
            # '他们排名第二，落后B' has no resolved subject in this sentence.
            # Never turn its sole named OBJECT B into the owner of that rank.
            recent.append(tid)
            continue
        if subject is None or not is_object:
            subject = tid
            boundaries.append((start if boundaries else 0, tid))
        recent.append(tid)
        # Explicit anaphora can refer back to either participant.
        stop = next((s for s, _, _ in mentions if s >= end), len(sentence))
        for pronoun in re.finditer(r"(?:，|,)\s*(前者|后者)", sentence[end:stop]):
            if len(recent) >= 2:
                boundaries.append((end + pronoun.end(), recent[-2] if pronoun.group(1) == "前者" else recent[-1]))
    boundaries.sort()
    if not boundaries and len(rows) == 1 and re.match(r"\s*(?:他们|该队|球队|其|目前|赛后|此役过后|七轮战罢)", sentence):
        boundaries = [(0, next(iter(rows)))]
    return [(tid, sentence[start:boundaries[i + 1][0] if i + 1 < len(boundaries) else len(sentence)])
            for i, (start, tid) in enumerate(boundaries)]


def old_season_records(packet, sources):
    """Derive reading evidence in memory for old frozen v2 material; never rewrite it."""
    result = {}
    if sources is not None and packet["meta"]["exporter_version"] == "weekly-export/2":
        from football_simulator.press.standings import season_records
        book = sources["records"]
        for comp in ("premier", "second"):
            if "table:" + comp not in book:
                continue
            table = book["table:" + comp]["value"]
            result.update(season_records(table["team_ids"], [book[k]["value"] for k in table["match_keys"]], table["previous_rank"]))
    return result


def check_firsts(window, tid, cited, record, packet):
    loss_claims = re.finditer(r"(?:本赛季|本季|赛季|联赛)[^。；！？]{0,10}?(?:首场失利|首败|首次失利|第一次失利)", window)
    for m in loss_claims:
        if speculative(window[:m.end()]):
            continue
        # A league record cannot prove a first defeat in ALL competitions.
        need("各项赛事" not in window[max(0, m.start() - 8):m.end()], "赛季首败缺少各项赛事的完整证据")
        need("联赛" in m.group() or any(e["name_en"] in window for cid, e in packet["entities"]["competitions"].items()
             if cid in ("premier", "second")), "首败缺少明确联赛范围，不能用联赛证据证明杯赛或全赛事首败")
        table_facts = [f for f in cited if f["kind"] == "table_row" and f["data"]["team_id"] == tid]
        need(record is not None and table_facts, "赛季首败缺少完整赛季证据")
        comp_ids = {f["competition_id"] for f in table_facts}
        losses = [f["data"] for f in cited if f["kind"] == "match_result" and f["competition_id"] in comp_ids
                  and f["time"]["season"] == packet["meta"]["season"]
                  and f["time"]["week"] == packet["meta"]["completed_week"]
                  and tid in f["team_ids"] and f["data"]["on_field_winner_team_id"] not in (None, tid)]
        need(any(d["match_id"] == record["first_loss_match_id"] for d in losses), "赛季首败错误：本场并非该队本季联赛首次失利")
    for m in re.finditer(r"(?:本赛季|本季)[^。；！？]{0,8}?(?:首次|第一次)[^。；！？]{0,12}?(?:榜首|领跑|登顶)", window):
        if not speculative(window[:m.end()]):
            need(record is not None and record["first_lead_week"] == packet["meta"]["completed_week"], "本季首次领跑缺少历周榜首证据或此前已领跑")


def source_pre_tables(packet, sources):
    if sources is None:
        return {}
    from football_simulator.game.calendar import standings_from_matches
    from football_simulator.press.builder import order
    result = {}
    book = sources["records"]
    for comp in ("premier", "second"):
        if "table:" + comp not in book:
            continue
        t = book["table:" + comp]["value"]
        games = [book[k]["value"] for k in t["match_keys"]]
        for m in games:
            if m["week"] != packet["meta"]["completed_week"]:
                continue
            rows = standings_from_matches(t["team_ids"], [g for g in games if order(g) < order(m)], previous_rank=t["previous_rank"])
            result[m["match_id"]] = {r["team_id"]: i for i, r in enumerate(rows, 1)}
    return result


def check_rank(window, d, tid, cited, names, pre_tables):
    before = d["week_comparison"]["before_rank"]
    pattern = r"(跃升至|上升至|下降至|升至|升到|跃居|跌至|下滑至|降至|退至|退居|仍列|仍居|位列|排名|位居|排在)\s*(第)?\s*(" + NUM + r")\s*(位|名)?(?![0-9零〇一二两三四五六七八九十百])"
    for m in re.finditer(pattern, window):
        if speculative(window[:m.start()]):
            continue
        action, ordinal, value, unit = m.groups()
        tail = window[m.end():]
        # Points, goals, rounds and appearances never become rank assertions.
        if re.match(r"\s*(?:分|球|场|轮|次|个|分钟|岁|%|万)", tail):
            continue
        if not ordinal and not unit and action not in ("排名", "位列", "位居", "仍列", "仍居", "排在"):
            continue
        # In 'A面对排名第9的B', this rank modifies B, not the subject A.
        if any(re.match(r"的\s*" + re.escape(name), tail) for _, name in names):
            continue
        rank = number(value)
        prefix = window[:m.start()]
        if re.search(r"(?:分别|依次)\s*$", prefix):
            # A distributed list cannot be checked as the last team's rank.
            continue
        clause = re.split(r"[，,]", prefix)[-1]
        phases = re.findall(r"周初|上周|本周开始前|赛前|赛后|周末|拿下这场后|此役过后|目前", clause)
        phase = phases[-1] if phases else None
        origin = re.search(r"从(?:周初|上周|赛前)?第\s*(" + NUM + r")(?:名|位)?\s*$", prefix)
        if origin is None and phase in ("周初", "上周", "本周开始前"):
            need(before == rank, "正文周初名次与冻结对照不符")
            continue
        before_match = [f["data"]["rank"] for f in cited if f["kind"] == "table_row" and f["data"]["team_id"] == tid and f["data"]["timing"] == "before_match"]
        before_match += [pre_tables[f["data"]["match_id"]][tid] for f in cited if f["kind"] == "match_result"
                         and tid in f["team_ids"] and f["data"]["match_id"] in pre_tables]
        if origin is None and phase == "赛前":
            need(bool(before_match) and rank in before_match, "正文赛前名次缺少对应赛前事实")
            continue
        need(d["rank"] == rank, "正文名次与本周积分榜不符")
        if origin:
            # Without an explicit week boundary, either a cited match's pre-table
            # or the week-start table can be the stated origin. Never conflate them.
            origins = [before] + before_match
            if re.search(r"周初|上周|本周开始前", prefix):
                origins = [before]
            elif "赛前" in prefix:
                origins = before_match
            need(number(origin.group(1)) in origins, "正文排名起点与周初或已引用赛前名次不符")
            before = number(origin.group(1))
        if action in ("升至", "升到", "跃居", "跃升至", "上升至"):
            need(before is not None and before > rank, "正文称排名上升，但没有对应变化")
        elif action in ("跌至", "下滑至", "降至", "下降至", "退至", "退居"):
            need(before is not None and before < rank, "正文称排名下降，但没有对应变化")
        elif action in ("仍列", "仍居"):
            need(before == rank, "正文称排名未变，但周初与周末名次不同")


def check_gaps(text, leaders):
    # Anonymous headlines still require a cited, unambiguous competition.
    for m in re.finditer(r"榜首\s*(" + NUM + r")\s*分(?:之差|优势|差距)[^。；！？]{0,6}(?:易主|换人)", text):
        if not speculative(text[:m.start()]):
            contexts = [d["lead_context"] for d in leaders]
            need(len(contexts) == 1 and contexts[0]["leader_changed"] is True
                 and contexts[0]["before_gap"] == number(m.group(1)), "榜首易主标题的分差与周初事实不符或赛事指代不明")


def check_chasing_gap(window, tid, cited, packet):
    own = next((f for f in cited if f["kind"] == "table_row" and f["data"]["team_id"] == tid
                and f["data"]["timing"] == "end_of_week"), None)
    if own is None:
        return
    comp = own["competition_id"]
    rows = [f["data"] for f in packet["facts"] if f["kind"] == "table_row"
            and f["competition_id"] == comp and f["data"]["timing"] == "end_of_week"]
    current_leader = next((r for r in rows if r["rank"] == 1), None)
    if current_leader is None:
        return
    own_data = own["data"]
    old_gap = max(r["week_comparison"]["before_points"] for r in rows) - own_data["week_comparison"]["before_points"]
    new_gap = current_leader["points"] - own_data["points"]
    cited_leader = any(f["kind"] == "table_row" and f["competition_id"] == comp and f["data"].get("rank") == 1
                       and f["data"]["timing"] == "end_of_week" for f in cited)
    # A production table-row citation also cites the full shared league table
    # proof; do not reject a valid gap just because its leader row isn't repeated.
    table_covered = cited_leader or any(s["key"] == "table:" + comp for s in own["sources"])
    phrase = r"(?:与榜首(?:的)?(?:差距|分差)|落后榜首)"
    for m in re.finditer(phrase + r"(?:为|达|是|至)?\s*(" + NUM + r")\s*分", window):
        if not speculative(window[:m.start()]):
            need(table_covered, "追赶分差缺少对应榜首引用")
            before = re.search(r"(?:周初|上周)[^，,]{0,10}$", window[:m.start()])
            need(number(m.group(1)) == (old_gap if before else new_gap), "追赶榜首的分差数值错误")
    for m in re.finditer(phrase + r"(?:从|由)\s*(" + NUM + r")\s*分?\s*(扩大|缩小|缩减|压缩)?(?:到|至|变为|变成)\s*(" + NUM + r")\s*分", window):
        if not speculative(window[:m.start()]):
            need(table_covered and number(m.group(1)) == old_gap and number(m.group(3)) == new_gap, "追赶分差的起点或终点错误")
            if m.group(2):
                need(new_gap > old_gap if m.group(2) == "扩大" else new_gap < old_gap, "追赶分差变化方向错误")


def style_advice(article, packet):
    """Advisory diagnostics are not factual rejection or a rewrite permission."""
    text = "\n".join(t for t, _ in pieces(article))
    blocks = [b for s in article["sections"] for b in s["blocks"]]
    ids = {fid for b in blocks for fid in b["fact_ids"]}
    count = sum(f["kind"] == "match_result" and f["fact_id"] in ids
                and f["time"]["week"] == packet["meta"]["completed_week"] for f in packet["facts"])
    flags = []
    if count > 8: flags.append("赛果覆盖偏多：建议围绕1–2条主线压缩支线")
    if len(article["sections"]) > 3: flags.append("小标题偏多：建议合并相关主线")
    if sum(b["kind"] in ("fan_voice", "media_comment") for b in blocks) > 1: flags.append("虚构评论超过一段")
    if re.search(r"不败金身|争冠剧本|撕掉一页|进入净胜球阶段", text): flags.append("存在空泛修辞或将排序依据写成赛制阶段")
    if re.search(r"未记名|默认球员|匿名进球", text): flags.append("正文暴露球员筛选痕迹")
    return {"match_results_cited_in_body": count, "advisory_flags": flags, "style_requires_independent_reading": True}


def validate_narrative(article, packet, sources=None):
    if packet["meta"]["exporter_version"] not in ("weekly-export/2", "weekly-export/3", "weekly-export/4", "weekly-export/5"):
        return {"narrative_policy": "legacy_material", "prose_semantics": "requires_independent_reading"}
    facts = {f["fact_id"]: f for f in packet["facts"]}
    teams = packet["entities"]["teams"]
    names = sorted(((t, e["name_en"]) for t, e in teams.items()), key=lambda row: -len(row[1]))
    old_records = old_season_records(packet, sources)
    pre_tables = source_pre_tables(packet, sources)
    seen_stadiums = set()
    for text, refs in pieces(article):
        cited = [facts[fid] for fid in refs]
        rows = {f["data"]["team_id"]: f["data"] for f in cited if f["kind"] == "table_row" and f["data"]["timing"] == "end_of_week"}
        leaders = [d for d in rows.values() if d["rank"] == 1 and "lead_context" in d]
        check_gaps(text, leaders)
        for m in re.finditer(r"同一天|同日|当晚", text):
            if not speculative(text[:m.start()]):
                need(False, "冻结事实只有周次，不能断言同一天或具体比赛日")
        for match in re.finditer(r"榜首换人|新榜首|新领头羊|登顶榜首|(?:登上|升至|跃居|重返|夺回)[^，,。；！？]{0,35}榜首", text):
            if not speculative(text[:match.start()]):
                need(any(d["lead_context"]["leader_changed"] is True for d in leaders), "正文声称更换榜首，但缺少真实榜首变更证据")
        for match in re.finditer(r"继续领跑|(?:继续|仍然|仍)[^，,。；！？]{0,12}榜首", text):
            if not speculative(text[:match.start()]):
                need(any(d["week_comparison"]["before_rank"] == 1 for d in leaders), "正文声称延续榜首，但缺少周初榜首证据")
        for sentence in re.split(r"[。！？；\n]", text):
            for tid, window in subject_windows(sentence, names, rows):
                d = rows.get(tid)
                record = d.get("season_record") if d else None
                check_firsts(window, tid, cited, record or old_records.get(tid), packet)
                if d is None:
                    continue
                before = d["week_comparison"]["before_rank"]
                for m in re.finditer(r"登顶(?:榜首)?|(?:登上|升至|跃居|重返|夺回)[^，,。；！？]{0,35}榜首", window):
                    if speculative(window[:m.start()]):
                        continue
                    if "榜首" not in m.group() and any(e["name_en"] in window for cid, e in packet["entities"]["competitions"].items() if cid not in ("premier", "second")):
                        continue
                    need(d["rank"] == 1 and before is not None and before > 1, "正文把延续领跑或首轮种子写成新登顶")
                for m in re.finditer(r"继续领跑|(?:继续|仍然|仍)[^，,。；！？]{0,12}榜首", window):
                    if not speculative(window[:m.start()]):
                        need(d["rank"] == 1 and before == 1, "正文声称延续榜首，但周初并非榜首")
                check_rank(window, d, tid, cited, names, pre_tables)
                check_chasing_gap(window, tid, cited, packet)
                for m in re.finditer(r"(?:领先优势|领先分差)(?:从|由)\s*(" + NUM + r")\s*分?\s*(?:扩大|缩小)?(?:至|到|变为|变成)\s*(" + NUM + r")\s*分", window):
                    if not speculative(window[:m.start()]):
                        context = d.get("lead_context", {})
                        need(context.get("leader_changed") is False and number(m.group(1)) == context["before_gap"]
                             and number(m.group(2)) == context["after_gap"], "正文领先分差的起点或终点错误")
                for m in re.finditer(r"(?:领先(?:优势|分差)?|领先第二名|领先第2名|与第二名的分差)[^。；！？]{0,12}?(扩大|缩小)(?:至|到|为)?\s*(" + NUM + r")?", window):
                    if speculative(window[:m.start()]):
                        continue
                    context = d.get("lead_context", {})
                    delta = context.get("same_leader_gap_change")
                    need(delta is not None and (delta > 0 if m.group(1) == "扩大" else delta < 0), "正文领先优势变化缺少同一榜首的前后分差证据")
                    if m.group(2): need(number(m.group(2)) == context["after_gap"], "正文领先分差数值错误")
                for m in re.finditer(r"(?:领先第二名|领先第2名|领先优势|领先分差|与第二名的分差)(?:为|达|是|至)?\s*(" + NUM + r")\s*分", window):
                    if not speculative(window[:m.start()]):
                        key = "before_gap" if re.search(r"(?:周初|上周)[^，,]{0,10}$", window[:m.start()]) else "after_gap"
                        need(d.get("lead_context", {}).get(key) == number(m.group(1)), "正文静态领先分差数值错误")
        if re.search(r"主场(?:连续)?[一二三四五六七八九十0-9]+场(?:不败|不胜|连胜)|球场(?:里|上)?的(?:那)?[一二三四五六七八九十0-9]+场(?:不败|不胜)", text):
            need(any(f["kind"] == "streak" and f["data"]["scope"] == "home" for f in cited), "没有主场纪录事实，不能将全赛事纪录改为主场纪录")
        for sid, stadium in packet["entities"]["stadiums"].items():
            if sid not in seen_stadiums and any(stadium[k] in text for k in ("name_zh", "name_en")):
                need(stadium["name_zh"] in text and stadium["name_en"] in text, "球场首次出现必须使用既定中英双语名")
                seen_stadiums.add(sid)
    all_text = "\n".join(text for text, _ in pieces(article))
    for topic in packet["editorial"]["topics"]:
        if topic["topic_id"] not in article["used_topic_ids"] or not topic["event_type"].startswith("standout:"):
            continue
        for fid in topic["required_fact_ids"]:
            f = facts[fid]
            if f["kind"] == "player_match":
                need(packet["entities"]["players"][f["data"]["player_id"]]["name"] in all_text, "人物主线缺少实体表中的正式姓名")
    return {"narrative_policy": "weekly-narrative/3", "prose_semantics": "requires_independent_reading", "editorial_advice": style_advice(article, packet)}
