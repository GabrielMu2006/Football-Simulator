"""Run seeded V0.3.1 seasons in a new, explicitly supplied isolated data root."""
import argparse
from collections import Counter
import json
import random
import shutil
import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.game.awards_v031 import EVP_COUNTS
from football_simulator.persistence_v2 import store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--seasons", type=int, default=3)
    parser.add_argument("--seed", type=int, default=314159)
    args = parser.parse_args()
    root = args.data_root.resolve()
    if root.exists() or root == ROOT or ROOT in root.parents or root == runtime.user_data_root():
        raise SystemExit("Data root must be a new directory outside the project and user data root")
    root.mkdir(parents=True)
    config = root / runtime.SHARED_CONFIG_FILE_NAME
    shutil.copy2(ROOT / runtime.SHARED_CONFIG_FILE_NAME, config)
    runtime.set_data_root_override(root)
    runtime.set_config_path_override(config)
    rng = random.Random(args.seed)
    service = GameService(rng_factory=lambda: rng, edition_archives=True, honor_profile=rules.HONOR_PROFILE)
    name = "V031Acceptance"
    service.create_save(name)
    service.initialize_save(name)
    receipt = []
    for season in range(1, args.seasons + 1):
        for week in range(1, 53):
            service.advance_week(name)
            if season == 1 and week in (42, 49):
                from football_simulator.press import features
                from football_simulator.press.local_templates.engine import freeze_plan, render
                from football_simulator.press.validation import validate_article_basics
                with sqlite3.connect(store.database_path(name)) as conn:
                    conn.row_factory = sqlite3.Row
                    source = features.capture_snapshot(conn, week)
                packet = features.competition_packet(source, rules.PREMIER) if week == 42 else features.top20_packet(source)
                plan = freeze_plan(packet)
                article, _ = render(packet, plan)
                validate_article_basics(article, packet)
                text = json.dumps(article, ensure_ascii=False)
                if week == 42:
                    winners = packet["facts"][0]["data"]["awards"]["赛事EVP"]
                    assert len(winners) == EVP_COUNTS[rules.PREMIER]
                    assert all(row["player"] in text for row in winners)
                    assert "EVP" in text
                (root / ("competition-report.json" if week == 42 else "top20-report.json")).write_text(text)
            for _ in range(10):
                world = store.load_world(name, readonly=True)
                pending = world["season"]["pending"]
                if pending.get("ability_review"):
                    service.apply_ability_review(name, {r["player_id"]: True for r in pending["ability_review"]})
                elif pending.get("transfer_review"):
                    service.apply_transfer_reviews(name, {r["trade_id"]: True for r in pending["transfer_review"]})
                elif pending.get("draft", {}).get("status") == "awaiting_input":
                    service.apply_draft(name, None)
                else:
                    break
            if week % 10 == 0:
                print(f"season {season}, week {week}", flush=True)
        world = store.load_world(name, readonly=True)
        top = world["season"]["awards"]["top20"]
        assert len(top) == 20
        summary = {"season": season, "top20_positions": dict(Counter(r["position"] for r in top)), "competitions": {}}
        for competition, limit in EVP_COUNTS.items():
            edition = world.get("edition_archives", {}).get(f"{season}:{competition}")
            if not edition:
                assert competition != rules.PREMIER and not rules.cup_active(competition, season)
                continue
            awards = edition["awards"]
            mvp, evp = awards["mvp"], awards["evp"]
            assert len(mvp) <= 1 and len(evp) <= limit
            assert not set(r["player_id"] for r in mvp) & set(r["player_id"] for r in evp)
            if mvp:
                champion = (edition.get("championship") or {}).get("champion_team_id")
                runner = edition["state"].get("runner_up") if competition != rules.PREMIER else edition["tables"]["premier"][1]["team_id"]
                winner = mvp[0]
                assert winner["team_id"] in (champion, runner)
                assert edition["player_teams"][winner["player_id"]] == winner["team_id"]
                assert winner["team_id"] in winner["teams"]
            summary["competitions"][competition] = {"mvp": len(mvp), "evp": len(evp), "slots": limit}
        secondary = world["season"]["awards"]["competitions"].get(rules.SECOND, {})
        assert not secondary.get("mvp") and not secondary.get("evp")
        receipt.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if season < args.seasons:
            service.start_next_season(name)
    (root / "v031-acceptance.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(f"save={name} data_root={root}", flush=True)


if __name__ == "__main__":
    main()
