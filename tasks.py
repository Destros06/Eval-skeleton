"""Task loading + validation. Tasks live in JSONL: one JSON object per line."""
from __future__ import annotations

import json
from dataclasses import fields as dc_fields
from pathlib import Path

from .graders import GRADERS, grade
from .schema import Task


class TaskFileError(ValueError):
    pass


_ALLOWED = {f.name for f in dc_fields(Task)}
_REQUIRED = ("id", "prompt", "grader")


def load_tasks(path: str | Path) -> list[Task]:
    tasks: list[Task] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            where = f"{path}:{n}"
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise TaskFileError(f"{where}: invalid JSON ({e})") from e
            missing = [k for k in _REQUIRED if k not in raw]
            if missing:
                raise TaskFileError(f"{where}: missing keys {missing}")
            unknown = set(raw) - _ALLOWED  # catches typos like "metadat"
            if unknown:
                raise TaskFileError(f"{where}: unknown keys {sorted(unknown)}")
            if raw["id"] in seen:
                raise TaskFileError(f"{where}: duplicate task id {raw['id']!r}")
            gtype = raw["grader"].get("type")
            if gtype not in GRADERS:
                raise TaskFileError(f"{where}: unknown grader type {gtype!r}")
            seen.add(raw["id"])
            tasks.append(Task(**raw))
    if not tasks:
        raise TaskFileError(f"{path}: no tasks found")
    return tasks


def validate_tasks(tasks: list[Task]) -> list[str]:
    """Grader self-check. Returns a list of problems (empty == all good).

    - `reference` must PASS      -> catches false fails (grader too strict / buggy)
    - every `known_bad` must FAIL -> catches false passes (grader too lenient)
    """
    problems: list[str] = []
    for t in tasks:
        if t.reference is None:
            problems.append(f"{t.id}: no `reference` answer (add one so the grader can be sanity-checked)")
        else:
            try:
                r = grade(t.reference, t.grader)
                if not r.passed:
                    problems.append(f"{t.id}: reference answer FAILED its own grader ({r.details})")
            except Exception as e:  # noqa: BLE001
                problems.append(f"{t.id}: grader crashed on reference ({type(e).__name__}: {e})")
        for i, bad in enumerate(t.known_bad):
            try:
                if grade(bad, t.grader).passed:
                    problems.append(f"{t.id}: known_bad[{i}] was ACCEPTED by the grader (false pass)")
            except Exception as e:  # noqa: BLE001
                problems.append(f"{t.id}: grader crashed on known_bad[{i}] ({type(e).__name__}: {e})")
    return problems
