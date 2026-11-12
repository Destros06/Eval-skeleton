# names.py
import re
import unicodedata

NAME_RE = re.compile(r"^\w{3,16}$")
RESERVED = {"admin", "root", "support"}


def canonical(name: str) -> str:
    """Form used to compare names for uniqueness."""
    return unicodedata.normalize("NFKC", name).casefold()
