# g6
# Threat model: `name` is attacker-controlled. The function must never read a file outside /srv/attachments.
import os

BASE = "/srv/attachments"


def read_attachment(name):
    name = os.path.normpath(name)
    if name.startswith(".."):
        raise PermissionError("path traversal")
    with open(os.path.join(BASE, name), "rb") as f:
        return f.read()
