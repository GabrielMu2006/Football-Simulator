"""No-network provider/ledger regression. Simulated prose is NOT model acceptance."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.press import PressError, provider, repository
from football_simulator.press.editorial import render_markdown, request_preview
from football_simulator.press.pilot import Pilot, local_credential

ROOT = Path(__file__).resolve().parents[1]
KEY = "sk-LOCAL-FAKE-TEST-CREDENTIAL-NOT-A-SECRET-78123"


def response(article):
    return {"id": "simulated-completion", "model": "deepseek-flash", "system_fingerprint": "simulated-only",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(article, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200,
                      "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 100}}


class ProviderProtocolTests(unittest.TestCase):
    def setUp(self):
        self.article = json.loads((ROOT / "docs/press/examples/weekly-feature.article.json").read_text())
        self.request = {"model": provider.MODEL, "messages": [{"role": "user", "content": "json"}]}

    def test_request_is_bounded_nonthinking_json_and_has_no_key(self):
        transport = Mock(return_value=response(self.article))
        article, meta = provider.complete(self.request, KEY, transport=transport)
        payload, key = transport.call_args.args
        self.assertEqual(key, KEY)
        self.assertEqual(payload["max_tokens"], 6144)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertFalse(payload["stream"])
        self.assertNotIn(KEY, repository.canonical(payload))
        self.assertEqual(article, self.article)
        self.assertEqual(meta["usage"]["total_tokens"], 200)
        self.assertFalse(meta["alias_version_pinned"])

    def test_wrong_model_or_credential_is_rejected_before_transport(self):
        transport = Mock()
        for key in ("", "abc\nHeader: value", "含中文", "  key"):
            with self.subTest(key_type=type(key).__name__), self.assertRaises(provider.ProviderError):
                provider.complete(self.request, key, transport=transport)
        with self.assertRaises(provider.ProviderError):
            provider.complete({**self.request, "model": "deepseek-chat"}, KEY, transport=transport)
        transport.assert_not_called()

    def test_reasoning_mode_is_explicit_and_preserved_by_rebuilding_request(self):
        old=provider.build_request(self.request)
        self.assertEqual(old['thinking'],{'type':'disabled'})
        new=provider.build_request({**self.request,'thinking':{'type':'enabled'},'reasoning_effort':'high'})
        self.assertEqual(new['thinking'],{'type':'enabled'})
        self.assertEqual(new['reasoning_effort'],'high')
        self.assertEqual(provider.build_request(new),new)
        self.assertEqual(provider.build_request(old),old)
        long=provider.build_request({**new,'max_tokens':12288})
        self.assertEqual(long['max_tokens'],12288)
        self.assertEqual(provider.build_request(long),long)

    def test_partial_empty_refused_tool_and_invalid_json_outputs_fail(self):
        cases = []
        for reason in ("length", "content_filter", "aborted", "tool_calls", "insufficient_system_resource"):
            r = response(self.article); r["choices"][0]["finish_reason"] = reason; cases.append(r)
        for content in ("", "  ", "```json\n{}\n```", '{"a":1,"a":2}', '{"x":NaN}', "[]"):
            r = response(self.article); r["choices"][0]["message"]["content"] = content; cases.append(r)
        for extra in ({"refusal": "refused"}, {"tool_calls": [{"id": "tool"}]}, {"role": "user"}):
            r = response(self.article); r["choices"][0]["message"].update(extra); cases.append(r)
        for r in cases:
            with self.subTest(case=len(str(r))), self.assertRaises(provider.ProviderError):
                provider.complete(self.request, KEY, transport=Mock(return_value=r))

    def test_wrong_returned_model_and_missing_inconsistent_usage_fail(self):
        for update in ({"model": "deepseek-chat"}, {"usage": None},
                       {"usage": {"prompt_tokens": True, "completion_tokens": 0, "total_tokens": 1}},
                       {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3}}):
            with self.subTest(update=update), self.assertRaises(provider.ProviderError):
                provider.complete(self.request, KEY, transport=Mock(return_value={**response(self.article), **update}))

    def test_complete_fenced_article_is_losslessly_unwrapped(self):
        result=response(self.article)
        result['choices'][0]['message']['content']='```json\n'+result['choices'][0]['message']['content']+'\n```'
        article,metadata=provider.complete(self.request,KEY,transport=Mock(return_value=result))
        self.assertEqual(article,self.article)
        self.assertEqual(metadata['article_format_recovery']['method'],'outer_envelope/1')

    def test_truncated_output_preserves_safe_usage_without_accepting_article(self):
        r = response(self.article); r["choices"][0]["finish_reason"] = "length"
        with self.assertRaises(provider.ProviderError) as caught:
            provider.complete(self.request, KEY, transport=Mock(return_value=r))
        self.assertEqual(caught.exception.metadata["finish_reason"], "length")
        self.assertEqual(caught.exception.metadata["usage"]["total_tokens"], 200)

    def test_echoed_credential_is_never_returned_for_storage(self):
        r = response({"echo": KEY})
        with self.assertRaises(provider.ProviderError) as caught:
            provider.complete(self.request, KEY, transport=Mock(return_value=r))
        self.assertEqual(caught.exception.code, "credential_in_response")
        self.assertNotIn(KEY, str(caught.exception))

    def test_official_transport_uses_https_fixed_host_deadline_and_size_limit(self):
        connection = Mock(); connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read1.side_effect = [b'{"ok":true}', b'']
        with patch.object(provider.http.client, "HTTPSConnection", return_value=connection) as https, patch.object(provider.threading, "Timer") as timer:
            self.assertEqual(provider.send_once(self.request, KEY), {"ok": True})
        self.assertEqual(https.call_args.args, ("api.deepseek.com",))
        self.assertEqual(https.call_args.kwargs["port"], 443)
        self.assertEqual(https.call_args.kwargs["timeout"], 10)
        self.assertEqual(timer.call_args.args[0], 120)
        self.assertEqual(connection.request.call_args.args[:2], ("POST", "/chat/completions"))
        connection.sock.settimeout.assert_called_once_with(120)
        connection.close.assert_called_once()
        timer.return_value.cancel.assert_called_once()
        connection.reset_mock(); connection.getresponse.return_value.read1.side_effect = [b'x' * 17]
        with patch.object(provider.http.client, "HTTPSConnection", return_value=connection), patch.object(provider.threading, "Timer"), patch.object(provider, "MAX_RESPONSE_BYTES", 16):
            with self.assertRaises(provider.ProviderError) as caught:
                provider.send_once(self.request, KEY)
        self.assertEqual(caught.exception.code, "response_too_large")

    def test_redirect_http_errors_and_transport_timeout_do_not_retry_or_echo(self):
        for status in (301, 307, 400, 401, 402, 403, 429, 500, 503):
            connection = Mock(); connection.getresponse.return_value.status = status
            with patch.object(provider.http.client, "HTTPSConnection", return_value=connection), patch.object(provider.threading, "Timer"):
                with self.assertRaises(provider.ProviderError) as caught:
                    provider.send_once(self.request, KEY)
            self.assertEqual(caught.exception.status, status)
            connection.request.assert_called_once()
            connection.getresponse.return_value.read1.assert_not_called()
        for error in (TimeoutError(KEY), OSError(KEY)):
            connection = Mock(); connection.request.side_effect = error
            with patch.object(provider.http.client, "HTTPSConnection", return_value=connection), patch.object(provider.threading, "Timer"):
                with self.assertRaises(provider.ProviderError) as caught:
                    provider.send_once(self.request, KEY)
            self.assertNotIn(KEY, str(caught.exception))
            connection.request.assert_called_once()

    def test_environment_or_tty_input_never_writes_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": KEY}):
            self.assertEqual(local_credential(), KEY)
        with patch("sys.stdin.isatty", return_value=False), patch("getpass.getpass") as prompt:
            with self.assertRaises(PressError): local_credential(prompt=True)
            prompt.assert_not_called()
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", return_value=KEY):
            self.assertEqual(local_credential(prompt=True), KEY)
        import getpass
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", side_effect=getpass.GetPassWarning()):
            with self.assertRaises(PressError): local_credential(prompt=True)


class PilotLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material_root = tempfile.TemporaryDirectory(prefix="fs_press_pilot_material_")
        old = runtime._DATA_ROOT_OVERRIDE
        try:
            runtime.set_data_root_override(Path(cls.material_root.name))
            service = GameService(rng_factory=lambda: random.Random(423), edition_archives=True)
            service.create_save("PilotTest"); service.initialize_save("PilotTest")
            repository.enable("PilotTest")
            for _ in range(3): service.advance_week("PilotTest")
            cls.material = repository.load_week("PilotTest", 1, 3)
        finally:
            runtime.set_data_root_override(old)
        topic = cls.material["packet"]["editorial"]["topics"][0]
        refs = topic["required_fact_ids"]
        cls.article = {"schema_version": "press.article/0.1-draft", "report_id": cls.material["packet"]["meta"]["report_id"],
                       "title": "模拟响应，仅检查流程", "title_fact_ids": refs, "summary": "不是模型生成效果。", "summary_fact_ids": refs,
                       "used_topic_ids": [topic["topic_id"]], "sections": [{"heading": "本周比赛", "heading_fact_ids": refs,
                       "blocks": [{"kind": "body", "label": None, "text": "本周的比赛已经完成。", "fact_ids": refs}]}]}

    @classmethod
    def tearDownClass(cls):
        cls.material_root.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fs_press_pilot_test_")
        self.addCleanup(self.tmp.cleanup)
        self.transport = Mock(return_value=response(self.article))
        self.pilot = Pilot(Path(self.tmp.name) / "ledger", transport=self.transport)
        self.credential = Mock(return_value=KEY)

    def generate(self, **kwargs):
        return self.pilot.generate(self.material["packet"], self.material["sources"], self.credential, **kwargs)

    def rows(self, table):
        with closing(sqlite3.connect(self.pilot.database)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM " + table)]

    def test_success_cached_before_credentials_and_source_files_unchanged(self):
        dbs = {p: p.read_bytes() for p in Path(self.material_root.name).rglob("*.sqlite3")}
        result = self.generate()
        self.assertFalse(result["cached"])
        self.assertEqual(result["origin"], "simulated_provider")
        self.assertEqual(result["api_calls_this_run"], 0)
        self.credential.side_effect = AssertionError("cached result must not read credentials")
        again = self.generate(retry_failed=True)
        self.assertTrue(again["cached"])
        self.transport.assert_called_once()
        self.assertTrue(all(p.read_bytes() == content for p, content in dbs.items()))
        self.assertEqual(len(self.rows("attempts")), 1)
        self.assertEqual(self.rows("events")[0]["status"], "succeeded")
        out = Path(result["output"])
        self.assertIn("非 DeepSeek 实测", (out / "reading.md").read_text())
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(receipt["reading"]["independent_reading"], "pending")
        self.assertEqual(receipt["provider"]["usage"]["total_tokens"], 200)

    def test_missing_key_and_invalid_material_do_not_create_attempts(self):
        self.credential.return_value = ""
        with self.assertRaises(PressError): self.generate()
        self.transport.assert_not_called()
        self.assertEqual(self.rows("attempts"), [])
        bad = deepcopy(self.material["packet"]); bad["week_summary"]["completed_matches"] += 1
        with self.assertRaises(PressError):
            self.pilot.generate(bad, self.material["sources"], self.credential)
        with self.assertRaises(PressError):
            self.pilot.generate(self.material["packet"], None, self.credential)
        self.assertEqual(self.rows("attempts"), [])

    def test_old_packet_cannot_start_a_new_paid_attempt(self):
        old = deepcopy(self.material["packet"])
        old["meta"]["exporter_version"] = "weekly-export/1"
        with self.assertRaisesRegex(PressError, "旧材料仅可读取"):
            self.pilot.generate(old, self.material["sources"], self.credential)
        self.credential.assert_not_called()
        self.transport.assert_not_called()
        self.assertEqual(self.rows("attempts"), [])

    def test_hash_verified_success_cache_is_not_rejudged_by_new_prose_policy(self):
        self.generate()
        self.transport.reset_mock()
        self.credential.reset_mock()
        with patch('football_simulator.press.prose.validate_narrative', side_effect=AssertionError('cached prose is immutable')):
            cached = self.generate()
        self.assertTrue(cached['cached'])
        self.transport.assert_not_called()
        self.credential.assert_not_called()

    def test_complete_article_is_saved_without_prose_regex_rejection(self):
        with patch('football_simulator.press.prose.validate_narrative', side_effect=PressError('ambiguous prose')):
            result = self.generate()
        self.assertEqual(result['independent_reading'], 'pending')
        self.assertEqual(self.rows('events')[0]['status'], 'succeeded')

    def test_old_prose_rejection_reuses_return_without_new_request(self):
        self.generate()
        with closing(sqlite3.connect(self.pilot.database)) as conn:
            detail = repository.canonical({'error_code':'article_validation_failed', 'rejected_article':self.article,
                                           'metadata':{'returned_model':'deepseek-flash'}})
            conn.execute("UPDATE attempts SET status='failed', detail_json=?", (detail,))
            conn.execute("UPDATE events SET status='failed', article_json=NULL, receipt_json=NULL")
            conn.commit()
        self.credential.reset_mock(); self.transport.reset_mock()
        result = self.generate()
        self.assertTrue(result['recovered_existing_response'])
        self.assertEqual(self.rows('attempts')[0]['detail_json'], detail)
        self.assertEqual(self.rows('attempts')[0]['status'], 'failed')
        self.credential.assert_not_called(); self.transport.assert_not_called()

    def test_prepared_request_hash_must_match_before_credentials_or_attempt(self):
        for expected in ('0' * 64, 'invalid', 1):
            with self.subTest(expected=expected), self.assertRaisesRegex(PressError, '指纹'):
                self.generate(expected_request_sha256=expected)
        self.credential.assert_not_called()
        self.transport.assert_not_called()
        self.assertEqual(self.rows('attempts'), [])

    def test_prepared_request_hash_accepts_exact_payload_and_cache_still_wins(self):
        expected = repository.digest(provider.build_request(request_preview(self.material['packet'], self.material['sources'])['request']))
        self.generate(expected_request_sha256=expected)
        self.transport.reset_mock(); self.credential.reset_mock()
        cached = self.generate(expected_request_sha256='0' * 64)
        self.assertTrue(cached['cached'])
        self.credential.assert_not_called()
        self.transport.assert_not_called()

    def test_changed_prompt_cannot_silently_use_prepared_request_hash(self):
        preview = request_preview(self.material['packet'], self.material['sources'])
        expected = repository.digest(provider.build_request(preview['request']))
        preview['request']['messages'][0]['content'] += '\nChanged after preparation.'
        with patch('football_simulator.press.pilot.request_preview', return_value=preview):
            with self.assertRaisesRegex(PressError, '准备时的指纹不同'):
                self.generate(expected_request_sha256=expected)
        self.credential.assert_not_called()
        self.transport.assert_not_called()
        self.assertEqual(self.rows('attempts'), [])

    def test_failure_requires_explicit_retry_and_history_is_retained(self):
        self.transport.side_effect = provider.ProviderError("rate_limited", status=429)
        with self.assertRaises(PressError): self.generate()
        with self.assertRaises(PressError): self.generate()
        self.transport.assert_called_once()
        self.transport.side_effect = None
        self.assertFalse(self.generate(retry_failed=True)["cached"])
        self.assertEqual([r["status"] for r in self.rows("attempts")], ["failed", "succeeded"])

    def test_invalid_article_does_not_become_success(self):
        bad = deepcopy(self.article); bad["report_id"] = "wrong-world"
        self.transport.return_value = response(bad)
        with self.assertRaises(PressError): self.generate()
        self.assertEqual(self.rows("events")[0]["status"], "failed")
        detail = json.loads(self.rows("attempts")[0]["detail_json"])
        self.assertEqual(detail["error_code"], "article_validation_failed")
        self.assertEqual(detail["rejected_article"], bad)

    def test_crash_state_is_not_automatically_retried(self):
        self.transport.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.generate()
        self.assertEqual(self.rows("events")[0]["status"], "sending")
        with self.assertRaises(PressError): self.generate()
        self.assertEqual(self.transport.call_count, 1)
        self.transport.side_effect = None
        self.generate(retry_failed=True)
        self.assertEqual([r["status"] for r in self.rows("attempts")], ["interrupted", "succeeded"])

    def test_concurrent_call_has_one_sender(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def waiting(*args):
            entered.set()
            if not release.wait(5): raise RuntimeError("test stalled")
            return response(self.article)
        self.transport.side_effect = waiting
        def first():
            try: self.generate()
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=first)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(PressError): self.generate(retry_failed=True)
            self.assertEqual(self.transport.call_count, 1)
        finally:
            release.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_same_id_changed_facts_and_corrupt_success_are_refused(self):
        self.generate()
        bad = deepcopy(self.material["packet"])
        bad["editorial"]["topics"][0]["suggested_angle"] = "更改输入，但报道标识不变"
        with self.assertRaisesRegex(PressError, "同一报道标识的事实指纹已变"):
            self.pilot.generate(bad, self.material["sources"], self.credential)
        with closing(sqlite3.connect(self.pilot.database)) as conn:
            altered = deepcopy(self.article); altered["title"] = "悄悄替换"
            conn.execute("UPDATE events SET article_json=?", (repository.canonical(altered),)); conn.commit()
        with self.assertRaises(PressError): self.generate()
        self.transport.assert_called_once()

    def review_record(self, decision="accept"):
        f = next(f for f in self.material["packet"]["facts"] if f["sources"])
        return {"report_id": self.article["report_id"], "decision": decision, "notes": "受控流程用例，非模型验读。",
                "issues": [] if decision == "accept" else [{"category": "factual_error", "claim": "测试注入的事实错误",
                    "fact_ids": [f["fact_id"]], "source_keys": [f["sources"][0]["key"]]}]}

    def test_independent_acceptance_is_recorded_and_cannot_rewrite_success(self):
        self.generate()
        review = self.pilot.review(self.review_record())
        self.assertEqual(review["independent_reading"], "accepted")
        self.assertEqual(review["api_calls_this_run"], 0)
        with self.assertRaises(PressError): self.pilot.review(self.review_record("reject"))
        self.assertTrue(self.generate(retry_failed=True)["cached"])
        self.transport.assert_called_once()

    def test_evidenced_rejection_requires_manual_retry_preserving_original(self):
        self.generate()
        self.pilot.review(self.review_record("reject"))
        self.assertEqual(self.generate()['independent_reading'], 'rejected')
        self.generate(retry_failed=True)
        self.assertEqual([r["status"] for r in self.rows("attempts")], ["failed_review", "succeeded"])
        detail = json.loads(self.rows("attempts")[0]["detail_json"])
        self.assertEqual(detail["reading"]["independent_reading"], "rejected")
        self.assertEqual(self.transport.call_count, 2)

    def test_style_preferences_or_unknown_review_sources_cannot_enable_retry(self):
        self.generate()
        for field, value in (("category", "style_preference"), ("fact_ids", ["nonexistent"]), ("source_keys", ["not-this-fact"])):
            review = self.review_record("reject"); review["issues"][0][field] = value
            with self.subTest(field=field), self.assertRaises(PressError): self.pilot.review(review)
        self.assertTrue(self.generate()["cached"])
        self.transport.assert_called_once()

    def test_export_failure_after_success_is_repaired_without_paid_retry(self):
        with patch.object(self.pilot, "export_success", side_effect=OSError("disk error")):
            with self.assertRaises(OSError): self.generate()
        result = self.generate()
        self.assertTrue(result["cached"])
        self.assertTrue((Path(result["output"]) / "article.json").is_file())
        self.transport.assert_called_once()

    def test_credential_does_not_appear_in_ledger_artifacts_or_failure_message(self):
        self.transport.side_effect = OSError("Authorization: Bearer " + KEY)
        with self.assertRaises(PressError) as caught: self.generate()
        self.assertNotIn(KEY, str(caught.exception))
        self.transport.side_effect = None
        self.generate(retry_failed=True)
        for path in self.pilot.root.rglob("*"):
            if path.is_file(): self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_ledger_rejects_unowned_nonempty_or_protected_directories(self):
        with self.assertRaises(PressError): Pilot(ROOT / "release/macos", transport=self.transport)
        with self.assertRaises(PressError): Pilot(ROOT, transport=self.transport)
        with self.assertRaises(PressError): Pilot(Path(self.tmp.name), transport=self.transport)

    def test_real_api_markdown_does_not_claim_offline_or_finished_semantic_review(self):
        text, _ = render_markdown(self.article, self.material["packet"], origin="deepseek_api", real_api=True)
        self.assertNotIn("非 DeepSeek 实测", text)
        self.assertIn("仍待独立验读", text)
        text, _ = render_markdown(self.article, self.material["packet"], origin="deepseek_api", real_api=True, reading_status="accepted")
        self.assertIn("记录为通过", text)
        self.assertNotIn("仍待独立验读", text)


if __name__ == "__main__":
    unittest.main()
