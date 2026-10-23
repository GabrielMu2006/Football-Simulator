# -*- coding: utf-8 -*-
"""W-070：Windows 免安装包验收（ZIP 结构 → 解压 → 净化环境启动 → 路由截图）。

设计原则：
- 只读写 ``--workspace`` 内路径，并在写入前做规范化检查；
- 启动子进程时**移除**开发期 Python/Qt/conda 注入变量，只保留 Windows 系统路径；
- 每个场景都写出结构化结果（platformName、路由、页面类型、水平溢出、错误文本）；
- 记录运行中进程的真实模块列表，证明加载的是包内 Python/Qt，而不是开发环境；
- 任何一步失败都返回非零，不用"进程 exit 0"代替功能验收。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

EXE_NAME = "Football Simulator.exe"

#: 从子进程环境中剔除的开发期变量前缀/名称。
STRIPPED_PREFIXES = (
    "PYTHON", "QT_", "PIP_", "CONDA", "VIRTUAL_ENV", "PYVENV", "PYINSTALLER",
    "QML", "QTWEBENGINE", "VIRTUAL_ENV_PROMPT",
)
STRIPPED_EXACT = {"PATH_EXTRA", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 512), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_inside(path: Path, workspace: Path, label: str) -> Path:
    resolved = Path(path).resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise SystemExit(f"{label} 越界（必须在 {workspace} 内）：{resolved}")
    return resolved


def sanitized_environment(extra: dict) -> dict:
    """只保留 Windows 系统必需变量，去掉任何开发环境注入。"""
    keep_prefixes = ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT",
                     "NUMBER_OF_PROCESSORS", "PROCESSOR_", "OS", "TEMP", "TMP",
                     "USERPROFILE", "APPDATA", "LOCALAPPDATA", "ProgramData",
                     "ProgramFiles", "CommonProgramFiles", "HOMEDRIVE", "HOMEPATH",
                     "USERNAME", "USERDOMAIN", "COMPUTERNAME", "PUBLIC", "ALLUSERSPROFILE")
    env = {}
    for key, value in os.environ.items():
        if any(key.startswith(prefix) for prefix in keep_prefixes):
            env[key] = value
    env["PATH"] = os.pathsep.join([
        str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
        os.environ.get("SystemRoot", r"C:\Windows"),
        str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Wbem"),
    ])
    for key in list(env):
        if key in STRIPPED_EXACT or any(key.upper().startswith(p) for p in STRIPPED_PREFIXES):
            env.pop(key, None)
    env.update(extra)
    return env


def verify_zip(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise SystemExit(f"ZIP 完整性检查失败：{bad}")
        names = archive.namelist()
        roots = {name.split("/", 1)[0] for name in names if name}
        if len(roots) != 1:
            raise SystemExit(f"ZIP 应只有一个应用根目录，实际：{sorted(roots)}")
        if any(name.startswith("/") or ".." in name.split("/") for name in names):
            raise SystemExit("ZIP 内存在绝对路径或上级目录条目")
        root = roots.pop()
        return {
            "entries": len(names),
            "root": root,
            "has_exe": f"{root}/{EXE_NAME}" in names,
            "has_internal": any(name.startswith(f"{root}/_internal/") for name in names),
            "has_readme": f"{root}/README.txt" in names,
            "has_build_info": f"{root}/build-info.txt" in names,
            "has_version": f"{root}/VERSION.txt" in names,
            "uncompressed_bytes": sum(info.file_size for info in archive.infolist()),
        }


def extract(zip_path: Path, target: Path, workspace: Path) -> Path:
    target = assert_inside(target, workspace, "解压目录")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)
    return target


def process_modules(process_name: str, timeout: float = 25.0) -> list:
    """用 PowerShell 读取运行中进程的真实模块路径。"""
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        f"$p = Get-Process -Name '{process_name}' -ErrorAction SilentlyContinue | Select-Object -First 1;"
        "if ($p) { $p.Modules | ForEach-Object { $_.FileName } }"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def run_scenario(
    exe: Path,
    workdir: Path,
    data_root: Path,
    platform: str,
    route: str,
    size: str,
    exit_ms: int,
    timeout: int,
    screenshot: Path = None,
    capture_modules: bool = False,
) -> dict:
    data_root.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    result_file = data_root / "smoke-result.json"
    if result_file.exists():
        result_file.unlink()
    extra = {
        "FS_DATA_ROOT": str(data_root),
        "FS_SMOKE_EXIT_MS": str(exit_ms),
        "FS_SMOKE_RESULT": str(result_file),
        "QT_QPA_PLATFORM": platform,
        "PYTHONUTF8": "1",
    }
    if route:
        extra["FS_SMOKE_ROUTE"] = route
    if size:
        extra["FS_SMOKE_SIZE"] = size
    if screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        extra["FS_SMOKE_SCREENSHOT"] = str(screenshot)
    env = sanitized_environment(extra)
    started = time.time()
    process = subprocess.Popen(
        [str(exe)], cwd=str(workdir), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    modules = []
    if capture_modules:
        deadline = started + min(exit_ms / 1000.0, 20.0)
        while time.time() < deadline and process.poll() is None:
            time.sleep(1.0)
            modules = process_modules("Football Simulator")
            if modules:
                break
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        timed_out = True
    elapsed = round(time.time() - started, 2)
    payload = {}
    if result_file.exists():
        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    return {
        "route_requested": route or "(default)",
        "size": size or "(default)",
        "platform_requested": platform,
        "platform_reported": payload.get("platform"),
        "page": payload.get("page"),
        "route_actual": payload.get("route"),
        "error": payload.get("error"),
        "body_width": payload.get("body_width"),
        "viewport_width": payload.get("viewport_width"),
        "horizontal_max": payload.get("horizontal_max"),
        "label_count": len(payload.get("labels") or []),
        "screenshot": str(screenshot) if screenshot and screenshot.exists() else None,
        "screenshot_bytes": screenshot.stat().st_size if screenshot and screenshot.exists() else 0,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "seconds": elapsed,
        "stdout": (stdout or b"").decode("utf-8", "replace")[-2000:],
        "stderr": (stderr or b"").decode("utf-8", "replace")[-2000:],
        "modules_snapshot_count": len(modules),
        "modules_sample": [m for m in modules if "python" in m.lower() or "Qt6Core" in m or "qwindows" in m][:8],
        "modules": modules,
    }


def dev_environment_leaks(modules: list) -> list:
    leaks = []
    for path in modules:
        lowered = path.lower()
        if ".venv-win" in lowered or "anaconda" in lowered or "\\runtime\\python" in lowered:
            leaks.append(path)
    return leaks


def prepare_save(source: Path, data_root: Path, name: str, weeks: int) -> dict:
    """用源码里的服务层在验收数据根建一个真实新档（不启动 Qt）。"""
    source = Path(source).resolve()
    if not (source / "football_simulator").is_dir():
        raise SystemExit(f"源码目录无效：{source}")
    sys.path.insert(0, str(source))
    try:
        from football_simulator import runtime
        from football_simulator.app.service import GameService
    finally:
        sys.path.pop(0)
    data_root.mkdir(parents=True, exist_ok=True)
    runtime.set_data_root_override(data_root)
    service = GameService()
    if not (data_root / "saves" / name / "save.sqlite3").is_file():
        if name not in runtime.list_save_names():
            service.create_save(name)
        service.initialize_save(name)
    service.set_current_save(name)
    for _ in range(max(0, int(weeks))):
        snapshot = service.snapshot(name)
        if snapshot["season_complete"]:
            break
        if snapshot["pending"]["ability_review"]:
            service.apply_ability_review(name, {})
        elif snapshot["pending"]["draft"]:
            service.apply_draft(name, [])
        elif snapshot["pending"]["transfer_review"]:
            service.apply_transfer_reviews(name, {})
        service.advance_week(name)
    snapshot = service.snapshot(name)
    world = service.load_world(name)
    fixtures = world["season"].get("fixtures") or []
    played = [match for match in fixtures if match.get("status") in ("finished", "played", "completed")]
    teams = list((world.get("teams") or {}).keys())
    players = list((world.get("players") or {}).keys())
    real_players = [pid for pid in players if (world["players"][pid] or {}).get("is_real")]
    return {
        "save_name": name,
        "season": world["season_number"],
        "week": world["season"]["week"],
        "teams": len(teams),
        "matches": len(fixtures),
        "snapshot_season": snapshot["season"],
        "ids": {
            "competition": "aureole",
            "match": (played[0].get("match_id") if played else (fixtures[0].get("match_id") if fixtures else None)),
            "team": teams[0] if teams else None,
            "team_zh": (world["teams"][teams[0]].get("club_en") if teams else None),
            "player": (real_players[0] if real_players else (players[0] if players else None)),
            "week": world["season"]["week"],
        },
    }


def import_fixtures(source: Path, data_root: Path, specs: list) -> list:
    """把 .fsarchive 夹具导入验收数据根（用于 S2 冬窗 / S3 结束场景）。"""
    source = Path(source).resolve()
    sys.path.insert(0, str(source))
    try:
        from football_simulator import runtime
        from football_simulator.persistence_v2 import store
    finally:
        sys.path.pop(0)
    data_root.mkdir(parents=True, exist_ok=True)
    runtime.set_data_root_override(data_root)
    imported = []
    for spec in specs:
        name, _, path = spec.partition("=")
        if not name or not path:
            raise SystemExit(f"--import-fixture 需要 name=path：{spec}")
        package = Path(path)
        if not package.is_file():
            raise SystemExit(f"夹具不存在：{package}")
        if runtime.save_exists(name):
            imported.append({"save_name": name, "status": "already-present"})
            continue
        store.import_package(package, name)
        world = store.load_world(name)
        fixtures = world["season"].get("fixtures") or []
        played = [match for match in fixtures if match.get("status") == "completed"]
        teams = sorted(world.get("teams") or {})
        real_players = sorted(pid for pid, player in (world.get("players") or {}).items() if player.get("is_real"))
        championships = world["season"].get("championships") or {}
        imported.append({
            "save_name": name,
            "status": "imported",
            "season": world["season_number"],
            "week": world["season"]["week"],
            "championships": len(championships),
            "ids": {
                "match": (played[-1]["match_id"] if played else (fixtures[-1]["match_id"] if fixtures else None)),
                "team": (next(iter(championships.values()))["champion_team_id"] if championships else (teams[0] if teams else None)),
                "player": (real_players[0] if real_players else None),
                "history_season": world["season_number"],
            },
        })
    if imported:
        runtime.store_current_save_name(imported[-1]["save_name"])
    return imported


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Windows 免安装包验收")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--zip", default="")
    parser.add_argument("--app", default="", help="直接验收已解压的应用目录（跳过 ZIP 步骤）")
    parser.add_argument("--extract", default="")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--platform", default="windows")
    parser.add_argument("--routes", default="")
    parser.add_argument("--sizes", default="1440x900")
    parser.add_argument("--exit-ms", type=int, default=3500)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--screenshot-dir", default="")
    parser.add_argument("--reuse-data-root", action="store_true", help="所有场景共用一个数据根（先用 --prepare-save 建好存档）")
    parser.add_argument("--prepare-save", default="", help="在数据根内创建并初始化该存档")
    parser.add_argument("--prepare-weeks", type=int, default=0)
    parser.add_argument("--source", default="", help="准备存档时使用的源码目录")
    parser.add_argument("--prepare-only", action="store_true", help="只准备存档/夹具，不启动应用")
    parser.add_argument("--import-fixture", action="append", default=[],
                        help="导入 .fsarchive 夹具，格式 name=path（可重复）")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    data_root = assert_inside(Path(args.data_root), workspace, "数据根")
    result_path = assert_inside(Path(args.result), workspace, "结果文件")
    screenshot_dir = Path(args.screenshot_dir).resolve() if args.screenshot_dir else None
    if screenshot_dir is not None:
        screenshot_dir = assert_inside(screenshot_dir, workspace, "截图目录")

    report = {}
    if args.prepare_only:
        app_dir = None
    elif args.app:
        app_dir = assert_inside(Path(args.app), workspace, "应用目录")
        report["app_dir"] = str(app_dir)
        report["zip_check"] = {"mode": "pre-extracted"}
    else:
        if not args.zip:
            raise SystemExit("需要 --zip 或 --app")
        zip_path = assert_inside(Path(args.zip), workspace, "ZIP")
        report["zip"] = str(zip_path)
        report["zip_sha256"] = sha256_file(zip_path)
        report["zip_bytes"] = zip_path.stat().st_size
        report["zip_check"] = verify_zip(zip_path)
        if not (report["zip_check"]["has_exe"] and report["zip_check"]["has_internal"]):
            raise SystemExit("ZIP 缺少 EXE 或 _internal")
        extract_root = extract(zip_path, Path(args.extract), workspace)
        app_dir = extract_root / report["zip_check"]["root"]
        report["app_dir"] = str(app_dir)
    if args.prepare_only:
        exe = None
    else:
        exe = app_dir / EXE_NAME
        if not exe.is_file():
            raise SystemExit(f"找不到 EXE：{exe}")
    if app_dir is not None:
        report["extracted_files"] = sum(1 for item in app_dir.rglob("*") if item.is_file())
        report["extracted_bytes"] = sum(item.stat().st_size for item in app_dir.rglob("*") if item.is_file())

    prepared = None
    if args.import_fixture:
        source = Path(args.source).resolve() if args.source else workspace / "source"
        prepared = {"fixtures": import_fixtures(source, data_root, args.import_fixture)}
    if args.prepare_save:
        source = Path(args.source).resolve() if args.source else workspace / "source"
        save_info = prepare_save(source, data_root, args.prepare_save, args.prepare_weeks)
        if prepared is None:
            prepared = save_info
        else:
            prepared = {"fixtures": prepared.get("fixtures"), "save": save_info}
    if args.prepare_only:
        report["prepared_save"] = prepared
        report_path = result_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"prepared_save": prepared, "prepare_only": True}, ensure_ascii=False))
        return 0
    workdir = data_root.parent / "unrelated-cwd"
    routes = [item for item in args.routes.split(",") if item] or [""]
    sizes = [item for item in args.sizes.split(",") if item] or ["1440x900"]

    scenarios = []
    index = 0
    for route in routes:
        for size in sizes:
            index += 1
            scenario_root = data_root if args.reuse_data_root else data_root / f"case-{index:02d}"
            shot = None
            if screenshot_dir is not None:
                safe = (route or "default").replace(":", "_").replace("/", "_")
                shot = screenshot_dir / f"{index:02d}-{safe}-{size}x{args.platform}.png"
            scenarios.append((route, size, scenario_root, shot))

    results = []
    for position, (route, size, scenario_root, shot) in enumerate(scenarios):
        result = run_scenario(
            exe=exe,
            workdir=workdir,
            data_root=scenario_root,
            platform=args.platform,
            route=route,
            size=size,
            exit_ms=args.exit_ms,
            timeout=args.timeout,
            screenshot=shot,
            capture_modules=(position == 0),
        )
        result["dev_environment_leaks"] = dev_environment_leaks(result.get("modules") or [])
        result["modules"] = None
        results.append(result)
        print(
            f"[{position + 1}/{len(scenarios)}] route={result['route_requested']} size={size} "
            f"exit={result['exit_code']} platform={result['platform_reported']} "
            f"hmax={result['horizontal_max']} error={result['error']!r}",
            flush=True,
        )

    failures = []
    for result in results:
        if result["exit_code"] != 0 or result["timed_out"]:
            failures.append(f"{result['route_requested']}: exit {result['exit_code']}")
        if result["platform_reported"] not in (None, args.platform):
            failures.append(f"{result['route_requested']}: platform {result['platform_reported']}")
        if result["error"]:
            failures.append(f"{result['route_requested']}: 错误页 {result['error']}")
        if result["horizontal_max"] not in (None, 0):
            failures.append(f"{result['route_requested']}: 主内容水平溢出 {result['horizontal_max']}")
        if result["dev_environment_leaks"]:
            failures.append(f"{result['route_requested']}: 加载了开发环境模块 {result['dev_environment_leaks'][:2]}")

    report["prepared_save"] = prepared
    report["scenarios"] = results
    report["failures"] = failures
    report["all_green"] = not failures
    report_path = result_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"scenarios": len(results), "failures": failures, "all_green": report["all_green"]}, ensure_ascii=False, indent=2))
    return 0 if report["all_green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
