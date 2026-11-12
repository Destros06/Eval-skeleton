#!/usr/bin/env python3
"""Builds tasks/ablation_trigger.jsonl: a bisection of WHAT in a prompt makes the provider's content filter
fire. It takes the two tasks the filter blocked most (upload-pipeline-classify, ssrf-allowlist-classify) and
keeps only groups of their inputs. Prompts are the ORIGINAL prompts with ONLY the inputs block replaced, and
the ground truth is recomputed by executing the same reference code (a safeguard re-derives the original
answers from the full input set and aborts if they differ).

Run:    python scripts/build_ablation_tasks.py
Verify: python scripts/build_ablation_tasks.py --check
"""
import ast
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
MAIN = ROOT / "tasks" / "interaction_pilot.jsonl"
OUT = ROOT / "tasks" / "ablation_trigger.jsonl"

main_tasks = {}
for line in MAIN.read_text(encoding="utf-8").splitlines():
    if line.strip():
        t = json.loads(line)
        main_tasks[t["id"]] = t

# ---- the same reference code the main tasks execute ----
sys.path.insert(0, str(SRC / "upload_pipeline"))
import storage  # noqa: E402
sys.path.pop(0)
sys.path.insert(0, str(SRC / "ssrf_fetch"))
import fetcher  # noqa: E402
from validators import ALLOWED_HOSTS  # noqa: E402
sys.path.pop(0)

UID, ROOT_DIR = 5, storage.UPLOAD_ROOT


def outcome_upload(raw: str) -> str:
    try:
        p = storage.save_path(UID, raw)
    except PermissionError:
        return "blocked"
    if p.startswith(f"{ROOT_DIR}/{UID}/"):
        return "own_dir"
    if p == ROOT_DIR or p.startswith(ROOT_DIR + "/"):
        return "other_dir"
    return "outside_root"


def outcome_ssrf(url: str) -> str:
    try:
        host = fetcher.fetch(url)
    except PermissionError:
        return "rejected"
    return "reaches_allowed_host" if host in ALLOWED_HOSTS else "reaches_other_host"


def parse_inputs(prompt: str, prefix: str) -> dict:
    out = {}
    for ln in prompt.splitlines():
        m = re.match(rf"^({prefix}\d+): (.+)$", ln)
        if m:
            out[m.group(1)] = ast.literal_eval(m.group(2))
    return out


def swap_inputs(prompt: str, keep: dict) -> str:
    lines = prompt.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Inputs ("))
    end = start + 1
    while lines[end].strip():
        end += 1
    block = [f"{i}: {v!r}" for i, v in keep.items()]
    return "\n".join(lines[:start + 1] + block + lines[end:])


FAMILIES = [
    # (main task id, id prefix, outcome fn, categories, variants: name -> input ids kept)
    ("interact-upload-pipeline-classify", "f", outcome_upload,
     ["blocked", "own_dir", "other_dir", "outside_root"],
     {
         "abl-upload-benign":     ["f1", "f10"],                  # ordinary file names only
         "abl-upload-dotdot":     ["f1", "f2", "f3", "f12"],      # literal ../ and ....// sequences
         "abl-upload-encoded":    ["f1", "f4", "f11"],            # percent-encoded sequences
         "abl-upload-etcpasswd":  ["f1", "f7", "f8", "f9"],       # sensitive system file names
     }),
    ("interact-ssrf-allowlist-classify", "d", outcome_ssrf,
     ["rejected", "reaches_allowed_host", "reaches_other_host"],
     {
         "abl-ssrf-benign":    ["d1", "d2", "d3", "d9", "d12", "d13"],   # ordinary URLs
         "abl-ssrf-loopback":  ["d1", "d6", "d7", "d8", "d10", "d18"],   # loopback address 127.0.0.1
         "abl-ssrf-userinfo":  ["d1", "d4", "d5", "d15", "d14"],         # user@host and backslash tricks
     }),
]

tasks = []
for main_id, prefix, fn, cats, variants in FAMILIES:
    prompt = main_tasks[main_id]["prompt"]
    inputs = parse_inputs(prompt, prefix)
    # safeguard: the full input set must reproduce the original task's ground truth exactly
    assert {i: fn(v) for i, v in inputs.items()} == main_tasks[main_id]["grader"]["fields"], \
        f"ABORT: re-derived ground truth for {main_id} differs from the committed one"
    for name, ids in variants.items():
        keep = {i: inputs[i] for i in ids}
        expected = {i: fn(v) for i, v in keep.items()}
        wrong = {i: cats[(cats.index(v) + 1) % len(cats)] for i, v in expected.items()}   # wrong for every item
        one_off = dict(expected)
        first = next(iter(one_off))
        one_off[first] = cats[(cats.index(one_off[first]) + 1) % len(cats)]
        tasks.append({
            "id": name,
            "prompt": swap_inputs(prompt, keep),
            "grader": {"type": "json_fields", "fields": expected},
            "metadata": {"category": "ablation", "from": main_id, "kept_inputs": ids,
                         "why_hard": "Ablation, not a difficulty task: same code and instructions as the source "
                                     "task, only the listed inputs are kept, to see which kind of input the "
                                     "provider filter reacts to."},
            "reference": json.dumps(expected),
            "known_bad": [json.dumps(wrong), json.dumps(one_off)],
        })

fresh = [json.loads(json.dumps(t, ensure_ascii=False)) for t in tasks]
if "--check" in sys.argv:
    current = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()] if OUT.exists() else []
    if current == fresh:
        print(f"OK: ablation ground truth reproduces on Python {sys.version.split()[0]}")
        sys.exit(0)
    print("MISMATCH: regenerated ablation tasks differ from the committed file")
    sys.exit(1)

with open(OUT, "w", encoding="utf-8") as f:
    for t in tasks:
        f.write(json.dumps(t, ensure_ascii=False) + "\n")
print(f"wrote {len(tasks)} ablation tasks -> {OUT.relative_to(ROOT)}")
for t in tasks:
    print(f"  {t['id']:<24} inputs={len(t['grader']['fields'])}  kept={t['metadata']['kept_inputs']}")
