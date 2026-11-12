# g8
# Threat model: `name` is attacker-controlled. The function must never read a file outside /srv/assets.
from pathlib import Path

BASE = Path("/srv/assets")


def fetch_asset(name):
    path = (BASE / name).resolve()
    if not path.is_relative_to(BASE.resolve()):
        raise PermissionError("outside assets directory")
    return path.read_bytes()
