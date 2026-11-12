"""Read results.jsonl and print summaries."""
from __future__ import annotations

import json
from math import sqrt
from pathlib import Path


def load_results(path: str | Path) -> dict[str, dict]:
    """key -> record. If a key appears twice (e.g. a retried error) the LAST one wins."""
    out: dict[str, dict] = {}
    p = Path(path)
    if not p.exists():
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                out[rec["key"]] = rec
    return out


def _is_graded(r: dict) -> bool:
    """Counts toward pass rate: the model answered fully and the grader ran."""
    return (not r.get("model_error") and not r.get("grader_error")
            and not r.get("truncated") and not r.get("blocked") and not r.get("refused"))


def _rate(recs: list[dict]) -> str:
    graded = [r for r in recs if _is_graded(r)]
    if not graded:
        return "  n/a"
    return f"{100 * sum(r['passed'] for r in graded) / len(graded):5.1f}%"


def _avg_out_tokens(recs: list[dict]) -> str:
    vals = [r["usage"]["completion_tokens"] for r in recs
            if isinstance(r.get("usage"), dict) and r["usage"].get("completion_tokens") is not None]
    return f"{sum(vals) / len(vals):.0f}" if vals else "-"


def _table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    fmt = lambda row: "  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip()  # noqa: E731
    return "\n".join([fmt(header), fmt(["-" * w for w in widths]), *[fmt(r) for r in rows]])


def _usage_table(recs: list[dict], models: list[str]) -> str | None:
    """Per-model token usage and cost, from the `usage` block providers return."""
    rows = []
    any_usage = False
    for m in models:
        mine = [r for r in recs if r["model"] == m and isinstance(r.get("usage"), dict)]
        if not mine:
            rows.append([m, "0", "-", "-", "-", "-"])
            continue
        any_usage = True
        outs = [u["completion_tokens"] for u in (r["usage"] for r in mine)
                if u.get("completion_tokens") is not None]
        thinks = [(u.get("completion_tokens_details") or {}).get("reasoning_tokens")
                  for u in (r["usage"] for r in mine)]
        thinks = [t for t in thinks if t is not None]
        cost = sum(float(r["usage"].get("cost") or 0) for r in mine)
        lat = [r["latency_s"] for r in mine if r.get("latency_s") is not None]
        rows.append([
            m, str(len(mine)),
            f"{sum(outs) / len(outs):.0f}" if outs else "-",
            f"{sum(thinks) / len(thinks):.0f}" if thinks else "-",
            f"${cost:.4f}",
            f"{sum(lat) / len(lat):.0f}s" if lat else "-",
        ])
    if not any_usage:
        return None
    return _table(["model", "calls", "avg_out_tok", "avg_think_tok", "cost_usd", "avg_latency"], rows)


def _consistency_table(recs: list[dict], models: list[str]) -> str | None:
    """Per model: average pass rate, pass@n (>=1 of n trials passes) and pass^n (ALL n pass),
    averaged over tasks. Only tasks with >= 2 graded trials count (needs --samples >= 2)."""
    rows, any_multi = [], False
    for m in models:
        per_task: dict[str, list[bool]] = {}
        for r in recs:
            if r["model"] == m and _is_graded(r):
                per_task.setdefault(r["task_id"], []).append(bool(r["passed"]))
        multi = {t: v for t, v in per_task.items() if len(v) >= 2}
        if not multi:
            continue
        any_multi = True
        ns = [len(v) for v in multi.values()]
        n_txt = str(ns[0]) if min(ns) == max(ns) else f"{min(ns)}-{max(ns)}"
        k = len(multi)
        avg = sum(sum(v) / len(v) for v in multi.values()) / k
        at_least_one = sum(any(v) for v in multi.values()) / k
        all_pass = sum(all(v) for v in multi.values()) / k
        rows.append([m, str(k), n_txt, f"{100 * avg:.1f}%", f"{100 * at_least_one:.1f}%", f"{100 * all_pass:.1f}%"])
    if not any_multi:
        return None
    return _table(["model", "tasks", "trials/task", "avg_pass", "pass@n", "pass^n"], rows)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials (honest about small samples)."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _availability_tables(recs: list[dict], models: list[str], tasks: list[str]) -> list[str]:
    """Non-completion = blocked by the provider's filter or refused in plain language.
    It is NOT a wrong answer, so it is reported separately and kept out of the pass rate."""
    rows, any_nc = [], False
    for m in models:
        mine = [r for r in recs if r["model"] == m and not r.get("model_error")]
        blocked = sum(1 for r in mine if r.get("blocked"))
        refused = sum(1 for r in mine if r.get("refused"))
        any_nc = any_nc or bool(blocked or refused)
        nc = blocked + refused
        lo, hi = wilson(nc, len(mine))
        rows.append([m, str(len(mine)), str(blocked), str(refused),
                     f"{100 * nc / len(mine):.1f}%" if mine else "n/a",
                     f"{100 * lo:.1f}%-{100 * hi:.1f}%" if mine else "n/a"])
    if not any_nc:
        return []
    out = ["", "== Availability: non-completion = blocked by provider filter + refused (not wrong answers) ==",
           _table(["model", "calls", "blocked", "refused", "non_completion", "95% interval"], rows)]
    trows = []
    for t in tasks:
        cells, hit = [], False
        for m in models:
            mine = [r for r in recs if r["model"] == m and r["task_id"] == t and not r.get("model_error")]
            nc = sum(1 for r in mine if r.get("blocked") or r.get("refused"))
            hit = hit or nc > 0
            cells.append(f"{nc}/{len(mine)}" if mine else "-")
        if hit:
            trows.append([t, *cells])
    if trows:
        out += ["", "Non-completions by task (count/calls):", _table(["task", *models], trows)]
    return out


def format_report(results: dict[str, dict], show_failures: bool = False) -> str:
    recs = list(results.values())
    if not recs:
        return "(no results)"
    models = sorted({r["model"] for r in recs})
    tasks = sorted({r["task_id"] for r in recs})
    cats = sorted({r.get("category") or "uncategorized" for r in recs})
    parts: list[str] = []

    rows = []
    for m in models:
        mine = [r for r in recs if r["model"] == m]
        graded = [r for r in mine if _is_graded(r)]
        mean = sum(r["score"] for r in graded) / len(graded) if graded else 0.0
        rows.append([
            m, str(len(graded)), _rate(mine), f"{mean:.2f}",
            str(sum(1 for r in mine if r.get("truncated"))),
            str(sum(1 for r in mine if r.get("blocked"))),
            str(sum(1 for r in mine if r.get("refused"))),
            str(sum(1 for r in mine if r.get("model_error"))),
            str(sum(1 for r in mine if r.get("grader_error"))),
        ])
    parts += ["== Overall (pass rate over graded samples) ==",
              _table(["model", "graded", "pass", "mean_score", "truncated", "blocked", "refused", "model_err",
                      "grader_err"], rows)]

    parts += _availability_tables(recs, models, tasks)

    cons = _consistency_table(recs, models)
    if cons:
        parts += ["", "== Consistency over repeated trials (pass@n: at least one trial passes; "
                      "pass^n: every trial passes) ==", cons]

    usage = _usage_table(recs, models)
    if usage:
        parts += ["", "== Usage (tokens per call, hidden-thinking tokens, total cost of the calls recorded) ==", usage]

    if len(cats) > 1:
        rows = [[c, *[_rate([r for r in recs if r["model"] == m and (r.get("category") or "uncategorized") == c])
                      for m in models]] for c in cats]
        parts += ["", "== By category ==", _table(["category", *models], rows)]

    rows = [[t, *[_rate([r for r in recs if r["model"] == m and r["task_id"] == t]) for m in models]] for t in tasks]
    parts += ["", "== By task (a task every model passes -- or none can -- tells you little) ==",
              _table(["task", *models], rows)]

    bad = [r for r in recs if r.get("grader_error")]
    if bad:
        parts += ["", "!! GRADER ERRORS (fix these before trusting any numbers) !!"]
        parts += [f"  {r['task_id']} [{r['model']}]: {r['grader_error']}" for r in bad]
    trunc = [r for r in recs if r.get("truncated")]
    if trunc:
        parts += ["", "Truncated answers (budget ran out before the model finished; NOT counted as wrong -- "
                      "re-run those models with a larger --max-tokens):"]
        parts += [f"  {r['task_id']} [{r['model']}] max_tokens={r.get('max_tokens')} "
                  f"reasoning_chars={r.get('reasoning_chars', 0)}" for r in trunc]
    blocked = [r for r in recs if r.get("blocked")]
    if blocked:
        parts += ["", "Blocked by the provider's content filter (NOT counted as wrong; resuming keeps them; use --retry-blocked to re-roll):"]
        parts += [f"  {r['task_id']} [{r['model']}]" for r in blocked]
    errs = [r for r in recs if r.get("model_error")]
    if errs:
        parts += ["", "Model call errors (re-run the same command to retry them):"]
        parts += [f"  {r['task_id']} [{r['model']}]: {r['model_error'][:160]}" for r in errs]

    if show_failures:
        parts += ["", "== Failures =="]
        for r in sorted(recs, key=lambda r: (r["task_id"], r["model"])):
            if _is_graded(r) and not r["passed"]:
                snippet = r["output"].strip().replace("\n", " ")[:160]
                parts += [f"- {r['task_id']} [{r['model']}] {r['details']}", f"    output: {snippet}"]
    return "\n".join(parts)
