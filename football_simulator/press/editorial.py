"""Prepare a request and readable offline samples. This module never sends it."""
from __future__ import annotations

import html
import json
from pathlib import Path

from football_simulator.press import PressError
from football_simulator.press.validation import validate_article, validate_packet
from football_simulator.press.briefing import writing_view


def request_preview(packet, sources=None, prompt_root=None):
    validate_packet(packet, sources)
    if packet["meta"]["input_validity"] != "ready":
        raise PressError("本周没有可生成赛事长文的比赛材料。")
    prompt_root = Path(prompt_root) if prompt_root else Path(__file__).resolve().parents[2] / "docs/press/prompts"
    system = (prompt_root / "editorial-system.md").read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
    user = (prompt_root / "weekly-user.md").read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
    if user.count("{{WEEKLY_PACKAGE_JSON}}") != 1:
        raise PressError("周报模板必须恰好有一个输入占位符。")
    view = writing_view(packet)
    encoded = json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    user = user.replace("{{WEEKLY_PACKAGE_JSON}}", encoded)
    return {"offline_preview": True, "prompt_versions": ["editorial-system-draft-5", "weekly-user-draft-5"],
            "writing_view": {"version": view['writing_view_version'], "facts": len(view['facts']),
                             "bytes": len(encoded.encode('utf-8')), "proofs_kept_locally": True}, "request": {
        "model": "deepseek-flash", "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}}


def render_markdown(article, packet, *, origin="manual_or_fake_offline_sample", real_api=False, reading_status="pending", sources=None):
    checks = validate_article(article, packet, sources)
    return format_markdown(article, origin=origin, real_api=real_api, reading_status=reading_status), checks


def format_markdown(article, *, origin, real_api=False, reading_status="pending"):
    """Pure formatting for a hash-verified saved success; no new-policy rejudging."""
    esc = html.escape
    note = "离线验读资料，非 DeepSeek 实测。"
    if real_api:
        note = {"pending": "DeepSeek API 返回稿；结构检查通过，事实语义与文风仍待独立验读。",
                "accepted": "DeepSeek API 返回稿；本地独立验读记录为通过。",
                "rejected": "DeepSeek API 返回稿；本地独立验读已拒收，错误与来源见回执。"}[reading_status]
    elif reading_status == "accepted":
        note += "本地验读记录：通过。"
    elif reading_status == "rejected":
        note += "本地验读记录：拒收，错误与来源见回执。"
    lines = [f"# {esc(article['title'])}", "", f"来源：{esc(origin)}；{note}", "", esc(article["summary"]), ""]
    for section in article["sections"]:
        lines += ["## " + esc(section["heading"]), ""]
        for block in section["blocks"]:
            if block["label"]:
                lines += ["**" + esc(block["label"]) + "**", ""]
            lines += [esc(block["text"]), ""]
    return "\n".join(lines)
