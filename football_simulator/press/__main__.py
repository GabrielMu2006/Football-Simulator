"""Offline-only CLI. A sandbox marker prevents operating on existing game data."""
import argparse
import json
from pathlib import Path
import random

from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.press import PressError
from football_simulator.press import repository
from football_simulator.press.editorial import request_preview, render_markdown
from football_simulator.press.validation import validate_packet

MARKER = ".press-offline-sandbox.json"
PROJECT = Path(__file__).resolve().parents[2]


def sandbox(path, create=False):
    path = Path(path).resolve()
    protected = [PROJECT / n for n in ("saves", "backups", "trash", "release")]
    protected += [runtime.user_data_root(), PROJECT]
    if any(path == p or (p != PROJECT and p in path.parents) for p in protected):
        raise PressError("离线工具拒绝使用正式游戏或项目根作为测试数据根。")
    if create:
        if path.exists() and any(path.iterdir()):
            raise PressError("demo 需要新的空数据根。")
        path.mkdir(parents=True, exist_ok=True)
        (path / MARKER).write_text(json.dumps({"kind": "press-offline-sandbox", "version": 1}), encoding="utf-8")
    if not (path / MARKER).is_file():
        raise PressError("数据根没有离线沙箱标识；不操作现有游戏存档。")
    runtime.set_data_root_override(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo"); demo.add_argument("--seed", type=int, default=423); demo.add_argument("--weeks", type=int, default=3); demo.add_argument("--output", type=Path, required=True)
    for name in ("enable", "list", "export"):
        p = sub.add_parser(name); p.add_argument("save")
        if name == "export":
            p.add_argument("season", type=int); p.add_argument("week", type=int); p.add_argument("--output", type=Path, required=True)
    for name in ("validate", "preview", "render"):
        p = sub.add_parser(name); p.add_argument("packet", type=Path); p.add_argument("--sources", type=Path)
        if name != "validate": p.add_argument("--output", type=Path, required=True)
        if name == "render": p.add_argument("--article", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("demo", "enable", "list", "export"):
            if args.data_root is None:
                raise PressError("必须显式指定 --data-root。")
            sandbox(args.data_root, create=args.command == "demo")
        if args.command == "demo":
            if not 1 <= args.weeks <= 23:
                raise PressError("首轮 demo 限制为1–23周；完整窗口/跨季在隔离测试验证。")
            service = GameService(rng_factory=lambda: random.Random(args.seed), edition_archives=True)
            service.create_save("PressDemo"); service.initialize_save("PressDemo")
            repository.enable("PressDemo")
            for _ in range(args.weeks):
                result = service.advance_week("PressDemo")
            output = repository.export_week("PressDemo", 1, result["week"], args.output)
            material = repository.load_week("PressDemo", 1, result["week"])
            (output / "request-preview.json").write_text(json.dumps(request_preview(material["packet"], material["sources"]), ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"export": str(output), "source": "isolated_new_save", "api_calls": 0}, ensure_ascii=False))
        elif args.command == "enable":
            print(json.dumps(repository.enable(args.save), ensure_ascii=False))
        elif args.command == "list":
            print(json.dumps(repository.list_weeks(args.save), ensure_ascii=False))
        elif args.command == "export":
            print(repository.export_week(args.save, args.season, args.week, args.output))
        else:
            packet = json.loads(args.packet.read_text(encoding="utf-8"))
            sources = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources else None
            checks = validate_packet(packet, sources)
            if args.command == "validate":
                print(json.dumps(checks, ensure_ascii=False))
            else:
                repository.protect_output(args.output)
                if args.command == "preview":
                    text = json.dumps(request_preview(packet, sources), ensure_ascii=False, indent=2)
                else:
                    text, _ = render_markdown(json.loads(args.article.read_text(encoding="utf-8")), packet, sources=sources)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("x", encoding="utf-8") as f:
                    f.write(text)
                print(args.output)
        return 0
    except (PressError, FileExistsError, FileNotFoundError, json.JSONDecodeError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
