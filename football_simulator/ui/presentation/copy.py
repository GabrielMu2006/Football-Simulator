"""GFE-010: player-facing copy that keeps rule facts out of the main body.

These strings replace implementation/verification sentences. Definitions that
matter stay reachable through a rule/statistics entry (``RULE_NOTES``), never as
long paragraphs under the page title.
"""

from __future__ import annotations

HOME_SUBTITLE = "最近一轮赛况、积分变化与下一场"

RULE_NOTES = {
    "standings_premier": "排序：积分 → 净胜球 → 进球 → 相互战绩 → 上季排名。第 18–20 名降级。",
    "standings_second": "第 1–2 名直接升级，第 3–6 名进入升级附加赛。",
    "calendar": "赛季共 52 周；冬窗与夏窗为休赛周，第 49 周结束全部赛事。",
    "cup_unopened": "本赛事尚未开始。",
    "history_stats": "赛季统计来自已保存的逐场记录，按当时结果累计。",
    "honors_grant": "冠军荣誉按夺冠当刻的注册阵容计入随队冠军。",
    "player_stats": "赛季统计含加时进球；点球大战只决定比赛结果，不计个人进球或助攻。",
    "transfer_review": "勾选代表本批决定，可随时修改，提交后生效。",
    "import_export": "存档包包含数据库、配置与校验清单；导入会先校验再安装。",
}

EMPTY = {
    "no_week_matches": ("本周没有比赛", "冬窗、夏窗或休赛周。可查看赛季日历确认下一场。"),
    "no_saves": ("还没有存档", "开始一个新世界，或导入已有的存档包。"),
    "no_transfers": ("当前没有待处理提案", "下一个转会窗口开放时，提案会出现在这里。"),
    "no_watch": ("还没有关注", "在球队或球员页面加入关注后，这里会汇总他们的近况。"),
    "no_awards": ("本赛季还没有颁奖", "奖项在赛季结算后正式公布。"),
}


def label_for_fact(kind: str, route: str, params: dict) -> str:
    """Action label that names what the reader will actually see."""
    if kind == "table":
        return "查看积分榜"
    if kind == "streak" or kind == "comeback" or kind == "goals":
        return "查看战报"
    if kind == "champion":
        return "查看赛事"
    if kind == "qualification":
        return "查看签表"
    return label_for_route(route)


def label_for_route(route: str) -> str:
    """Action labels that name the real destination."""
    return {
        "match": "查看战报",
        "competition": "查看赛事",
        "team": "查看球队",
        "player": "查看球员",
        "weekly": "查看周报",
        "season": "查看赛历",
        "honors": "查看荣誉馆",
        "records": "查看纪录",
        "history": "查看历史",
        "transfers": "查看转会",
        "draft": "查看选秀",
        "saves": "查看存档",
    }.get(route, "查看详情")
