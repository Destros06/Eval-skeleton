# g10
# Threat model: `name` is attacker-controlled. The function must never read a file outside /srv/reports.
import os

BASE = "/srv/reports"


def load_report(name):
    path = os.path.realpath(os.path.join(BASE, name))
    if os.path.commonpath([BASE, path]) != BASE:
        raise PermissionError("outside reports directory")
    with open(path) as f:
        return f.read()
