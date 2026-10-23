"""崩溃隔离的全量测试运行器（T-000）。

历史问题：所有测试共用一个 Python/Qt 进程时，会在后续模块构造 QApplication
时触发 exit139。单模块运行可稳定完成，因此正式全量验证按模块启动独立子进程：
- 每个模块都有全新的 Qt、SQLite 与测试夹具；
- 任何模块崩溃只影响该模块，报告为 crash（附 faulthandler 日志）；
- 汇总通过/失败/跳过/崩溃数，退出码非零表示不是全绿。

跨平台：
- 子进程强制 UTF-8 输入输出，避免 Windows 代码页把中文断言信息变成乱码；
- 崩溃识别同时覆盖 Unix 的 SIGSEGV(-11/139) 与 Windows NTSTATUS
  (0xC0000005 等)，不把原生崩溃当成普通 assertion failure。

用法：
    QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python tests/run_suite.py \
        --json artifacts/tests/results.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = PROJECT_ROOT / "tests"
RAN_RE = re.compile(r"Ran (\d+) tests? in ([0-9.]+)s")
RESULT_RE = re.compile(r"^(OK|FAILED)(?: \((.*)\))?$", re.MULTILINE)
SKIP_RE = re.compile(r"skipped[= ](\d+)")
SKIPPED_REASON_RE = re.compile(r"^(\S+) \(.*\) \.\.\. skipped ['\"](.*)['\"]$", re.MULTILINE)

#: Windows 原生崩溃退出码（NTSTATUS），Python 里可能是无符号或补码形式。
NTSTATUS_CRASHES = {
    0xC0000005: "access violation",
    0xC000001D: "illegal instruction",
    0xC00000FD: "stack overflow",
    0xC0000374: "heap corruption",
    0xC0000409: "fail-fast / stack buffer overrun",
    0xC0000602: "fail-fast exception",
    0xC000013A: "control-C termination",
}


def discover_modules() -> list:
    return [f"tests.{path.stem}" for path in sorted(TESTS_DIR.glob("test_*.py"))]


def _display_path(path: Path) -> str:
    """日志可能被要求在项目外（Windows 工作区的 artifacts 目录）。"""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _normalize_exit_code(code: int) -> int:
    """把 Python 返回的退出码还原成无符号 32 位，便于匹配 NTSTATUS。"""
    return code & 0xFFFFFFFF if code is not None else 0


def _crash_detail(code: int, combined: str) -> str:
    unsigned = _normalize_exit_code(code)
    if unsigned in NTSTATUS_CRASHES:
        return f"native crash 0x{unsigned:08X} ({NTSTATUS_CRASHES[unsigned]})"
    if code in (139, -11):
        return "segmentation fault (SIGSEGV)"
    if code in (134, -6):
        return "abort (SIGABRT)"
    if "Fatal Python error" in combined:
        return "Fatal Python error"
    if "Windows fatal exception" in combined:
        return "Windows fatal exception"
    return ""


def run_module(module: str, timeout: int, log_dir: Path) -> dict:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    try:
        process = subprocess.run(
            [sys.executable, "-m", "unittest", module, "-v"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stdout, stderr, code = process.stdout, process.stderr, process.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = -999
    elapsed = round(time.time() - started, 2)
    log_path = log_dir / f"{module}.log"
    log_path.write_text(stdout + "\n--- STDERR ---\n" + stderr, encoding="utf-8")
    combined = stdout + "\n" + stderr
    ran = RAN_RE.search(combined)
    tests = int(ran.group(1)) if ran else 0
    result_match = RESULT_RE.search(combined)
    skipped = 0
    for match in SKIP_RE.finditer(combined):
        skipped = max(skipped, int(match.group(1)))
    skip_reasons = [f"{name}: {reason}" for name, reason in SKIPPED_REASON_RE.findall(combined)]
    status = "ok"
    detail = ""
    crash = ""
    if code == -999:
        status = "timeout"
        detail = f"{timeout}s 超时"
    elif code != 0:
        crash = _crash_detail(code, combined)
        if crash:
            status = "crash"
            detail = crash
        elif result_match:
            status = "failed"
            detail = result_match.group(2) or "test failures"
        else:
            status = "error"
            detail = f"exit {code}"
    elif not ran:
        status = "error"
        detail = "no test summary"
    return {
        "module": module,
        "status": status,
        "tests": tests,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "seconds": elapsed,
        "returncode": code,
        "detail": detail,
        "log": _display_path(log_path),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="崩溃隔离的全量测试运行器")
    parser.add_argument("--json", default="", help="汇总 JSON 输出路径")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--only", default="", help="只运行模块名包含该字符串的测试")
    parser.add_argument("--skip", default="", help="跳过模块名包含该字符串的测试")
    parser.add_argument(
        "--log-dir",
        default="artifacts/tests/logs",
        help="独立模块日志目录（默认 artifacts/tests/logs）",
    )
    args = parser.parse_args(argv)
    log_dir = Path(args.log_dir)
    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    modules = discover_modules()
    if args.only:
        modules = [module for module in modules if args.only in module]
    if args.skip:
        modules = [module for module in modules if args.skip not in module]
    results = []
    total_started = time.time()
    for module in modules:
        print(f"=== {module}", flush=True)
        result = run_module(module, args.timeout, log_dir)
        print(
            f"    {result['status']:<8} {result['tests']:>4} tests "
            f"{result['skipped']:>3} skipped {result['seconds']:>7.2f}s "
            f"{result['detail']}",
            flush=True,
        )
        results.append(result)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version,
        "platform": sys.platform,
        "isolation": "one subprocess per test module",
        "modules": len(results),
        "tests": sum(item["tests"] for item in results),
        "skipped": sum(item["skipped"] for item in results),
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "errors": sum(1 for item in results if item["status"] == "error"),
        "crashes": sum(1 for item in results if item["status"] == "crash"),
        "timeouts": sum(1 for item in results if item["status"] == "timeout"),
        "seconds": round(time.time() - total_started, 2),
        "results": results,
        "skip_reasons": [reason for item in results for reason in item["skip_reasons"]],
        "all_green": all(item["status"] == "ok" for item in results),
    }
    print(json.dumps({key: summary[key] for key in ("modules", "tests", "skipped", "ok", "failed", "errors", "crashes", "timeouts", "seconds", "all_green")}, ensure_ascii=False, indent=2))
    if args.json:
        output = Path(args.json)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if summary["all_green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
