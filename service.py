# service.py
from names import NAME_RE, RESERVED, canonical

USERS = ["alice", "strasse", "finn"]  # already registered


def register(raw: str) -> str:
    if not NAME_RE.match(raw):
        return "invalid"
    key = canonical(raw)
    if key in RESERVED:
        return "reserved"
    if key in {canonical(u) for u in USERS}:
        return "taken"
    USERS.append(raw)
    return "created"
