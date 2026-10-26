"""Source-only weekly API pilot. Separate ledger; never opens a game save."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import warnings

from football_simulator.press import PressError, EXPORTER_VERSION
from football_simulator.press.editorial import request_preview, format_markdown
from football_simulator.press import provider
from football_simulator.press.repository import canonical, digest, protect_output
from football_simulator.press.validation import validate_article_basics

PROJECT = Path(__file__).resolve().parents[2]
PILOT_ROOT = PROJECT / "artifacts/press-pilot"
MARKER = ".press-pilot.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def local_credential(*, prompt=False):
    if prompt:
        if not sys.stdin.isatty():
            raise PressError("密钥输入需要本机交互终端；不要通过聊天或命令参数发送密钥。")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = getpass.getpass("DeepSeek API key（不回显、不保存）：")
        except getpass.GetPassWarning:
            raise PressError("终端无法关闭回显，已停止输入；请使用正常的本机Terminal。") from None
    else:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
    provider.validate_key(key)
    return key


def reading_checks(article, packet):
    text = "\n".join(block["text"] for section in article["sections"] for block in section["blocks"])
    return {"han_characters_body": len(re.findall(r"[\u3400-\u9fff]", text)),
            "weekly_target_han_characters": [600, 1000], "length_is_advisory": True,
            "independent_reading": "pending", "facts_available": len(packet["facts"]),
            "checklist": ["逐项核对比分、晋级与两回合", "连续纪录及老东家范围正确", "因果、评价与虚构声音有边界",
                          "有主线、无重复套话；资料稀少时不凑字数"]}


class Pilot:
    """One validated success per report_id in this local pilot ledger.

    The process lock survives slow requests and is released by the OS on crash.
    A leftover 'sending' attempt has unknown billing outcome, never auto-retried.
    This is not the later in-game job queue / portable archive integration.
    """

    def __init__(self, root=PILOT_ROOT, *, transport=None):
        self.root = Path(root).resolve()
        protect_output(self.root)
        if self.root == PROJECT or self.root in PROJECT.parents:
            raise PressError("联调账本不能使用项目根或其父目录。")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = self.root / MARKER
        if not marker.exists():
            if any(self.root.iterdir()):
                raise PressError("联调账本必须使用新的空目录或已有专用账本。")
            with marker.open("x", encoding="utf-8") as handle:
                handle.write('{"kind":"press-api-pilot","version":1}\n')
        if provider.strict_json(marker.read_text()) != {"kind": "press-api-pilot", "version": 1}:
            raise PressError("联调账本标识不符。")
        self.transport = transport
        self.origin = "deepseek_api" if transport is None else "simulated_provider"
        self.database = self.root / "pilot.sqlite3"
        if self.database.is_symlink():
            raise PressError("账本数据库不能使用符号链接。")
        # Only this dedicated ledger is writable; there is no store/GameService import.
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    report_id TEXT PRIMARY KEY, packet_sha256 TEXT NOT NULL, sources_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL, attempt INTEGER NOT NULL, article_json TEXT, receipt_json TEXT, packet_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    report_id TEXT NOT NULL, number INTEGER NOT NULL, status TEXT NOT NULL,
                    started TEXT NOT NULL, finished TEXT, request_json TEXT NOT NULL, detail_json TEXT,
                    PRIMARY KEY(report_id,number));
            """)
        self.database.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.database, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def locked(self, report_id):
        path = self.root / (digest(report_id) + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PressError("该周报已有调用正在进行，请等待；未发起第二次请求。") from None
            yield
        finally:
            os.close(fd)

    def generate(self, packet, sources, credential, *, retry_failed=False, expected_request_sha256=None):
        if not isinstance(sources, dict):
            raise PressError("真实模型联调必须附带冻结的来源包。")
        preview = request_preview(packet, sources)
        request = provider.build_request(preview["request"])
        report_id = packet["meta"]["report_id"]
        packet_hash, sources_hash = digest(packet), digest(sources)
        with self.locked(report_id), self.connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE report_id=?", (report_id,)).fetchone()
            if row:
                if (row["packet_sha256"], row["sources_sha256"]) != (packet_hash, sources_hash):
                    raise PressError("同一报道标识的事实指纹已变；拒绝覆盖或重新付费生成。")
                if row["status"] == "succeeded" or (row["status"] == "failed_review" and not retry_failed):
                    saved, receipt = json.loads(row["article_json"]), json.loads(row["receipt_json"])
                    if (digest(saved), receipt.get("packet_sha256"), receipt.get("sources_sha256"), receipt.get("report_id")) != (receipt.get("article_sha256"), packet_hash, sources_hash, report_id):
                        raise PressError("成功文章或回执指纹不符；停止读取，不重新付费生成。")
                    return self.export_success(saved, packet, receipt, cached=True)
                # Older prose rules could reject a complete response. Reuse it
                # locally before credentials/retry handling; never pay for it twice.
                recovered = self._recover_complete_return(conn, row, packet, sources)
                if recovered is not None:
                    return recovered
                if not retry_failed:
                    raise PressError("已有失败或中断记录。检查原因后显式使用 --retry-failed；不会自动重试，先前调用可能已计费。")
                if row["status"] == "sending":
                    conn.execute("UPDATE attempts SET status='interrupted',finished=?,detail_json=? WHERE report_id=? AND number=?",
                                 (now(), canonical({"error_code": "process_interrupted_outcome_unknown"}), report_id, row["attempt"]))
            if packet["meta"]["exporter_version"] != EXPORTER_VERSION:
                raise PressError("旧材料仅可读取已有成功稿；新调用需使用带完整赛季证据的 weekly-export/3 独立测试材料。")
            if expected_request_sha256 is not None:
                if not isinstance(expected_request_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_request_sha256):
                    raise PressError("预期请求指纹格式无效；未读取密钥或发送请求。")
                if digest(request) != expected_request_sha256:
                    raise PressError("写作请求与准备时的指纹不同；请先离线检查模板与配置，未读取密钥或发送请求。")
            # Cache checks precede credentials. Missing credentials create no paid attempt.
            key = credential()
            provider.validate_key(key)
            number = row["attempt"] + 1 if row else 1
            started, clock = now(), time.monotonic()
            conn.execute("INSERT INTO attempts VALUES(?,?, 'sending',?,NULL,?,NULL)", (report_id, number, started, canonical(request)))
            conn.execute("INSERT INTO events VALUES(?,?,?,'sending',?,NULL,NULL,?) ON CONFLICT(report_id) DO UPDATE SET status='sending',attempt=excluded.attempt",
                         (report_id, packet_hash, sources_hash, number, canonical(packet)))
            conn.commit()  # Never hold a SQLite write transaction across the HTTP call.
            article, metadata = None, None
            try:
                article, metadata = provider.complete(request, key, **({"transport": self.transport} if self.transport else {}))
                checks = validate_article_basics(article, packet)
            except Exception as exc:
                # Error bodies and exception messages may echo headers; persist fixed codes only.
                code = exc.code if isinstance(exc, provider.ProviderError) else "article_validation_failed" if isinstance(exc, PressError) else "client_error_outcome_unknown"
                detail = {"error_code": code, "http_status": getattr(exc, "status", None), "metadata": metadata or getattr(exc, "metadata", None),
                          "rejected_article": article, "automatic_retry": False}
                conn.execute("UPDATE attempts SET status='failed',finished=?,detail_json=? WHERE report_id=? AND number=?",
                             (now(), canonical(detail), report_id, number))
                conn.execute("UPDATE events SET status='failed' WHERE report_id=?", (report_id,))
                conn.commit()
                raise PressError(f"联调失败：{code}。不会自动重试；请先检查账本，费用以服务商记录为准。") from None
            finally:
                key = None
            receipt = {"report_id": report_id, "origin": self.origin, "attempt": number, "started": started, "finished": now(),
                       "client_policy_version": "weekly-api-pilot/3-essential",
                       "source_identity": {"type": "source API pilot; no UI acceptance", "pid": os.getpid(), "executable": sys.executable,
                                           "entry": str(Path(__file__).resolve()), "provider_module": str(Path(provider.__file__).resolve())},
                       "implementation_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                                 for name in ("provider.py", "pilot.py", "editorial.py", "briefing.py", "validation.py", "prose.py", "standings.py")},
                       "elapsed_seconds": round(time.monotonic() - clock, 3), "packet_sha256": packet_hash, "sources_sha256": sources_hash,
                       "request_sha256": digest(request), "prompt_versions": preview["prompt_versions"],
                       "message_sha256": digest(request["messages"]), "provider": metadata, "checks": checks,
                       "reading": reading_checks(article, packet), "article_sha256": digest(article)}
            conn.execute("UPDATE attempts SET status='succeeded',finished=?,detail_json=? WHERE report_id=? AND number=?",
                         (receipt["finished"], canonical(receipt), report_id, number))
            conn.execute("UPDATE events SET status='succeeded',article_json=?,receipt_json=? WHERE report_id=?",
                         (canonical(article), canonical(receipt), report_id))
            conn.commit()  # Success persists before materializing human-readable files.
            return self.export_success(article, packet, receipt, cached=False)

    def _recover_complete_return(self, conn, row, packet, sources):
        attempt = conn.execute("SELECT * FROM attempts WHERE report_id=? AND number=?",
                               (row["report_id"], row["attempt"])).fetchone()
        if attempt is None:
            return None
        detail = json.loads(attempt["detail_json"] or "{}")
        article = detail.get("rejected_article")
        if article is None:
            return None
        try:
            checks = validate_article_basics(article, packet)
        except PressError:
            return None
        request = json.loads(attempt["request_json"])
        receipt = {"report_id": row["report_id"], "origin": self.origin, "attempt": row["attempt"],
                   "started": attempt["started"], "finished": attempt["finished"],
                   "client_policy_version": "weekly-api-pilot/3-essential",
                   "packet_sha256": digest(packet), "sources_sha256": digest(sources),
                   "request_sha256": digest(request), "message_sha256": digest(request["messages"]),
                   "provider": detail.get("metadata"), "checks": checks,
                   "reading": reading_checks(article, packet), "article_sha256": digest(article),
                   "local_recovery": {"recorded": now(), "api_calls": 0,
                       "original_attempt_status": attempt["status"],
                       "original_error_code": detail.get("error_code"),
                       "meaning": "complete returned article available; factual quality not accepted"}}
        # Keep the original attempt/error/raw response exactly as recorded.
        # 'succeeded' here means a usable return is available, not factual approval.
        conn.execute("UPDATE events SET status='succeeded',article_json=?,receipt_json=? WHERE report_id=?",
                     (canonical(article), canonical(receipt), row["report_id"]))
        conn.commit()
        result = self.export_success(article, packet, receipt, cached=True)
        return {**result, "recovered_existing_response": True}

    def review(self, review):
        """Record independent reading, never model self-rating or another API call."""
        if not isinstance(review, dict) or set(review) != {"report_id", "decision", "issues", "notes"}:
            raise PressError("验读记录需含 report_id、decision、issues、notes。")
        report_id, decision, issues = review["report_id"], review["decision"], review["issues"]
        if not isinstance(report_id, str) or decision not in ("accept", "reject") or not isinstance(issues, list) or not isinstance(review["notes"], str):
            raise PressError("验读记录字段类型不符。")
        if (decision == "reject" and not issues) or (decision == "accept" and issues):
            raise PressError("拒收必须列出实际错误；通过记录的错误列表应为空。")
        for issue in issues:
            if not isinstance(issue, dict) or set(issue) != {"category", "claim", "fact_ids", "source_keys"}:
                raise PressError("每条错误需给出 category、claim、fact_ids、source_keys。")
            if issue["category"] not in ("factual_error", "scope_error", "incomplete_article") or not isinstance(issue["claim"], str) or not issue["claim"].strip():
                raise PressError("拒收只针对有证据的事实、范围或文章缺失错误；不能为文风偏好重写成功文章。")
            if any(not isinstance(issue[k], list) or not issue[k] or any(not isinstance(v, str) or not v for v in issue[k]) for k in ("fact_ids", "source_keys")):
                raise PressError("拒收需提供对应的事实编号和来源编号。")
        with self.locked(report_id), self.connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE report_id=?", (report_id,)).fetchone()
            if row is None or row["status"] != "succeeded":
                raise PressError("该报道没有等待验读的完整文章。")
            receipt = json.loads(row["receipt_json"])
            if digest(json.loads(row["article_json"])) != receipt.get("article_sha256"):
                raise PressError("验读文章指纹不符。")
            if receipt["reading"]["independent_reading"] != "pending":
                raise PressError("本次文章已有验读结论，不能覆盖。")
            # All cited IDs must be present in the exact request which produced this article.
            packet = json.loads(row["packet_json"])
            if digest(packet) != row["packet_sha256"]:
                raise PressError("验读材料指纹不符。")
            facts = {f["fact_id"]: f for f in packet["facts"]}
            for issue in issues:
                if not set(issue["fact_ids"]) <= set(facts):
                    raise PressError("验读引用了本次材料以外的事实。")
                available = {source["key"] for fid in issue["fact_ids"] for source in facts[fid]["sources"]}
                if not set(issue["source_keys"]) <= available:
                    raise PressError("验读来源编号不属于所引用的事实。")
            receipt["reading"]["independent_reading"] = "accepted" if decision == "accept" else "rejected"
            receipt["reading"]["review"] = {**review, "recorded": now()}
            status = "succeeded" if decision == "accept" else "failed_review"
            conn.execute("UPDATE events SET status=?,receipt_json=? WHERE report_id=?", (status, canonical(receipt), report_id))
            if not receipt.get("local_recovery"):
                conn.execute("UPDATE attempts SET status=?,detail_json=? WHERE report_id=? AND number=?", (status, canonical(receipt), report_id, row["attempt"]))
            conn.commit()
            output = self.export_success(json.loads(row["article_json"]), packet, receipt, cached=True)
            return {**output, "decision": decision, "retry_requires_explicit_flag": decision == "reject"}

    def export_success(self, article, packet, receipt, *, cached):
        output = self.root / "reports" / digest(receipt["report_id"])
        protect_output(output)
        if (self.root / "reports").is_symlink() or output.is_symlink():
            raise PressError("联调产物目录不能使用符号链接。")
        output.mkdir(parents=True, exist_ok=True)
        reading_status = receipt["reading"]["independent_reading"]
        # Validated before commit, or checked against its saved SHA on cache read.
        # A prompt/gate upgrade must not reinterpret a successful frozen article.
        text = format_markdown(article, origin=receipt["origin"], real_api=receipt["origin"] == "deepseek_api", reading_status=reading_status)
        artifacts = {"article.json": canonical(article) + "\n", "receipt.json": canonical(receipt) + "\n", "reading.md": text}
        for name, content in artifacts.items():
            # Rebuild missing files from the ledger without ever calling the API again.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as handle:
                handle.write(content)
                temporary = Path(handle.name)
            temporary.replace(output / name)
        return {"report_id": receipt["report_id"], "cached": cached, "origin": receipt["origin"], "output": str(output),
                "independent_reading": reading_status, "api_calls_this_run": 0 if cached or self.transport is not None else 1}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--review-file", type=Path, help="独立验读JSON；只更新本地验读结论，不请求API")
    parser.add_argument("--prompt-key", action="store_true", help="本机终端隐藏输入，仅在内存中使用；默认读取 DEEPSEEK_API_KEY")
    parser.add_argument("--retry-failed", action="store_true", help="显式重试失败/中断；先前调用可能已计费")
    parser.add_argument("--expected-request-sha256", help="新调用前核对已准备的请求指纹；成功缓存不重新判定")
    args = parser.parse_args(argv)
    try:
        if args.review_file:
            if args.packet or args.sources or args.prompt_key or args.retry_failed or args.expected_request_sha256:
                raise PressError("验读和生成需要分别执行。")
            print(json.dumps(Pilot().review(provider.strict_json(args.review_file.read_text(encoding="utf-8"))), ensure_ascii=False))
            return 0
        if not args.packet or not args.sources:
            raise PressError("生成需同时指定 --packet 和 --sources。")
        packet = provider.strict_json(args.packet.read_text(encoding="utf-8"))
        sources = provider.strict_json(args.sources.read_text(encoding="utf-8"))
        result = Pilot().generate(packet, sources, lambda: local_credential(prompt=args.prompt_key), retry_failed=args.retry_failed,
                                  expected_request_sha256=args.expected_request_sha256)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (PressError, OSError, ValueError):
        # CLI prints only our safe domain errors, never arbitrary input or paths.
        exc = sys.exc_info()[1]
        message = str(exc) if isinstance(exc, PressError) else "输入文件或专用账本不可用。"
        parser.exit(2, message + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
