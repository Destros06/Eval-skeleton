import json
import os
import tempfile
import unittest
from pathlib import Path

from evalkit.graders import extract_json, grade
from evalkit.report import format_report, load_results
from evalkit.runner import run_eval
from evalkit.tasks import TaskFileError, load_tasks, validate_tasks

ROOT = Path(__file__).resolve().parent.parent


class ExtractJson(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(extract_json('x ```json\n{"a": 1}\n``` y'), {"a": 1})

    def test_prose_around(self):
        self.assertEqual(extract_json('Answer: {"a": {"b": 2}} done'), {"a": {"b": 2}})

    def test_last_object_wins(self):
        self.assertEqual(extract_json('e.g. {"a": 1}\nfinal: {"a": 2}'), {"a": 2})

    def test_none(self):
        self.assertIsNone(extract_json("no json here {oops"))


class JsonFields(unittest.TestCase):
    spec = {"type": "json_fields", "fields": {"v": True, "line": {"in": [2, 3]}}}

    def test_pass(self):
        self.assertTrue(grade('{"v": true, "line": 3}', self.spec).passed)

    def test_partial_score(self):
        r = grade('{"v": true, "line": 9}', self.spec)
        self.assertFalse(r.passed)
        self.assertEqual(r.score, 0.5)

    def test_bool_is_not_int(self):
        self.assertFalse(grade('{"v": 1, "line": 2}', self.spec).passed)

    def test_numeric_string_tolerated(self):
        self.assertTrue(grade('{"v": true, "line": "2"}', self.spec).passed)

    def test_missing_field(self):
        self.assertIn("missing", grade('{"v": true}', self.spec).details)

    def test_no_json(self):
        self.assertFalse(grade("I think it is vulnerable", self.spec).passed)


class SimpleGraders(unittest.TestCase):
    def test_exact_match_normalizes(self):
        self.assertTrue(grade("  Hello   WORLD ", {"type": "exact_match", "expected": "hello world"}).passed)

    def test_regex_negate(self):
        self.assertTrue(grade("safe", {"type": "regex", "pattern": "unsafe", "negate": True}).passed)


class PythonTests(unittest.TestCase):
    def test_pass_and_fail(self):
        spec = {"type": "python_tests", "entrypoint": "f", "tests": "assert f(2) == 4"}
        self.assertTrue(grade("```python\ndef f(x): return x*2\n```", spec).passed)
        self.assertFalse(grade("```python\ndef f(x): return x+1\n```", spec).passed)

    def test_timeout(self):
        spec = {"type": "python_tests", "tests": "", "timeout": 1}
        r = grade("```python\nwhile True: pass\n```", spec)
        self.assertFalse(r.passed)
        self.assertIn("timeout", r.details)

    def test_secrets_not_leaked_to_model_code(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-test-secret"
        try:
            spec = {"type": "python_tests",
                    "tests": "import os; assert 'OPENROUTER_API_KEY' not in os.environ"}
            self.assertTrue(grade("x = 1", spec).passed)
        finally:
            del os.environ["OPENROUTER_API_KEY"]


class Tasks(unittest.TestCase):
    def _write(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "t.jsonl"
        p.write_text(text, encoding="utf-8")
        return p

    def test_pilot_tasks_load_and_graders_are_sane(self):
        tasks = load_tasks(ROOT / "tasks" / "security_pilot.jsonl")
        self.assertEqual(validate_tasks(tasks), [])

    def test_interaction_pilot_graders_are_sane(self):
        tasks = load_tasks(ROOT / "tasks" / "interaction_pilot.jsonl")
        self.assertEqual(len(tasks), 12)
        self.assertEqual(validate_tasks(tasks), [])


    def test_ground_truth_reproduces_on_this_python(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_interaction_tasks.py"), "--check"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_duplicate_id(self):
        line = json.dumps({"id": "a", "prompt": "p", "grader": {"type": "exact_match", "expected": "x"}})
        with self.assertRaises(TaskFileError):
            load_tasks(self._write(line + "\n" + line + "\n"))

    def test_unknown_grader_and_typo_keys(self):
        with self.assertRaises(TaskFileError):
            load_tasks(self._write(json.dumps({"id": "a", "prompt": "p", "grader": {"type": "nope"}})))
        with self.assertRaises(TaskFileError):
            load_tasks(self._write(json.dumps({"id": "a", "prompt": "p", "metadat": {},
                                               "grader": {"type": "exact_match", "expected": "x"}})))

    def test_validation_catches_lenient_grader(self):
        line = json.dumps({"id": "a", "prompt": "p", "reference": "yes", "known_bad": ["yes"],
                           "grader": {"type": "exact_match", "expected": "yes"}})
        problems = validate_tasks(load_tasks(self._write(line)))
        self.assertTrue(any("false pass" in p for p in problems))


class FlakyModel:
    """Fails the first call for each prompt, then answers correctly."""
    name = "flaky"

    def __init__(self):
        self.calls = 0
        self.seen = set()

    def complete(self, system, prompt):
        self.calls += 1
        if prompt not in self.seen:
            self.seen.add(prompt)
            raise RuntimeError("boom")
        return "4"


class Runner(unittest.TestCase):
    def test_resume_retries_only_errors(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "t.jsonl"
        p.write_text(json.dumps({"id": "q", "prompt": "2+2?",
                                 "grader": {"type": "exact_match", "expected": "4"}}) + "\n")
        tasks = load_tasks(p)
        model = FlakyModel()
        factory = lambda spec, **kw: model  # noqa: E731
        quiet = lambda s: None  # noqa: E731

        path = run_eval(tasks, ["flaky"], d, model_factory=factory, progress=quiet)
        first = load_results(path)["flaky|q|0"]
        self.assertTrue(first["model_error"])
        self.assertEqual(model.calls, 1)

        run_eval(tasks, ["flaky"], d, model_factory=factory, progress=quiet)   # retries the error
        second = load_results(path)["flaky|q|0"]
        self.assertTrue(second["passed"])
        self.assertEqual(model.calls, 2)

        run_eval(tasks, ["flaky"], d, model_factory=factory, progress=quiet)   # nothing left to do
        self.assertEqual(model.calls, 2)
        self.assertIn("flaky", format_report(load_results(path)))

    def test_grader_crash_is_not_a_model_failure(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "t.jsonl"
        # exact_match without "expected" -> KeyError inside the grader
        p.write_text(json.dumps({"id": "q", "prompt": "x", "grader": {"type": "exact_match"}}) + "\n")
        model = FlakyModel(); model.seen.add("x")
        path = run_eval(load_tasks(p), ["flaky"], d, model_factory=lambda s, **k: model, progress=lambda s: None)
        rec = load_results(path)["flaky|q|0"]
        self.assertTrue(rec["grader_error"])
        self.assertIn("GRADER ERRORS", format_report(load_results(path)))


class DotEnv(unittest.TestCase):
    def _load(self, data: bytes, preset=None):
        from evalkit.cli import _load_dotenv
        d = tempfile.mkdtemp()
        path = Path(d) / ".env"
        path.write_bytes(data)
        saved = os.environ.pop("OPENROUTER_API_KEY", None)
        if preset is not None:
            os.environ["OPENROUTER_API_KEY"] = preset
        try:
            _load_dotenv(str(path))
            return os.environ.get("OPENROUTER_API_KEY")
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
            if saved is not None:
                os.environ["OPENROUTER_API_KEY"] = saved

    def test_plain_utf8(self):
        self.assertEqual(self._load(b"OPENROUTER_API_KEY=abc\n"), "abc")

    def test_utf8_with_bom_windows_notepad(self):
        self.assertEqual(self._load(b"\xef\xbb\xbfOPENROUTER_API_KEY=abc\r\n"), "abc")

    def test_utf16_windows_powershell_redirect(self):
        self.assertEqual(self._load("OPENROUTER_API_KEY=abc\r\n".encode("utf-16")), "abc")

    def test_quotes_spaces_export_and_comments(self):
        data = b"# my key\nexport OPENROUTER_API_KEY = 'abc'\n"
        self.assertEqual(self._load(data), "abc")

    def test_empty_env_var_does_not_block_file(self):
        self.assertEqual(self._load(b"OPENROUTER_API_KEY=abc\n", preset=""), "abc")

    def test_real_env_var_wins(self):
        self.assertEqual(self._load(b"OPENROUTER_API_KEY=fromfile\n", preset="fromshell"), "fromshell")

    def test_blank_value_stays_unset(self):
        self.assertIsNone(self._load(b"OPENROUTER_API_KEY=\n"))


def _payload(content, finish="stop", reasoning=None):
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning"] = reasoning
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20}}


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class OpenRouterBehaviour(unittest.TestCase):
    """Fake HTTP: no network needed."""

    def _model(self, **kw):
        from evalkit.models import OpenRouterModel
        return OpenRouterModel("x/y", api_key="k", **kw)

    def _call(self, responses, **kw):
        from unittest import mock
        urlopen = mock.Mock(side_effect=[_Resp(r) for r in responses])
        with mock.patch("evalkit.models.urllib.request.urlopen", urlopen), \
                mock.patch("evalkit.models.time.sleep"):
            return self._model(**kw).complete(None, "p"), urlopen.call_count

    def test_normal(self):
        comp, calls = self._call([_payload("hello")])
        self.assertEqual((comp.text, comp.finish_reason, calls), ("hello", "stop", 1))
        self.assertEqual(comp.usage["completion_tokens"], 20)

    def test_empty_with_length_is_truncation_and_not_retried(self):
        comp, calls = self._call([_payload("", finish="length", reasoning="thinking " * 50)])
        self.assertEqual(comp.finish_reason, "length")
        self.assertGreater(comp.reasoning_chars, 0)
        self.assertEqual(calls, 1)  # previously this burned 5 requests of free quota

    def test_empty_stop_retried_once_then_ok(self):
        comp, calls = self._call([_payload(""), _payload("fine")])
        self.assertEqual((comp.text, calls), ("fine", 2))

    def test_empty_stop_gives_up_after_one_retry(self):
        from evalkit.models import ModelError
        with self.assertRaises(ModelError):
            self._call([_payload(""), _payload(""), _payload("never reached")])


class TruncationInRunner(unittest.TestCase):
    def _setup(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "t.jsonl"
        p.write_text(json.dumps({"id": "q", "prompt": "2+2?",
                                 "grader": {"type": "exact_match", "expected": "4"}}) + "\n")

        class Budget:
            name = "b"
            calls = 0

            def complete(self, system, prompt):
                from evalkit.schema import Completion
                Budget.calls += 1
                return Completion(text="", finish_reason="length", reasoning_chars=999)

        return d, load_tasks(p), Budget

    def test_truncated_is_excluded_from_pass_rate_and_reported(self):
        d, tasks, Budget = self._setup()
        f = lambda spec, **kw: Budget()  # noqa: E731
        path = run_eval(tasks, ["b"], d, max_tokens=100, model_factory=f, progress=lambda s: None)
        rec = load_results(path)["b|q|0"]
        self.assertTrue(rec["truncated"])
        self.assertEqual(rec["max_tokens"], 100)
        text = format_report(load_results(path))
        self.assertIn("Truncated answers", text)
        self.assertIn("n/a", text)  # no graded samples -> no fake 0% pass rate

    def test_resume_skips_same_budget_but_retries_with_larger(self):
        d, tasks, Budget = self._setup()
        f = lambda spec, **kw: Budget()  # noqa: E731
        q = lambda s: None  # noqa: E731
        run_eval(tasks, ["b"], d, max_tokens=100, model_factory=f, progress=q)
        run_eval(tasks, ["b"], d, max_tokens=100, model_factory=f, progress=q)
        self.assertEqual(Budget.calls, 1)
        run_eval(tasks, ["b"], d, max_tokens=500, model_factory=f, progress=q)
        self.assertEqual(Budget.calls, 2)


class PythonTestsProse(unittest.TestCase):
    def test_prose_instead_of_code_gets_clear_message(self):
        spec = {"type": "python_tests", "entrypoint": "f", "tests": "assert f(2) == 4"}
        r = grade("We need to rewrite save_path to be secure. The original code had a flaw.", spec)
        self.assertFalse(r.passed)
        self.assertIn("no ```python block", r.details)


class UsageReport(unittest.TestCase):
    def test_usage_table_shows_tokens_thinking_and_cost(self):
        base = {"task_id": "t", "sample": 0, "category": "c", "output": "x", "passed": True,
                "score": 1.0, "details": "", "model_error": None, "grader_error": None}
        recs = {
            "a|t|0": {**base, "key": "a|t|0", "model": "a", "latency_s": 4.0,
                      "usage": {"completion_tokens": 200, "cost": 0.002,
                                "completion_tokens_details": {"reasoning_tokens": 150}}},
            "b|t|0": {**base, "key": "b|t|0", "model": "b", "latency_s": 1.0,
                      "usage": {"completion_tokens": 6, "cost": 0.0,
                                "completion_tokens_details": {"reasoning_tokens": 0}}},
        }
        text = format_report(recs)
        self.assertIn("avg_think_tok", text)
        self.assertIn("$0.0020", text)
        self.assertIn("150", text)


class ReasoningEffortSpec(unittest.TestCase):
    def test_suffix_is_sent_to_api_and_stripped_from_model_id(self):
        from unittest import mock
        from evalkit.models import get_model
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data))
            return _Resp(_payload("ok"))

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                mock.patch("evalkit.models.urllib.request.urlopen", fake_urlopen):
            get_model("openrouter:a/b#high").complete(None, "p")
            get_model("openrouter:a/b").complete(None, "p")
        self.assertEqual(captured[0]["model"], "a/b")
        self.assertEqual(captured[0]["reasoning"], {"effort": "high"})
        self.assertNotIn("reasoning", captured[1])  # default: nothing extra is sent

    def test_bad_effort_rejected(self):
        from evalkit.models import get_model
        with self.assertRaises(ValueError):
            get_model("openrouter:a/b#extreme")


class ContentFilter(unittest.TestCase):
    def test_content_filter_not_retried(self):
        from unittest import mock
        from evalkit.models import OpenRouterModel
        urlopen = mock.Mock(side_effect=[_Resp(_payload("", finish="content_filter"))] * 3)
        with mock.patch("evalkit.models.urllib.request.urlopen", urlopen), \
                mock.patch("evalkit.models.time.sleep"):
            comp = OpenRouterModel("x/y", api_key="k").complete(None, "p")
        self.assertEqual(comp.finish_reason, "content_filter")
        self.assertEqual(urlopen.call_count, 1)

    def test_runner_marks_blocked_and_excludes_it(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "t.jsonl"
        p.write_text(json.dumps({"id": "q", "prompt": "x",
                                 "grader": {"type": "exact_match", "expected": "4"}}) + "\n")

        class Filtered:
            name = "f"

            def complete(self, system, prompt):
                from evalkit.schema import Completion
                return Completion(text="", finish_reason="content_filter")

        path = run_eval(load_tasks(p), ["f"], d, model_factory=lambda s, **k: Filtered(),
                        progress=lambda s: None)
        rec = load_results(path)["f|q|0"]
        self.assertTrue(rec["blocked"])
        text = format_report(load_results(path))
        self.assertIn("Blocked by the provider", text)
        self.assertIn("n/a", text)


class Consistency(unittest.TestCase):
    def _rec(self, model, task, i, passed):
        return {"key": f"{model}|{task}|{i}", "model": model, "task_id": task, "sample": i, "category": "c",
                "output": "x", "passed": passed, "score": 1.0 if passed else 0.0, "details": "",
                "model_error": None, "grader_error": None}

    def test_pass_at_n_and_pass_hat_n(self):
        recs = {}
        for i, ok in enumerate([True, True, True]):
            r = self._rec("m", "t1", i, ok); recs[r["key"]] = r
        for i, ok in enumerate([True, False, False]):
            r = self._rec("m", "t2", i, ok); recs[r["key"]] = r
        text = format_report(recs)
        self.assertIn("pass^n", text)
        self.assertIn("66.7%", text)   # average pass rate: (3/3 + 1/3) / 2
        self.assertIn("100.0%", text)  # pass@n: both tasks passed at least once
        self.assertIn("50.0%", text)   # pass^n: only t1 passed every trial

    def test_no_table_with_single_trial(self):
        r = self._rec("m", "t1", 0, True)
        self.assertNotIn("Consistency", format_report({r["key"]: r}))


class OnlyFlag(unittest.TestCase):
    def test_only_filters_tasks(self):
        import contextlib
        import io
        from evalkit.cli import main
        d = tempfile.mkdtemp()
        out = str(Path(d) / "r")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["run", "--tasks", str(ROOT / "tasks" / "security_pilot.jsonl"),
                         "--models", "mock:naive", "--only", "sqli", "--out", out])
        self.assertEqual(code, 0)
        recs = load_results(Path(out) / "results.jsonl")
        self.assertEqual({r["task_id"] for r in recs.values()}, {"or-sqli"})
    
    def test_only_with_no_match_is_an_error(self):
        import contextlib
        import io
        from evalkit.cli import main
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["run", "--tasks", str(ROOT / "tasks" / "security_pilot.jsonl"),
                         "--models", "mock:naive", "--only", "nothing-like-this",
                         "--out", str(Path(tempfile.mkdtemp()) / "r")])
        self.assertEqual(code, 2)


class RefusalAndAvailability(unittest.TestCase):
    def test_is_refusal(self):
        from evalkit.refusal import is_refusal
        self.assertTrue(is_refusal("I'm sorry, but I can't help with that request."))
        self.assertTrue(is_refusal("I can\u2019t assist with that."))
        self.assertTrue(is_refusal("I cannot provide that. It violates my guidelines."))
        self.assertFalse(is_refusal("The function is vulnerable because the check is a prefix test."))
        self.assertFalse(is_refusal(""))

    def test_valid_json_with_hedge_is_not_refused(self):
        # the refusal check only runs when no JSON was found, so a hedged but structured answer is graded normally
        r = grade('I can\'t be fully sure, but: {"v": true, "line": 3}',
                  {"type": "json_fields", "fields": {"v": True, "line": {"in": [2, 3]}}})
        self.assertTrue(r.passed)

    def _run(self, spec, **kw):
        d = tempfile.mkdtemp()
        out = str(Path(d) / "r")
        import contextlib, io
        from evalkit.cli import main
        argv = ["run", "--tasks", str(ROOT / "tasks" / "security_pilot.jsonl"), "--models", spec, "--out", out]
        with contextlib.redirect_stdout(io.StringIO()):
            main(argv + kw.get("extra", []))
        return out

    def test_mock_refuse_is_marked_refused_not_failed(self):
        out = self._run("mock:refuse")
        recs = load_results(Path(out) / "results.jsonl")
        self.assertTrue(recs and all(r["refused"] for r in recs.values()))
        self.assertIn("non_completion", format_report(recs))

    def test_mock_blocked_is_marked_blocked(self):
        out = self._run("mock:blocked")
        recs = load_results(Path(out) / "results.jsonl")
        self.assertTrue(recs and all(r["blocked"] for r in recs.values()))

    def test_resume_does_not_reroll_blocked_unless_asked(self):
        out = self._run("mock:blocked")
        first = (Path(out) / "results.jsonl").read_text().count("\n")
        import contextlib, io
        from evalkit.cli import main
        base = ["run", "--tasks", str(ROOT / "tasks" / "security_pilot.jsonl"), "--models", "mock:blocked", "--out", out]
        with contextlib.redirect_stdout(io.StringIO()):
            main(base)
        self.assertEqual((Path(out) / "results.jsonl").read_text().count("\n"), first)
        with contextlib.redirect_stdout(io.StringIO()):
            main(base + ["--retry-blocked"])
        self.assertEqual((Path(out) / "results.jsonl").read_text().count("\n"), 2 * first)

    def test_wilson_interval_matches_hand_calculation(self):
        from evalkit.report import wilson
        lo, hi = wilson(8, 30)
        self.assertAlmostEqual(lo, 0.142, places=2)
        self.assertAlmostEqual(hi, 0.444, places=2)
        self.assertEqual(wilson(0, 0), (0.0, 0.0))


class LabelNormalisationAndRegrade(unittest.TestCase):
    spec = {"type": "json_fields", "fields": {"vulnerable": True, "vuln_type": "path_traversal"}}

    def test_label_formats_are_equivalent(self):
        for label in ["path_traversal", "Path Traversal", "path-traversal", "PATH  TRAVERSAL", " path_traversal "]:
            r = grade('{"vulnerable": true, "vuln_type": "%s"}' % label, self.spec)
            self.assertTrue(r.passed, label)

    def test_different_labels_still_differ(self):
        self.assertFalse(grade('{"vulnerable": true, "vuln_type": "sql_injection"}', self.spec).passed)
        self.assertFalse(grade('{"vulnerable": true, "vuln_type": "xss"}', self.spec).passed)
        
    def test_regrade_flips_format_only_failures_and_leaves_blocks_alone(self):
        import contextlib, io
        from evalkit.cli import main
        d = Path(tempfile.mkdtemp())
        (d / "tasks.jsonl").write_text(json.dumps({"id": "t", "prompt": "p", "grader": self.spec}) + "\n")
        base = {"task_id": "t", "category": "c", "passed": False, "score": 0.0, "details": "old",
                "model_error": None, "grader_error": None, "truncated": False, "refused": False}
        rows = [
            {**base, "key": "m|t|0", "model": "m", "sample": 0, "blocked": False,
             "output": '{"vulnerable": true, "vuln_type": "Path Traversal"}'},
            {**base, "key": "m|t|1", "model": "m", "sample": 1, "blocked": True, "output": ""},
        ]
        run = d / "run"
        run.mkdir()
        (run / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["regrade", str(run), "--tasks", str(d / "tasks.jsonl"), "--out", str(d / "out")])
        self.assertEqual(code, 0)
        out = load_results(d / "out" / "results.jsonl")
        self.assertTrue(out["m|t|0"]["passed"])
        self.assertTrue(out["m|t|0"]["regraded"])
        self.assertTrue(out["m|t|1"]["blocked"] and not out["m|t|1"]["passed"])
        self.assertIn("'fail->pass': 1", buf.getvalue())


class AblationFile(unittest.TestCase):
    def test_ablation_tasks_validate_and_reproduce(self):
        import subprocess
        import sys
        tasks = load_tasks(ROOT / "tasks" / "ablation_trigger.jsonl")
        self.assertEqual(len(tasks), 7)
        self.assertEqual(validate_tasks(tasks), [])
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_ablation_tasks.py"), "--check"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Merge(unittest.TestCase):
    def _rec(self, model, task, sample, ts, passed=True, **kw):
        base = {"key": f"{model}|{task}|{sample}", "model": model, "task_id": task, "sample": sample, "category": "c",
                "output": f"out-{ts}", "passed": passed, "score": 1.0 if passed else 0.0, "details": "",
                "model_error": None, "grader_error": None, "truncated": False, "blocked": False, "refused": False,
                "temperature": 0.0, "max_tokens": 16000, "system": None, "ts": ts}
        base.update(kw)
        return base

    def _write(self, rows):
        d = Path(tempfile.mkdtemp())
        (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return str(d)

    def test_merge_renumbers_samples_drops_errors_and_duplicates(self):
        import contextlib, io
        from evalkit.cli import main
        r1 = [self._rec("m", "t", 0, "2026-11-01T00:00:00"), self._rec("m", "t", 1, "2026-11-01T00:00:01"),
              self._rec("m", "t", 2, "2026-11-01T00:00:02", model_error="HTTP 402")]
        r2 = [self._rec("m", "t", 0, "2026-11-02T00:00:00", passed=False),      # same sample index as r1, different trial
              self._rec("m", "t", 1, "2026-11-01T00:00:01")]                    # exact duplicate of r1's second record
        out = Path(tempfile.mkdtemp()) / "final"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["merge", self._write(r1), self._write(r2), "--out", str(out)])
        self.assertEqual(code, 0)
        merged = load_results(out / "results.jsonl")
        self.assertEqual(sorted(merged), ["m|t|0", "m|t|1", "m|t|2"])   # 3 distinct trials
        self.assertEqual(sum(1 for r in merged.values() if not r["passed"]), 1)
        self.assertIn("1 infrastructure errors, 1 duplicates", buf.getvalue())

    def test_merge_warns_on_mixed_settings_and_filters_by_tasks_file(self):
        import contextlib, io
        from evalkit.cli import main
        d = Path(tempfile.mkdtemp())
        keep = {"id": "t", "prompt": "p", "grader": {"type": "exact_match", "expected": "x"}}
        (d / "tasks.jsonl").write_text(json.dumps(keep) + "\n")
        rows = [self._rec("m", "t", 0, "2026-11-01T00:00:00"),
                self._rec("m", "t", 1, "2026-11-01T00:00:01", max_tokens=4000),
                self._rec("m", "other", 0, "2026-11-01T00:00:02")]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["merge", self._write(rows), "--out", str(d / "out"), "--tasks", str(d / "tasks.jsonl")])
        text = buf.getvalue()
        self.assertIn("mixed settings", text)
        self.assertIn("1 outside --tasks", text)
        self.assertNotIn("other", load_results(d / "out" / "results.jsonl").__repr__())


if __name__ == "__main__":
    unittest.main()
