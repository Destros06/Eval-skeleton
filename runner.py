"""Run (model x task x sample) jobs concurrently, grade them, append to results.jsonl.

Design choices worth keeping:
  * results.jsonl is append-only and every line is flushed -> crash-safe.
  * Re-running the same command RESUMES: finished jobs are skipped, jobs that
    errored are retried, truncated jobs are retried only with a LARGER --max-tokens.
  * A wrong answer, a model API failure, a grader crash, a truncated answer and a provider-blocked answer are
    five different things and are recorded separately. Only wrong answers count
    against a model.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .graders import grade
from .refusal import is_refusal
from .models import Model, as_completion, get_model
from .report import load_results, _is_graded
from .schema import Task


def job_key(model: str, task_id: str, sample: int) -> str:
    return f"{model}|{task_id}|{sample}"


def apply_grade(rec: dict, task: Task) -> None:
    """Grade rec['output'] against task.grader and update rec in place (shared by run and regrade)."""
    rec["grader_error"] = None
    rec["refused"] = False
    try:
        g = grade(rec["output"], task.grader)
        rec.update(passed=g.passed, score=g.score, details=g.details)
        structural_miss = ("no JSON object found" in g.details) or ("no ```python block" in g.details)
        if not g.passed and structural_miss and is_refusal(rec["output"]):
            rec["refused"] = True
            rec["details"] = "REFUSED: no answer and the text reads as a refusal; excluded from pass rate."
    except Exception as e:  # noqa: BLE001
        rec["grader_error"] = f"{type(e).__name__}: {e}"


def _run_job(model: Model, spec: str, task: Task, sample: int, default_system: str | None,
             params: dict) -> dict:
    rec = {
        "key": job_key(spec, task.id, sample),
        "model": spec,
        "task_id": task.id,
        "sample": sample,
        "category": task.metadata.get("category"),
        "output": "",
        "passed": False,
        "score": 0.0,
        "details": "",
        "model_error": None,
        "grader_error": None,
        "truncated": False,
        "blocked": False,
        "refused": False,
        "prompt_sha": hashlib.sha256(task.prompt.encode("utf-8")).hexdigest()[:12],
        "system": task.system or default_system,
        "finish_reason": None,
        "usage": None,
        "reasoning_chars": 0,
        "temperature": params["temperature"],
        "max_tokens": params["max_tokens"],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    t0 = time.monotonic()
    try:
        comp = as_completion(model.complete(task.system or default_system, task.prompt))
    except Exception as e:  # noqa: BLE001
        rec["model_error"] = f"{type(e).__name__}: {e}"
        rec["latency_s"] = round(time.monotonic() - t0, 2)
        return rec
    rec["latency_s"] = round(time.monotonic() - t0, 2)
    rec.update(output=comp.text, finish_reason=comp.finish_reason, usage=comp.usage,
               reasoning_chars=comp.reasoning_chars)
    if comp.finish_reason == "length":
        rec["truncated"] = True
        rec["details"] = ("TRUNCATED: the model hit --max-tokens before finishing "
                          "(excluded from pass rate). Re-run with a larger --max-tokens.")
        return rec
    if comp.finish_reason == "content_filter":
        rec["blocked"] = True
        rec["details"] = ("BLOCKED by the provider's content filter (finish_reason=content_filter); "
                          "excluded from pass rate. Report it separately.")
        return rec
    apply_grade(rec, task)
    return rec


def run_eval(
    tasks: list[Task],
    model_specs: list[str],
    out_dir: str | Path,
    samples: int = 1,
    workers: int = 4,
    system: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    model_factory: Callable[..., Model] = get_model,
    progress: Callable[[str], None] = print,
    retry_blocked: bool = False,
) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"

    done = set()
    for k, r in load_results(results_path).items():
        if _is_graded(r):
            done.add(k)
        elif (r.get("blocked") or r.get("refused")) and not retry_blocked:
            done.add(k)  # a block/refusal is an OUTCOME being measured: never silently re-roll it
        elif r.get("truncated") and (r.get("max_tokens") or 0) >= max_tokens:
            done.add(k)  # would just truncate again at this budget
    params = {"temperature": temperature, "max_tokens": max_tokens}
    models = {s: model_factory(s, temperature=temperature, max_tokens=max_tokens) for s in model_specs}
    jobs = [(s, t, k) for s in model_specs for t in tasks for k in range(samples)
            if job_key(s, t.id, k) not in done]
    progress(f"{len(jobs)} job(s) to run ({len(done)} already done) -> {results_path}")

    lock = threading.Lock()
    finished = 0
    with open(results_path, "a", encoding="utf-8") as sink, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_job, models[s], s, t, k, system, params) for s, t, k in jobs]
        for fut in as_completed(futures):
            rec = fut.result()
            with lock:
                sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sink.flush()
                finished += 1
            flag = ("MODEL_ERR" if rec["model_error"] else "GRADER_ERR" if rec["grader_error"]
                    else "TRUNCATED" if rec["truncated"] else "BLOCKED" if rec["blocked"]
                    else "REFUSED" if rec["refused"] else "pass" if rec["passed"] else "fail")
            progress(f"[{finished}/{len(jobs)}] {flag:<10} {rec['model']} :: {rec['task_id']}")
    return results_path
