# storage.py
import posixpath
from urllib.parse import unquote

from utils.filenames import clean_name

UPLOAD_ROOT = "/srv/app/uploads"


def save_path(user_id: int, raw_name: str) -> str:
    """Absolute path where `raw_name` (exactly as received in the URL, still
    percent-encoded) is stored for `user_id`."""
    name = unquote(clean_name(raw_name))
    path = posixpath.normpath(posixpath.join(UPLOAD_ROOT, str(user_id), name))
    if not path.startswith(UPLOAD_ROOT):
        raise PermissionError("outside upload root")
    return path
