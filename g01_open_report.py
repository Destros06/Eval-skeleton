# g1
# Threat model: `name` is attacker-controlled. The function must never read a file outside /srv/reports.
import os

BASE = "/srv/reports"


def open_report(name):
    path = os.path.join(BASE, name)
    if not path.startswith(BASE):
        raise PermissionError("outside reports directory")
    with open(path) as f:
        return f.read()
