# fetcher.py
from validators import is_allowed


def effective_host(url: str) -> str:
    """The host our HTTP client really connects to. It follows browser rules,
    so a backslash counts as a slash."""
    url = url.replace("\\", "/")
    rest = url.split("://", 1)[1]
    authority = rest.split("/", 1)[0]
    host = authority.rsplit("@", 1)[-1]
    return host.split(":", 1)[0].lower()


def connect(host: str) -> str:
    """Open a TCP connection to `host` (details omitted) and return the host."""
    return host


def fetch(url: str) -> str:
    if not is_allowed(url):
        raise PermissionError("host not allowed")
    return connect(effective_host(url))
