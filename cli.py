from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .models import ModelError, as_completion, get_model
from .report import format_report, load_results
from .runner import run_eval
from .tasks import TaskFileError, load_tasks, validate_tasks


def _read_text_any(p: Path) -> str:
    """Read a text file saved by any common Windows/Unix editor (UTF-8, UTF-8 with BOM, UTF-16)."""
    raw = p.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env reader so we don't need python-dotenv.

    A real, NON-EMPTY environment variable wins over the file; an empty one does not.
    """
    p = Path(path)
    if not p.is_file():
        return
    for line in _read_text_any(p).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k and v and not os.environ.get(k):
            os.environ[k] = v


def _dotenv_hint() -> str:
    """Explain WHY the key may be missing (wrong file name, wrong folder, empty value)."""
    cwd = Path.cwd()
    if (cwd / ".env").is_file():
        return (f"Found {cwd / '.env'} but OPENROUTER_API_KEY has no value in it. "
                "The line must look like: OPENROUTER_API_KEY=sk-or-... (no spaces, no quotes needed).")
    lookalikes = [f.name for f in cwd.glob(".env*") if f.name != ".env.example"]
    if lookalikes:
        return (f"No '.env' file here, but found {lookalikes}. Rename it to exactly '.env' "
                "(Windows Notepad often saves it as '.env.txt').")
    return (f"No '.env' file in {cwd}. Create it in THIS folder (copy .env.example to .env), "
            "or set the key for this terminal only:  $env:OPENROUTER_API_KEY=\"sk-or-...\"  (PowerShell)")


def _cmd_validate(args) -> int:
    try:
        tasks = load_tasks(args.tasks)
    except TaskFileError as e:
        print(f"ERROR: {e}")
        return 2
    problems = validate_tasks(tasks)
    print(f"{len(tasks)} task(s) loaded.")
    for p in problems:
        print(f"  - {p}")
    print("Grader sanity check: " + ("OK" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


def _cmd_run(args) -> int:
    _load_dotenv()
    try:
        tasks = load_tasks(args.tasks)
    except TaskFileError as e:
        print(f"ERROR: {e}")
        return 2
    problems = validate_tasks(tasks)
    if problems and not args.skip_validation:
        print("Refusing to run: grader sanity check failed (use --skip-validation to override):")
        for p in problems:
            print(f"  - {p}")
        return 1

    if args.only:
        wanted = [w.lower() for w in args.only]
        tasks = [t for t in tasks if any(w in t.id.lower() for w in wanted)]
        if not tasks:
            print(f"ERROR: --only {args.only} matched no task id")
            return 2
        print(f"Running only {len(tasks)} task(s): {[t.id for t in tasks]}")

    out_dir = Path(args.out or f"results/{Path(args.tasks).stem}_{datetime.now():%Y%m%d_%H%M%S}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(json.dumps({
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tasks_file": str(args.tasks),
        "tasks_sha256": hashlib.sha256(Path(args.tasks).read_bytes()).hexdigest(),
        "models": args.models, "samples": args.samples,
        "temperature": args.temperature, "max_tokens": args.max_tokens,
        "python": sys.version.split()[0],
    }, indent=2))

    try:
        path = run_eval(tasks, args.models, out_dir, samples=args.samples, workers=args.workers,
                        system=args.system, temperature=args.temperature, max_tokens=args.max_tokens,
                        retry_blocked=args.retry_blocked)
    except (ModelError, ValueError) as e:  # bad model spec, missing API key, ...
        print(f"ERROR: {e}")
        if "OPENROUTER_API_KEY" in str(e):
            print("HINT: " + _dotenv_hint())
        return 2
    print("\n" + format_report(load_results(path), show_failures=args.show_failures))
    print(f"\nResults: {path}")
    return 0


def _cmd_report(args) -> int:
    p = Path(args.run_dir)
    p = p / "results.jsonl" if p.is_dir() else p
    print(format_report(load_results(p), show_failures=args.show_failures))
    return 0


def _cmd_probe(args) -> int:
    """One tiny request to see how a model behaves (costs ~1 call). Use it to debug MODEL_ERR."""
    _load_dotenv()
    try:
        model = get_model(args.model, temperature=0.0, max_tokens=args.max_tokens)
    except (ModelError, ValueError) as e:
        print(f"ERROR: {e}")
        if "OPENROUTER_API_KEY" in str(e):
            print("HINT: " + _dotenv_hint())
        return 2
    prompt = 'Reply with exactly this JSON and nothing else: {"ok": true}'
    try:
        comp = as_completion(model.complete(None, prompt))
    except ModelError as e:
        print(f"FAILED: {e}")
        return 1
    print(f"finish_reason   : {comp.finish_reason}")
    print(f"usage           : {comp.usage}")
    print(f"reasoning_chars : {comp.reasoning_chars}")
    print(f"visible text    : {comp.text[:200]!r}")
    if comp.finish_reason == "length":
        print("VERDICT: the model spent the whole --max-tokens budget before answering "
              "(usually hidden reasoning). Use a larger --max-tokens for it, or pick another model.")
    elif not comp.text.strip():
        print("VERDICT: empty visible answer with no 'length' stop: provider hiccup or unsupported model.")
    else:
        print("VERDICT: the model answers normally at this budget.")
    return 0


def _cmd_regrade(args) -> int:
    """Re-apply the CURRENT graders to outputs already stored (no API calls, no cost)."""
    from .runner import apply_grade
    try:
        tasks = {t.id: t for t in load_tasks(args.tasks)}
    except TaskFileError as e:
        print(f"ERROR: {e}")
        return 2
    src = Path(args.run_dir)
    src_file = src / "results.jsonl" if src.is_dir() else src
    recs = load_results(src_file)
    if not recs:
        print(f"ERROR: no results found in {src_file}")
        return 2
    out_dir = Path(args.out or (str(src_file.parent) + "_regraded"))
    out_dir.mkdir(parents=True, exist_ok=True)
    flips = {"fail->pass": 0, "pass->fail": 0}
    unchanged = skipped = 0
    with open(out_dir / "results.jsonl", "w", encoding="utf-8") as f:
        for rec in recs.values():
            task = tasks.get(rec["task_id"])
            gradable = not (rec.get("model_error") or rec.get("blocked") or rec.get("truncated"))
            if task is not None and gradable:
                before = bool(rec.get("passed"))
                apply_grade(rec, task)
                rec["regraded"] = True
                if before != bool(rec["passed"]):
                    flips["pass->fail" if before else "fail->pass"] += 1
                else:
                    unchanged += 1
            else:
                skipped += 1
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Regraded {len(recs)} records -> {out_dir / 'results.jsonl'}")
    print(f"  verdict changes: {flips}   unchanged: {unchanged}   not regradable (errors/blocked/other tasks): {skipped}")
    print("  NOTE: changing a grader AFTER seeing outputs is a researcher degree of freedom. Report it in the README.")
    print("\n" + format_report(load_results(out_dir / "results.jsonl")))
    return 0


def _cmd_merge(args) -> int:
    """Combine several run folders into one consolidated results file + a coverage matrix.

    Every record is an independent trial, so sample numbers are re-assigned per (model, task) in time order.
    Infrastructure errors are dropped (they are not outcomes). Exact duplicates are removed."""
    import hashlib
    from collections import defaultdict

    allowed = None
    if args.tasks:
        try:
            allowed = {t.id for t in load_tasks(args.tasks)}
        except TaskFileError as e:
            print(f"ERROR: {e}")
            return 2
    rows, seen, dropped_err, dropped_dup, dropped_task = [], set(), 0, 0, 0
    for run in args.runs:
        path = Path(run)
        path = path / "results.jsonl" if path.is_dir() else path
        recs = load_results(path)
        if not recs:
            print(f"WARNING: nothing found in {path}")
        for rec in recs.values():
            if rec.get("model_error"):
                dropped_err += 1
                continue
            if allowed is not None and rec["task_id"] not in allowed:
                dropped_task += 1
                continue
            ident = (rec["model"], rec["task_id"], rec.get("ts"), hashlib.sha1(rec.get("output", "").encode()).hexdigest())
            if ident in seen:
                dropped_dup += 1
                continue
            seen.add(ident)
            rows.append(rec)
    if not rows:
        print("ERROR: no usable records")
        return 2
    rows.sort(key=lambda r: (r["model"], r["task_id"], r.get("ts") or ""))
    counter: dict = defaultdict(int)
    for rec in rows:
        k = (rec["model"], rec["task_id"])
        rec["sample"] = counter[k]
        counter[k] += 1
        rec["key"] = f"{rec['model']}|{rec['task_id']}|{rec['sample']}"

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "results.jsonl", "w", encoding="utf-8") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Merged {len(rows)} trials -> {out_dir / 'results.jsonl'}   "
          f"(dropped: {dropped_err} infrastructure errors, {dropped_dup} duplicates, {dropped_task} outside --tasks)")

    # --- consistency warnings: only merge what is comparable ---
    warn = []
    for model in sorted({r["model"] for r in rows}):
        cfg = {(r.get("temperature"), r.get("max_tokens"), str(r.get("system"))) for r in rows if r["model"] == model}
        if len(cfg) > 1:
            warn.append(f"{model}: mixed settings (temperature, max_tokens, system) = {sorted(map(str, cfg))}")
    for task in sorted({r["task_id"] for r in rows}):
        shas = {r.get("prompt_sha") for r in rows if r["task_id"] == task and r.get("prompt_sha")}
        if len(shas) > 1:
            warn.append(f"{task}: the PROMPT changed between runs ({len(shas)} versions): do not mix them")
    for w in warn:
        print("  WARNING:", w)

    # --- coverage matrix: usable (graded) trials per cell; +Nb blocked, +Nt truncated, +Nr refused ---
    models = sorted({r["model"] for r in rows})
    tasks = sorted({r["task_id"] for r in rows})
    def cell(m, t):
        rs = [r for r in rows if r["model"] == m and r["task_id"] == t]
        if not rs:
            return "."
        g = sum(1 for r in rs if not (r.get("blocked") or r.get("truncated") or r.get("refused") or r.get("grader_error")))
        extra = "".join(f"+{n}{tag}" for tag, n in
                        (("b", sum(bool(r.get("blocked")) for r in rs)), ("t", sum(bool(r.get("truncated")) for r in rs)),
                         ("r", sum(bool(r.get("refused")) for r in rs))) if n)
        return f"{g}{extra}"
    short = lambda m: m.split("/")[-1][:13]
    print("\nCoverage (graded trials per cell; +b blocked, +t truncated, +r refused; '.' = no data):")
    print(f"{'task':<36}" + "".join(f"{short(m):>15}" for m in models))
    for t in tasks:
        print(f"{t:<36}" + "".join(f"{cell(m, t):>15}" for m in models))
    low = [(t, short(m)) for t in tasks for m in models if cell(m, t) in (".", "0") or
           (cell(m, t)[0].isdigit() and int(cell(m, t).split("+")[0]) < args.min_trials)]
    if low:
        print(f"\nCells with fewer than {args.min_trials} graded trials ({len(low)}): the gaps to fill, if budget allows.")
    print("\n" + format_report(load_results(out_dir / "results.jsonl")))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evalkit", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="load tasks and sanity-check graders (no model calls)")
    v.add_argument("--tasks", required=True)
    v.set_defaults(fn=_cmd_validate)

    r = sub.add_parser("run", help="run models on tasks, grade, and print a report")
    r.add_argument("--tasks", required=True)
    r.add_argument("--models", nargs="+", required=True, help="e.g. mock:naive openrouter:<model-id>")
    r.add_argument("--samples", type=int, default=1, help="samples per (model, task)")
    r.add_argument("--only", nargs="+", metavar="TEXT",
                   help="run only tasks whose id contains any of these texts (e.g. --only router signup)")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--out", help="results dir; pass the same dir again to RESUME a run")
    r.add_argument("--system", help="default system prompt (a task's own `system` wins)")
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--max-tokens", type=int, default=2048)
    r.add_argument("--show-failures", action="store_true")
    r.add_argument("--retry-blocked", action="store_true",
                   help="re-run calls that were blocked by the provider or refused (default: keep them as outcomes)")
    r.add_argument("--skip-validation", action="store_true")
    r.set_defaults(fn=_cmd_run)

    p = sub.add_parser("report", help="re-print the report for a finished run")
    p.add_argument("run_dir")
    p.add_argument("--show-failures", action="store_true")
    p.set_defaults(fn=_cmd_report)

    q = sub.add_parser("probe", help="send ONE tiny request to a model and show finish_reason/usage")
    q.add_argument("--model", required=True)
    q.add_argument("--max-tokens", type=int, default=4000)
    q.set_defaults(fn=_cmd_probe)

    g = sub.add_parser("regrade", help="re-apply the current graders to stored outputs (no API calls)")
    g.add_argument("run_dir")
    g.add_argument("--tasks", required=True)
    g.add_argument("--out", help="output dir (default: <run_dir>_regraded)")
    g.set_defaults(fn=_cmd_regrade)

    mg = sub.add_parser("merge", help="combine several run folders into one results file + coverage matrix")
    mg.add_argument("runs", nargs="+", help="run folders or results.jsonl files")
    mg.add_argument("--out", required=True)
    mg.add_argument("--tasks", help="keep only tasks that appear in this file (the frozen task list)")
    mg.add_argument("--min-trials", type=int, default=3)
    mg.set_defaults(fn=_cmd_merge)

    args = ap.parse_args(argv)
    return args.fn(args)
