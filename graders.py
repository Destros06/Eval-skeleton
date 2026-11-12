"""Deterministic graders.

Rule of thumb: prefer graders that check *facts* (parsed fields, test results,
final system state) over graders that judge *prose*. Every grader has the same
signature, (model_output: str, spec: dict) -> GradeResult, and is registered by
name so tasks can reference it from JSONL.

Add your own with the @register decorator.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable

from .schema import GradeResult

GraderFn = Callable[[str, dict], GradeResult]
GRADERS: dict[str, GraderFn] = {}


def register(name: str) -> Callable[[GraderFn], GraderFn]:
    def deco(fn: GraderFn) -> GraderFn:
        if name in GRADERS:
            raise ValueError(f"grader {name!r} already registered")
        GRADERS[name] = fn
        return fn

    return deco


def grade(output: str, spec: dict) -> GradeResult:
    gtype = spec.get("type")
    if gtype not in GRADERS:
        raise KeyError(f"unknown grader type {gtype!r}; available: {sorted(GRADERS)}")
    return GRADERS[gtype](output, spec)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def _norm_label(s: str) -> str:
    """Normalização puramente de FORMATO (sem sinônimos)."""
    s = _PAREN.sub(" ", s)
    s = _SEP.sub(" ", s)
    s = _PREFIX.sub("", s)
    s = _SUFFIX.sub("", s)
    return _WS.sub(" ", s).strip().casefold()

def _canonical_label(s: str) -> str:
    norm = _norm_label(s)
    for canon, alts in _SYNONYMS.items():
        canon_norm = _norm_label(canon)
        if norm == canon_norm:
            return canon_norm
        if any(norm == _norm_label(a) for a in alts):
            return canon_norm
    return norm


def extract_json(text: str) -> dict | None:
    """Return the LAST top-level JSON object found in `text`, or None.

    Last (not first) because models often think out loud or show an example
    before giving their final answer. Works with ```json fences, prose before or
    after the object, and nested objects.
    """
    decoder = json.JSONDecoder()
    found: dict | None = None
    i = 0
    while True:
        i = text.find("{", i)
        if i == -1:
            return found
        try:
            obj, end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            found = obj
        i += max(end, 1)


def extract_code(text: str, entrypoint: str | None = None) -> str:
    """Pull Python code out of a model answer (fenced block, else raw text)."""
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if entrypoint:
        for b in blocks:
            if re.search(rf"def\s+{re.escape(entrypoint)}\b", b):
                return b
    return blocks[0] if blocks else text


def _eq(value: Any, expected: Any) -> bool:
    # (mantém o resto igual)
    if isinstance(expected, bool) or isinstance(value, bool):
        return isinstance(expected, bool) and isinstance(value, bool) and value == expected
    if isinstance(expected, (int, float)) and isinstance(value, str):
        try:    return float(value.strip()) == float(expected)
        except ValueError: return False
    if isinstance(expected, str) and isinstance(value, str):
        return _canonical_label(value) == _canonical_label(expected)
    return value == expected


def _match_rule(value: Any, rule: Any) -> tuple[bool, str]:
    """Rules: equals | in | set_equals | contains_all | regex (combine freely)."""
    if not isinstance(rule, dict):
        rule = {"equals": rule}
    checks: list[tuple[str, bool]] = []
    if "equals" in rule:
        checks.append(("equals", _eq(value, rule["equals"])))
    if "in" in rule:
        checks.append(("in", any(_eq(value, x) for x in rule["in"])))
    if "set_equals" in rule:
        ok = isinstance(value, list) and (
            {_norm_label(str(v)) for v in value} == {_norm_label(str(v)) for v in rule["set_equals"]}
        )
        checks.append(("set_equals", ok))
    if "contains_all" in rule:
        have = {_norm_label(str(v)) for v in value} if isinstance(value, list) else set()
        checks.append(("contains_all", {_norm_label(str(v)) for v in rule["contains_all"]} <= have))
    if "regex" in rule:
        checks.append(("regex", isinstance(value, str) and re.search(rule["regex"], value) is not None))
    if not checks:
        return False, "empty rule"
    failed = [name for name, ok in checks if not ok]
    return (not failed), ("" if not failed else f"failed {failed}")


# --------------------------------------------------------------------------- #
# graders
# --------------------------------------------------------------------------- #

@register("exact_match")
def exact_match(output: str, spec: dict) -> GradeResult:
    """spec: {"expected": str | [str, ...]}  (whitespace/case-insensitive)"""
    expected = spec["expected"]
    options = expected if isinstance(expected, list) else [expected]
    ok = _norm_text(output) in {_norm_text(o) for o in options}
    return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"got {output[:120]!r}")


@register("regex")
def regex(output: str, spec: dict) -> GradeResult:
    """spec: {"pattern": str, "flags": "i"|"s"|"m"|..., "negate": bool}"""
    flags = 0
    for ch in spec.get("flags", ""):
        flags |= {"i": re.I, "s": re.S, "m": re.M}[ch]
    hit = re.search(spec["pattern"], output, flags) is not None
    ok = hit != bool(spec.get("negate", False))
    return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else "pattern check failed")


@register("json_fields")
def json_fields(output: str, spec: dict) -> GradeResult:
    """Parse the model's JSON answer and check each field.

    spec: {"fields": {"vulnerable": true,
                      "vuln_type": {"in": ["sql_injection"]},
                      "line": {"in": [2, 3]}}}
    The task passes only if EVERY field matches; score = fraction matched.
    """
    obj = extract_json(output)
    if obj is None:
        return GradeResult(False, 0.0, "no JSON object found in output")
    fields: dict = spec["fields"]
    problems = []
    for key, rule in fields.items():
        if key not in obj:
            problems.append(f"{key}: missing")
            continue
        ok, why = _match_rule(obj[key], rule)
        if not ok:
            problems.append(f"{key}: got {obj[key]!r} ({why})")
    score = (len(fields) - len(problems)) / max(len(fields), 1)
    return GradeResult(not problems, score, "; ".join(problems))


@register("python_tests")
def python_tests(output: str, spec: dict) -> GradeResult:
    """Run model-written Python against your test code (exit code 0 == pass).

    spec: {"tests": "<python source appended after the solution>",
           "entrypoint": "safe_join", "timeout": 10}

    WARNING: this executes model-generated code. It uses a subprocess, isolated
    mode (-I), a temp dir, a scrubbed environment (no API keys) and a timeout,
    but it is NOT a sandbox. For anything riskier than pure functions, run the
    whole eval inside a container with no network (e.g. `docker run --network none`).
    """
    code = extract_code(output, spec.get("entrypoint"))
    has_fence = re.search(r"```", output) is not None
    if not has_fence:
        try:
            ast.parse(code)
        except SyntaxError:
            return GradeResult(False, 0.0, "no ```python block found and the output is not valid Python")
    timeout = float(spec.get("timeout", 10))
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if k in os.environ}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "solution_and_tests.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code + "\n\n# ---- tests ----\n" + spec["tests"] + "\n")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", path],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return GradeResult(False, 0.0, f"timeout after {timeout}s")
    ok = proc.returncode == 0
    tail = (proc.stderr or proc.stdout or "").strip()[-400:]
    return GradeResult(ok, 1.0 if ok else 0.0, "" if ok else f"exit {proc.returncode}: {tail}")

_PAREN   = re.compile(r"\s*\([^)]*\)")                       # "(XSS)", "(CSRF)", ...
_SEP     = re.compile(r"[_\-/]+")                            # "_", "-", "/" -> espaço
_PREFIX  = re.compile(r"^(?:cwe-\d+:\s*|reflected\s+|stored\s+|dom\s+|persistent\s+)", re.I)
_SUFFIX  = re.compile(r"\s+(?:attack|vulnerability)$", re.I)
_WS      = re.compile(r"\s+")

_SYNONYMS: dict[str, list[str]] = {
    "xss": ["cross site scripting", "cross site script", "cross site scripting attack"],
    "csrf": ["cross site request forgery", "xsrf", "cross site forgery"],
    "ssrf": ["server side request forgery"],
    "sqli": ["sql injection", "sql injection attack"],
    "idor": ["insecure direct object reference", "broken object level authorization", "bola"],
    "xxe": ["xml external entity", "xml external entity injection", "xml entity injection"],
    "path_traversal": ["directory traversal", "path traversal attack", "directory traversal attack"],
    "command_injection": ["os command injection", "shell injection", "command injection"],
    "insecure_deserialization": [
        "deserialization", "unsafe deserialization", "insecure deserialization attack",
        "unsafe deserialization leading to arbitrary code execution",
        "insecure deserialization leading to arbitrary code execution",
        "deserialization of untrusted data",
    ],
    "open_redirect": ["url redirection", "unvalidated redirect", "open url redirect"],
    "weak_crypto": ["weak cryptography", "insecure hashing", "broken crypto", "weak hash", "insecure hash algorithm"],
    "weak_randomness": [
        "insecure randomness", "predictable randomness", "weak prng", "predictable token generation",
        "cryptographically weak pseudo random number generator",
        "use of cryptographically weak pseudo random number generator",
    ],
    "hardcoded_secret": [
        "hardcoded credential", "hard coded secret", "hard coded credential",
        "hardcoded secret weak token signing", "hard coded cryptographic secret",
    ],
}