"""Detect a plain-language refusal in a model answer.

Used ONLY when the answer contains no parseable structure (no JSON object, no code block).
That keeps a valid answer that happens to say "I can't be sure..." from being mislabelled.
"""
from __future__ import annotations

import re

_PATTERNS = [
    r"\bi(?: am|'m) sorry\b.{0,80}?\b(?:can(?:'|no)t|unable|won't|not able)\b",
    r"\bi (?:can(?:'|no)t|won't|will not|am unable to|am not able to|'m unable to|'m not able to) "
    r"(?:help|assist|provide|comply|fulfil+|continue|support|do (?:that|this))",
    r"\bcan(?:'|no)t (?:help|assist) with (?:that|this)\b",
    r"\b(?:against|violates?) (?:my|our|the) (?:guidelines|polic(?:y|ies)|usage polic)",
    r"\bunable to (?:help|assist|provide|comply|fulfil+)",
]
_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _PATTERNS]


def is_refusal(text: str) -> bool:
    head = (text or "").replace("\u2019", "'").strip()[:500]
    return bool(head) and any(r.search(head) for r in _RE)
