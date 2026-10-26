"""Offline contract and deterministic semantic checks, without extra dependencies.

The small structural validator intentionally supports ONLY the keywords used
by our bundled contracts, not arbitrary JSON Schema. CI also checks these files
with the reference jsonschema implementation where available. Prose semantics
still require the independent reading checklist; citations are not truth proof.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re

from football_simulator.press import PressError

CONTRACTS = Path(__file__).parent / "contracts"
SUPPORTED = {"$schema", "$id", "$ref", "$defs", "title", "description", "type", "properties", "required", "additionalProperties", "items", "minItems", "minimum", "maximum", "minLength", "pattern", "const", "enum", "anyOf", "oneOf"}


def same(a, b):
    return a == b and (isinstance(a, bool) == isinstance(b, bool))


def shape(value, schema, root=None, path="$"):
    root = root or schema
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise PressError(f"Unsupported contract keywords: {sorted(unknown)}")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise PressError("External schema references are not allowed")
        shape(value, root["$defs"][ref[len('#/$defs/'):]], root, path)
    for union in ("anyOf", "oneOf"):
        if union in schema:
            valid = 0
            for candidate in schema[union]:
                try:
                    shape(value, candidate, root, path)
                    valid += 1
                except PressError:
                    pass
            if not valid or (union == "oneOf" and valid != 1):
                raise PressError(f"{path}: invalid {union}")
    if "const" in schema and not same(value, schema["const"]):
        raise PressError(f"{path}: wrong constant")
    if "enum" in schema and not any(same(value, item) for item in schema["enum"]):
        raise PressError(f"{path}: invalid enum")
    kind = schema.get("type")
    allowed = {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list), "string": lambda: isinstance(value, str),
               "integer": lambda: isinstance(value, int) and not isinstance(value, bool), "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
               "boolean": lambda: isinstance(value, bool), "null": lambda: value is None}
    if kind and not allowed[kind]():
        raise PressError(f"{path}: expected {kind}")
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            raise PressError(f"{path}: missing required field")
        for key, item in value.items():
            properties = schema.get("properties", {})
            if key in properties:
                shape(item, properties[key], root, path + "." + key)
            elif schema.get("additionalProperties") is False:
                raise PressError(f"{path}: unknown field {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                shape(item, schema["additionalProperties"], root, path + "." + key)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise PressError(f"{path}: insufficient items")
        if "items" in schema:
            for i, item in enumerate(value):
                shape(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or ("pattern" in schema and re.search(schema["pattern"], value) is None):
            raise PressError(f"{path}: invalid string")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise PressError(f"{path}: out of range")


def contract(value, filename):
    shape(value, json.loads((CONTRACTS / filename).read_text(encoding="utf-8")))


def require(condition, message):
    if not condition:
        raise PressError(message)


def moment(t):
    return int(t["season"]), int(t["week"])


def _unique(rows, key):
    out = {row[key]: row for row in rows}
    require(len(out) == len(rows), "重复ID: " + key)
    return out


def validate_packet(packet, sources=None):
    from football_simulator.press.builder import canonical_match, field_result, order, status_for_team
    contract(packet, "weekly-input.schema.json")
    meta = packet["meta"]; cutoff = moment(meta["cutoff"])
    require(cutoff == (meta["season"], meta["completed_week"]), "完成周与截止时点不符")
    facts = _unique(packet["facts"], "fact_id")
    entities = packet["entities"]
    matches = {f["data"]["match_id"]: f for f in facts.values() if f["kind"] == "match_result"}
    require(len(matches) == sum(f["kind"] == "match_result" for f in facts.values()), "比赛重复")
    if meta["source_mode"] == "production_export":
        require(sources is not None, "真实导出必须提供冻结来源包")
        require(meta.get("source_world_version") and meta.get("save_instance_id"), "缺少来源身份")
        require(sources["identity"] == {"world_id": meta["world_id"], "instance_id": meta["save_instance_id"], "source_world_version": meta["source_world_version"], "season": meta["season"], "week": meta["completed_week"]}, "来源版本或存档身份不符")
    book = (sources or {}).get("records", {})
    compared_tables = {}
    compared_pre_tables = {}
    modern = meta["exporter_version"] in ("weekly-export/2", "weekly-export/3", "weekly-export/4")
    known_matches = set(matches) | {r["value"]["match_id"] for r in book.values() if r["kind"] == "match"}
    for f in facts.values():
        require(moment(f["time"]) <= cutoff, "发现未来事实")
        for field, table in (("team_ids", "teams"), ("player_ids", "players")):
            require(all(i in entities[table] for i in f[field]), "未知实体引用")
        require(not f["competition_id"] or f["competition_id"] in entities["competitions"], "未知赛事")
        require(set(f["match_ids"]) <= known_matches, "未知比赛引用")
        for s in f["sources"]:
            require(moment(s["as_of"]) <= moment(f["time"]) <= cutoff, "事实早于其可知来源或发现未来来源")
            if meta["source_mode"] == "production_export":
                require(s["kind"] != "synthetic", "真实导出含合成来源")
            if sources is not None:
                require(s["key"] in book and book[s["key"]]["kind"] == s["kind"] and book[s["key"]]["as_of"] == s["as_of"], "来源引用不符")
        if sources is not None:
            proof = book.get("fact:" + f["fact_id"])
            require(proof is not None and proof["value"]["result"] == f["data"], "事实与冻结证据不符")
            require(all(k in book and moment(book[k]["as_of"]) <= cutoff for k in proof["value"]["evidence_keys"]), "证据不完整或包含未来")
        d = f["data"]
        for key in ("team_id", "home_team_id", "away_team_id", "former_team_id", "current_team_id", "departure_destination_team_id", "team_a_id", "team_b_id"):
            if key in d:
                require(d[key] in entities["teams"] and d[key] in f["team_ids"], "数据内部队伍引用不符")
        if "player_id" in d:
            require(d["player_id"] in f["player_ids"], "数据内部球员引用不符")
        if "entity_id" in d:
            require(d["entity_id"] in f["team_ids"] + f["player_ids"], "纪录实体引用不符")
        for key in ("match_id", "affected_match_id"):
            if key in d:
                require(d[key] in f["match_ids"], "数据内部比赛引用不符")
        if f["kind"] == "match_result":
            h, a = d["home_team_id"], d["away_team_id"]
            require(h != a, "比赛对手相同")
            hg = d["regulation"]["home"] + (d["extra_time"] or {}).get("home", 0)
            ag = d["regulation"]["away"] + (d["extra_time"] or {}).get("away", 0)
            require(d["on_field_winner_team_id"] == (h if hg > ag else a if ag > hg else None), "场上胜者与比分不符")
            pk = d["shootout"]
            if pk:
                aggregate_tied = any(t["kind"] == "tie_result" and d["match_id"] in t["data"]["leg_match_ids"] and t["data"]["complete"] and t["data"]["team_a_goals"] == t["data"]["team_b_goals"] for t in facts.values())
                require((hg == ag or (d["leg"] == 2 and aggregate_tied)) and pk["home"] != pk["away"], "无效点球结果")
            require(d["shootout_winner_team_id"] == (h if pk["home"] > pk["away"] else a) if pk else d["shootout_winner_team_id"] is None, "点球胜者错误")
            require(d["stadium_id"] is None or d["stadium_id"] in entities["stadiums"], "未知球场")
            if sources is not None:
                raw = book["match:" + d["match_id"]]["value"]
                require(canonical_match(raw, entities["players"]) == d, "比赛与原始赛果不符")
        elif f["kind"] == "player_match":
            require(d["match_id"] in matches, "球员出场比赛不存在")
            require(d["team_id"] in matches[d["match_id"]]["team_ids"], "球员出场球队不是比赛球队")
            if sources is not None:
                raw = book[f"player:{d['match_id']}:{d['player_id']}"]["value"]
                require(all(raw[k] == v for k, v in d.items() if k != "appeared") and raw["appeared"] == 1, "球员数据与逐场事实不符")
        elif f["kind"] == "table_row" and d["timing"] == "end_of_week" and modern:
            require("week_comparison" in d, "缺少周初与周末排名对照")
            if sources is not None:
                from football_simulator.press.standings import compare_tables, season_records
                from football_simulator.game.calendar import standings_from_matches
                comp = f["competition_id"]
                if comp not in compared_tables:
                    table = book["table:" + comp]["value"]
                    played = [book[k]["value"] for k in table["match_keys"]]
                    require(all(m["competition"] == comp and m["season_number"] == meta["season"] and m["week"] <= meta["completed_week"] for m in played), "积分榜混入其他赛事、赛季或未来比赛")
                    require(len({m["match_id"] for m in played}) == len(played), "积分榜重复计算比赛")
                    before = [m for m in played if m["week"] < meta["completed_week"]]
                    require(table.get("before_match_keys") == ["match:" + m["match_id"] for m in before], "周初积分榜混入本周比赛")
                    rows = standings_from_matches(table["team_ids"], played, previous_rank=table["previous_rank"])
                    old_rows = standings_from_matches(table["team_ids"], before, previous_rank=table["previous_rank"])
                    require(table["rows"] == rows and table.get("before_rows") == old_rows, "周初或周末积分榜与原始比赛不符")
                    comparisons, lead = compare_tables(old_rows, rows)
                    records = season_records(table["team_ids"], played, table["previous_rank"]) if meta["exporter_version"] in ("weekly-export/3", "weekly-export/4") else None
                    compared_tables[comp] = ({r["team_id"]: (i, r) for i, r in enumerate(rows, 1)}, comparisons, lead, records)
                rows, comparisons, lead, records = compared_tables[comp]
                rank, row = rows[d["team_id"]]
                require((d["rank"], d["points"], d["played"]) == (rank, row["points"], row["played"]), "周末名次或积分错误")
                require(d["week_comparison"] == comparisons[d["team_id"]], "周初排名、积分或升降幅度错误")
                require(d.get("lead_context") == (lead if rank == 1 else None), "榜首变更或领先分差错误")
                if records is not None:
                    require(d.get("season_record") == records[d["team_id"]], "赛季胜平负、首败或首次领跑证据错误")
            if meta["exporter_version"] in ("weekly-export/3", "weekly-export/4"):
                require("season_record" in d, "缺少完整赛季首败与首次领跑证据")
            if d.get("lead_context", {}).get("before_leader_team_id"):
                require(d["lead_context"]["before_leader_team_id"] in f["team_ids"], "此前榜首缺少球队引用")
        elif f["kind"] == "table_row" and d["timing"] == "before_match" and modern and sources is not None:
            from football_simulator.game.calendar import standings_from_matches
            proof_key = next((s["key"] for s in f["sources"] if s["key"].startswith("prematch:")), None)
            require(proof_key is not None, "赛前积分榜缺少对应比赛证据")
            if proof_key not in compared_pre_tables:
                target = book["match:" + proof_key[len("prematch:"):]]["value"]
                table = book["table:" + f["competition_id"]]["value"]
                if meta['exporter_version'] == 'weekly-export/4':
                    before = [book[k]['value'] for k in table['match_keys']
                              if (book[k]['value']['week'] < target['week'] if target['competition'] in ('premier', 'second')
                                  else book[k]['value']['week'] <= target['week'])]
                    require(f['match_ids'] == [target['match_id']], '赛前背景缺少比赛锚点')
                else:
                    before = [book[k]["value"] for k in table["match_keys"] if order(book[k]["value"]) < order(target)]
                rows = standings_from_matches(table["team_ids"], before, previous_rank=table["previous_rank"])
                proof = book[proof_key]["value"]
                require(proof["input_match_keys"] == ["match:" + m["match_id"] for m in before]
                        and proof["rows"] == rows, "赛前积分榜与原始比赛顺序不符")
                compared_pre_tables[proof_key] = {r["team_id"]: (i, r) for i, r in enumerate(rows, 1)}
            rank, row = compared_pre_tables[proof_key][d["team_id"]]
            require((d["rank"], d["points"], d["played"]) == (rank, row["points"], row["played"]), "赛前名次、积分或场数与原始比赛不符")
        elif f["kind"] == "tie_result":
            legs = d["leg_match_ids"]
            require(all(mid in matches for mid in legs), "两回合缺少比赛")
            require(len(set(legs)) == len(legs) and len(legs) <= 2, "两回合场次重复或过多")
            totals = Counter()
            for mid in legs:
                leg = matches[mid]["data"]
                require(set(matches[mid]["team_ids"]) == {d["team_a_id"], d["team_b_id"]}, "两回合对手不符")
                totals[leg["home_team_id"]] += leg["regulation"]["home"] + (leg["extra_time"] or {}).get("home", 0)
                totals[leg["away_team_id"]] += leg["regulation"]["away"] + (leg["extra_time"] or {}).get("away", 0)
            require(totals[d["team_a_id"]] == d["team_a_goals"] and totals[d["team_b_id"]] == d["team_b_goals"], "两回合总比分错误")
            require(d["complete"] == (len(legs) == 2), "两回合完成状态错误")
            require(d["complete"] or d["advancing_team_id"] is None, "首回合提前确认晋级")
            if d["complete"]:
                expected = d["team_a_id"] if d["team_a_goals"] > d["team_b_goals"] else d["team_b_id"] if d["team_b_goals"] > d["team_a_goals"] else None
                if expected is None:
                    require(d["shootout_match_id"] in legs, "总比分平局未提供点球")
                    expected = matches[d["shootout_match_id"]]["data"]["shootout_winner_team_id"]
                require(d["advancing_team_id"] == expected and expected is not None, "总比分晋级队错误")
        elif f["kind"] == "competition_status" and sources is not None:
            state = book["cup-state:" + f["competition_id"]]["value"]["state"]
            raw = {r["value"]["match_id"]: r["value"] for r in book.values() if r["kind"] == "match"}
            require((d["status"], d["next_stage"]) == status_for_team(state, d["team_id"], raw), "赛事资格与签表不符")
        elif f["kind"] == "record":
            require(d["complete_history"] or d["comparison"] == "best_in_available_records", "缺失历史却宣称首次/刷新")
        elif f["kind"] == "swiss_record" and sources is not None:
            state = book["cup-state:starpath"]["value"]["state"]
            require([d["wins"], d["losses"]] == state["records"][d["team_id"]], "瑞士资格战绩错误")
        elif f["kind"] == "streak" and sources is not None:
            proof = book["fact:" + f["fact_id"]]["value"]
            sequence = sorted([book[k]["value"] for k in proof["evidence_keys"] if book[k]["kind"] == "match"], key=order)
            require(sequence and sequence[-1]["match_id"] == d["affected_match_id"], "连续战绩缺少当前比赛")
            good = {"wins": {"W"}, "losses": {"L"}, "unbeaten": {"W", "D"}, "winless": {"L", "D"}}[d["kind"]]
            require(all(field_result(m, d["team_id"]) in good for m in sequence[:-1]), "连续战绩包含相反结果")
            before = len(sequence) - 1
            after = before + 1 if field_result(sequence[-1], d["team_id"]) in good else 0
            require((d["before"], d["after"], d["ended"]) == (before, after, before > 0 and after == 0), "连续战绩长度错误")
            if d["scope"] == "league_all_levels":
                require(all(m["competition"] in ("premier", "second") for m in sequence), "联赛纪录混入杯赛")
            require(d.get("competition_ids") == sorted({m["competition"] for m in sequence}), "连续纪录赛事范围不符")
        elif f["kind"] == "title_clinched" and sources is not None:
            proof = book[d["proof_source_key"]]["value"]
            require(d["proof_method"] == "strict_points_bound", "尚无实现的数学证明方式")
            rows = proof["rows"]; leader = next(r for r in rows if r["team_id"] == d["team_id"])
            require(len(rows) == 20 and all(proof["scheduled_team_counts"].get(r["team_id"]) == 38 for r in rows), "争冠证明缺少完整赛程")
            require(all(leader["points"] > r["points"] + 3 * (38 - r["played"]) for r in rows if r["team_id"] != d["team_id"]), "尚未排除所有竞争者")
    current = [f for f in matches.values() if moment(f["time"]) == cutoff]
    summary = packet["week_summary"]
    if meta["fixture_coverage"] == "full":
        require(summary["completed_matches"] == len(current), "本周场数不符")
        goal_total = sum(sum(f["data"]["regulation"].values()) + sum((f["data"]["extra_time"] or {}).values()) for f in current)
        require(summary["goals_excluding_shootouts"] == goal_total, "总进球错误，可能混入点球")
        require(Counter(f["competition_id"] for f in current) == {r["competition_id"]: r["completed_matches"] for r in summary["competitions"]}, "分赛事场次不符")
    require(bool(current) == (meta["input_validity"] == "ready") or meta["input_validity"] == "insufficient_material", "无比赛包标记错误")
    judgements = _unique(packet["editorial"]["judgements"], "judgement_id")
    topics = _unique(packet["editorial"]["topics"], "topic_id")
    for j in judgements.values():
        require(all(fid in facts for fid in j["basis_fact_ids"]), "评价缺少事实")
    for t in topics.values():
        require(all(fid in facts for fid in t["required_fact_ids"] + t["context_fact_ids"]), "选题缺少事实")
        require(all(jid in judgements for jid in t["judgement_ids"]), "选题缺少评价")
    if modern:
        from football_simulator.press.standings import equivalent_streaks
        require(packet["editorial"].get("streak_groups") == equivalent_streaks(packet["facts"]), "等价连续纪录分组与比赛证据不符")
    return {"facts": len(facts), "matches_this_week": len(current), "topics": len(topics), "source_mode": meta["source_mode"]}


def validate_article_basics(article, packet):
    """The generation gate: usable structure, identity and valid references only.

    Free-text semantics are reviewed separately; a Chinese parsing guess must
    not discard an already paid-for, complete article.
    """
    contract(article, "article-output.schema.json")
    require(article["report_id"] == packet["meta"]["report_id"], "文章报道ID不符")
    facts = {f["fact_id"] for f in packet["facts"]}
    topic_map = {t["topic_id"]: t for t in packet["editorial"]["topics"]}
    topics = set(topic_map)
    require(len(set(article["used_topic_ids"])) == len(article["used_topic_ids"]) and set(article["used_topic_ids"]) <= topics, "文章选题不符")
    refs = article["title_fact_ids"] + article["summary_fact_ids"]
    for section in article["sections"]:
        refs += section["heading_fact_ids"]
        for block in section["blocks"]:
            refs += block["fact_ids"]
    require(set(refs) <= facts, "文章引用未知事实")
    for topic_id in article["used_topic_ids"]:
        require(set(topic_map[topic_id]["required_fact_ids"]) <= set(refs), "已采用选题缺少必需的事实引用")
    return {"structural_and_reference_checks": "passed", "content_review": "pending",
            "free_text_semantics_block_generation": False}


def validate_article(article, packet, sources=None):
    """Optional offline audit retained for diagnosis, not the API success gate."""
    validate_article_basics(article, packet)
    if sources is not None:
        validate_packet(packet, sources)
    # Deliberately narrow prose gates. They catch unsupported score literals and
    # direct status assertions; they do not establish all natural-language truth.
    fact_map = {f["fact_id"]: f for f in packet["facts"]}
    pieces = [(article["title"], article["title_fact_ids"]), (article["summary"], article["summary_fact_ids"])]
    for section in article["sections"]:
        pieces.append((section["heading"], section["heading_fact_ids"]))
        pieces.extend((b["text"], b["fact_ids"]) for b in section["blocks"])
    for text, referenced in pieces:
        cited = [fact_map[i] for i in referenced]
        allowed_scores = set()
        named_scores = {}
        def score_for(a, b, h, aw):
            named_scores.setdefault((a, b), set()).add((h, aw))
            named_scores.setdefault((b, a), set()).add((aw, h))
        for f in cited:
            d = f["data"]
            if f["kind"] == "match_result":
                for score in [d["regulation"], d["extra_time"], d["shootout"]]:
                    if score:
                        allowed_scores.add((score["home"], score["away"]))
                        score_for(d["home_team_id"], d["away_team_id"], score["home"], score["away"])
                allowed_scores.add((d["regulation"]["home"] + (d["extra_time"] or {}).get("home", 0), d["regulation"]["away"] + (d["extra_time"] or {}).get("away", 0)))
                score_for(d["home_team_id"], d["away_team_id"], d["regulation"]["home"] + (d["extra_time"] or {}).get("home", 0), d["regulation"]["away"] + (d["extra_time"] or {}).get("away", 0))
            elif f["kind"] == "tie_result":
                allowed_scores.add((d["team_a_goals"], d["team_b_goals"]))
                score_for(d["team_a_id"], d["team_b_id"], d["team_a_goals"], d["team_b_goals"])
            elif f["kind"] == "swiss_record":
                allowed_scores.add((d["wins"], d["losses"]))
        allowed_scores |= {(a, h) for h, a in list(allowed_scores)}
        require(all((int(a), int(b)) in allowed_scores for a, b in re.findall(r"(?<!\d)(\d+)\s*[–—-]\s*(\d+)(?!\d)", text)), "正文比分缺少对应事实")
        for (a, b), scores in named_scores.items():
            names = packet["entities"]["teams"]
            pattern = re.escape(names[a]["name_en"]) + r"\s*(?:以)?\s*(\d+)\s*[–—-]\s*(\d+)\s*(?:击败|战胜|战平|不敌)?\s*" + re.escape(names[b]["name_en"])
            require(all((int(h), int(aw)) in scores for h, aw in re.findall(pattern, text)), "正文比分球队方向错误")
        for tid, entity in packet["entities"]["teams"].items():
            if re.search(re.escape(entity["name_en"]) + r"(?:已经|已|就此|最终)?(?:被淘汰|出局)", text):
                require(any(f["kind"] == "competition_status" and f["data"]["team_id"] == tid and f["data"]["status"] == "eliminated" for f in cited), "正文把未出局球队写成出局")
    from football_simulator.press.prose import validate_narrative
    return {"structural_and_reference_checks": "passed", **validate_narrative(article, packet, sources)}
