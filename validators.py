# validators.py
from urllib.parse import urlparse

ALLOWED_HOSTS = {"images.example.com", "cdn.example.com"}


def is_allowed(url: str) -> bool:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        return False
    return parts.hostname in ALLOWED_HOSTS
