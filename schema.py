from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Task:
    """One eval item.

    grader:     spec dict, e.g. {"type": "json_fields", "fields": {...}}
    reference:  an output the grader MUST pass (sanity check for your grader)
    known_bad:  outputs the grader MUST fail (sanity check against false passes)
    """

    id: str
    prompt: str
    grader: dict
    system: str | None = None
    metadata: dict = field(default_factory=dict)
    reference: str | None = None
    known_bad: list[str] = field(default_factory=list)


@dataclass
class GradeResult:
    passed: bool
    score: float  # 0.0 - 1.0, useful for partial credit / debugging
    details: str = ""


@dataclass
class Completion:
    """What a model call returned. Models may also just return a plain str."""

    text: str
    finish_reason: str | None = None   # "stop" | "length" | ...
    usage: dict | None = None          # provider token counts, when available
    reasoning_chars: int = 0           # size of hidden reasoning returned separately, if any
