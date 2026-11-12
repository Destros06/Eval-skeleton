#!/usr/bin/env python3
"""Builds tasks/interaction_pilot.jsonl.

Theme: failures that emerge from the INTERACTION between components (each file
looks reasonable alone; the bug lives in how they combine).

Ground truth is computed by EXECUTING the reference sources in src/, so the
answer key can never drift from the code shown to the model. Prompts embed the
source files verbatim.

Run:    python scripts/build_interaction_tasks.py          (regenerate tasks/interaction_pilot.jsonl)
Verify: python scripts/build_interaction_tasks.py --check  (does the committed ground truth reproduce on
        THIS Python? Standard-library behaviour can differ between versions; exit code 1 if it does not)
"""
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OUT = ROOT / "tasks" / "interaction_pilot.jsonl"


def code_block(rel: str) -> str:
    return "```python\n" + (SRC / rel).read_text(encoding="utf-8").rstrip() + "\n```"


def listing(items) -> str:
    return "\n".join(f"{i}: {v!r}" for i, v in items)


def listing_cp(items) -> str:
    """Like listing(), but spells out the code points of every non-ASCII character
    so that look-alike characters can never be ambiguous."""
    out = []
    for i, v in items:
        extra = [f"U+{ord(c):04X}" for c in v if ord(c) > 127]
        out.append(f"{i}: {v!r}" + (f"   # {' '.join(extra)}" if extra else ""))
    return "\n".join(out)


# =========================================================================== #
# Task A: classify what an upload-path pipeline really does (3 components)
# =========================================================================== #
sys.path.insert(0, str(SRC / "upload_pipeline"))
import storage  # noqa: E402

UID = 5
ROOT_DIR = storage.UPLOAD_ROOT


def outcome_a(raw: str) -> str:
    try:
        p = storage.save_path(UID, raw)
    except PermissionError:
        return "blocked"
    if p.startswith(f"{ROOT_DIR}/{UID}/"):
        return "own_dir"
    if p == ROOT_DIR or p.startswith(ROOT_DIR + "/"):
        return "other_dir"
    return "outside_root"


INPUTS_A = [
    ("f1", "report.pdf"),
    ("f2", "../6/secret.txt"),
    ("f3", "....//6/secret.txt"),
    ("f4", "..%2f6%2fsecret.txt"),
    ("f5", "%2e%2e%2f%2e%2e%2fetc%2fpasswd"),
    ("f6", "....//....//uploads-old/x.txt"),
    ("f7", "/etc/passwd"),
    ("f8", "%2Fetc%2Fpasswd"),
    ("f9", "a/../../../etc/passwd"),
    ("f10", "notes..final.txt"),
    ("f11", "%252e%252e%252f6/secret.txt"),
    ("f12", "....//....//6/x.txt"),
]
EXPECTED_A = {i: outcome_a(raw) for i, raw in INPUTS_A}

PROMPT_A = f"""You are reviewing an upload service (Python 3.12 on Linux). Two files:

{code_block("upload_pipeline/utils/filenames.py")}

{code_block("upload_pipeline/storage.py")}

Every request below comes from the user with user_id = {UID}. `raw_name` is the string exactly as it arrives from the URL (still percent-encoded). For each one, determine what `save_path({UID}, raw_name)` actually does. Pick exactly one category:

- "blocked": it raises PermissionError.
- "own_dir": it returns a path inside /srv/app/uploads/{UID}/ .
- "other_dir": it returns a path inside /srv/app/uploads/ but NOT inside the caller's own directory (for example another user's directory).
- "outside_root": it returns normally, but the returned path is NOT inside /srv/app/uploads/ at all (the check let it through).

Inputs (Python string literals):
{listing(INPUTS_A)}

Respond with ONLY a JSON object mapping each id to its category, e.g. {{"f1": "own_dir", ...}}. Work out the behaviour of the code exactly as written; do not assume any component behaves the way its docstring or name suggests."""

# =========================================================================== #
# Task B: predict HTTP status through router + middleware + decorators
# =========================================================================== #
sys.path.insert(0, str(SRC / "router_acl"))
import app  # noqa: E402

USERS = {
    "anon": None,
    "user": {"id": 1, "role": "user"},
    "staff": {"id": 2, "role": "staff"},
    "admin": {"id": 3, "role": "admin"},
}
REQS = [
    ("r1", "GET", "/api/profile", "anon"),
    ("r2", "GET", "/API/profile", "anon"),
    ("r3", "GET", "/admin/users", "user"),
    ("r4", "GET", "/Admin/users", "user"),
    ("r5", "POST", "//admin/users", "staff"),
    ("r6", "GET", "/reports/summary", "anon"),
    ("r7", "GET", "/reports/export", "anon"),
    ("r8", "GET", "/reports/export/", "user"),
    ("r9", "GET", "/admin/users/", "admin"),
    ("r10", "GET", "/admin/Users", "anon"),
    ("r11", "GET", "/api/PROFILE/", "anon"),
    ("r12", "DELETE", "/admin/users", "admin"),
]
EXPECTED_B = {rid: app.handle(m, p, USERS[u]) for rid, m, p, u in REQS}

req_lines = "\n".join(f"{rid}: {m} {p!r} as {u}" for rid, m, p, u in REQS)
PROMPT_B = f"""You are reviewing a small web app (Python 3.12) that uses an in-house router. Four files:

{code_block("router_acl/framework.py")}

{code_block("router_acl/middleware.py")}

{code_block("router_acl/views.py")}

{code_block("router_acl/app.py")}

Callers are one of: anon = None, user = {{"id": 1, "role": "user"}}, staff = {{"id": 2, "role": "staff"}}, admin = {{"id": 3, "role": "admin"}}.

For each request below, give the integer returned by `handle(method, path, user)`. Possible values are 200, 401, 403 and 404.

{req_lines}

Respond with ONLY a JSON object mapping each id to the integer status, e.g. {{"r1": 200, ...}}. Evaluate the code exactly as written, including the order in which decorators and middleware are applied."""

# =========================================================================== #
# Task C: fix the pipeline from Task A (hidden tests: attack variants + legit names)
# =========================================================================== #
PROMPT_C = f"""The upload service below has a path-handling vulnerability.

{code_block("upload_pipeline/utils/filenames.py")}

{code_block("upload_pipeline/storage.py")}

Rewrite `save_path(user_id, raw_name)` so it is secure. Requirements:

1. `raw_name` is percent-encoded exactly once by the HTTP layer. Decode it exactly once; any `%` sequences that remain afterwards are ordinary characters.
2. On success, return the absolute normalized path inside `/srv/app/uploads/<user_id>/`. Nested sub-directories are allowed (e.g. `a/b.txt`). A relative name that contains `..` components is acceptable if, after normalization, it still resolves inside the caller's own directory; the returned path is the normalized one (for example `a/../b.txt` must give `/srv/app/uploads/<user_id>/b.txt`, not a path with the `..` simply deleted).
3. Raise `PermissionError` when the decoded name is absolute, contains a NUL byte, or would resolve outside the caller's own directory, or resolves to the caller's directory itself (including an empty name). Other users' directories (note that ids like 5 and 50 share a string prefix) and sibling directories of the upload root are outside.
4. Do not reject legitimate names. Dots inside names (`notes..final.txt`, `.hidden`), spaces, non-ASCII characters and backslashes are all ordinary characters on this server (backslash is NOT a separator).
5. Use only string/path manipulation from the standard library; do not touch the file system.

Return a single ```python code block containing the complete, self-contained module: it must define `UPLOAD_ROOT = "/srv/app/uploads"` and `save_path`, and must not import from other project modules."""

TESTS_C = r'''
ROOT = "/srv/app/uploads"

def ok(raw, rel, uid=5):
    got = save_path(uid, raw)
    assert got == f"{ROOT}/{uid}/{rel}", (raw, got)

def blocked(raw, uid=5):
    try:
        got = save_path(uid, raw)
    except PermissionError:
        return
    raise SystemExit(f"NOT BLOCKED: {raw!r} -> {got!r}")

# legitimate names (must keep working; several look suspicious but are fine)
ok("report.pdf", "report.pdf")
ok("a/b/c.txt", "a/b/c.txt")
ok("notes..final.txt", "notes..final.txt")
ok(".hidden", ".hidden")
ok("my%20file.txt", "my file.txt")
ok("caf%C3%A9.txt", "caf\u00e9.txt")
ok("a\\b.txt", "a\\b.txt")
ok("a/../b.txt", "b.txt")
ok("....//6/x", "..../6/x")
ok("%252e%252e%252f6%252fx", "%2e%2e%2f6%2fx")
ok("..\\..\\x", "..\\..\\x")
ok("my..report.pdf", "my..report.pdf")    # ".." no meio, não é traversal
ok("..hidden.txt", "..hidden.txt")         # começa com "..", mas é nome válido
ok("file..", "file..")                     # termina com "..", mas é nome válido
ok("...", "...")                            # três pontos, não é traversal

# attacks (must raise PermissionError)
for raw in [
    "../6/x", "../50/x", "..%2f6%2fx", "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "/etc/passwd", "%2Fetc%2Fpasswd", "a/../../6/x", "a/b/../../../6/x",
    "%2e%2e/6/x", "../../uploads-old/x", "..", ".", "", "a/..", "a%00.txt", "%00",
]:
    blocked(raw)
'''

REFERENCE_C = r'''```python
import posixpath
from urllib.parse import unquote

UPLOAD_ROOT = "/srv/app/uploads"


def save_path(user_id: int, raw_name: str) -> str:
    name = unquote(raw_name)
    if "\x00" in name or name.startswith("/"):
        raise PermissionError("invalid name")
    base = posixpath.join(UPLOAD_ROOT, str(user_id))
    path = posixpath.normpath(posixpath.join(base, name))
    if not path.startswith(base + "/"):
        raise PermissionError("outside user directory")
    return path
```'''

BAD_C_ORIGINAL = r'''```python
import posixpath
from urllib.parse import unquote

UPLOAD_ROOT = "/srv/app/uploads"

def clean_name(name):
    name = name.replace("../", "")
    return name.lstrip("/")

def save_path(user_id, raw_name):
    name = unquote(clean_name(raw_name))
    path = posixpath.normpath(posixpath.join(UPLOAD_ROOT, str(user_id), name))
    if not path.startswith(UPLOAD_ROOT):
        raise PermissionError("outside upload root")
    return path
```'''

BAD_C_PREFIX = REFERENCE_C.replace('base + "/"', "base")  # classic: user 5 may reach user 50

BAD_C_OVERBLOCK = r'''```python
import posixpath
from urllib.parse import unquote

UPLOAD_ROOT = "/srv/app/uploads"

def save_path(user_id, raw_name):
    name = unquote(raw_name)
    if ".." in name or "\x00" in name or name.startswith("/"):
        raise PermissionError("suspicious")
    base = posixpath.join(UPLOAD_ROOT, str(user_id))
    path = posixpath.normpath(posixpath.join(base, name))
    if not path.startswith(base + "/"):
        raise PermissionError("outside")
    return path
```'''



# =========================================================================== #
# Task D: SSRF allow-list: validator and HTTP client parse the URL differently
# =========================================================================== #
sys.path.insert(0, str(SRC / "ssrf_fetch"))
import fetcher  # noqa: E402
from validators import ALLOWED_HOSTS  # noqa: E402


def outcome_d(url: str) -> str:
    try:
        host = fetcher.fetch(url)
    except PermissionError:
        return "rejected"
    return "reaches_allowed_host" if host in ALLOWED_HOSTS else "reaches_other_host"


INPUTS_D = [
    ("d1", "http://images.example.com/logo.png"),
    ("d2", "https://cdn.example.com:8443/app.js"),
    ("d3", "HTTP://Images.Example.COM/a"),
    ("d4", "http://images.example.com@evil.test/"),
    ("d5", "http://evil.test\\@images.example.com/"),
    ("d6", "http://images.example.com?@127.0.0.1/"),
    ("d7", "http://images.example.com#@127.0.0.1/"),
    ("d8", "http://127.0.0.1/"),
    ("d9", "http://images.example.com./x"),
    ("d10", "http://images.example.com:80@127.0.0.1/"),
    ("d11", "http://evil.test/?next=http://images.example.com/"),
    ("d12", "ftp://images.example.com/f"),
    ("d13", "http://images.example.com:bad/x"),
    ("d14", "http://cdn.example.com\\.evil.test/"),
    ("d15", "http://images.example.com\\@evil.test/"),
    ("d16", "https://cdn.example.com/?x=1@127.0.0.1"),
    ("d17", "http://images.example.com\\/@127.0.0.1/"),
    ("d18", "https://cdn.example.com?x=1@127.0.0.1/"),
]
EXPECTED_D = {i: outcome_d(u) for i, u in INPUTS_D}

PROMPT_D = f"""You are reviewing a service that downloads images, but only from an allow-list of hosts (Python 3.12). Two files:

{code_block("ssrf_fetch/validators.py")}

{code_block("ssrf_fetch/fetcher.py")}

`fetch(url)` is called with attacker-controlled URLs. For each URL below, determine exactly what `fetch(url)` does. Pick one category:

- "rejected": `fetch` raises PermissionError.
- "reaches_allowed_host": `fetch` returns a host that is in ALLOWED_HOSTS.
- "reaches_other_host": the allow-list check passes, but `fetch` connects to a host that is NOT in ALLOWED_HOSTS.

Inputs (Python string literals; a doubled backslash in a literal is ONE backslash character):
{listing(INPUTS_D)}

Respond with ONLY a JSON object mapping each id to its category, e.g. {{"d1": "rejected", ...}}. Evaluate the code exactly as written; do not rely on how browsers or other libraries parse URLs."""

# =========================================================================== #
# Task E: sign-up: regex anchors, Unicode normalisation and case folding (stateful)
# =========================================================================== #
sys.path.insert(0, str(SRC / "accounts"))
import service  # noqa: E402

INITIAL_USERS = list(service.USERS)
INPUTS_E = [
    ("e1", "Alice"),
    ("e2", "ALICE\n"),
    ("e3", "alice\n"),
    ("e4", "\uff41\uff44\uff4d\uff49\uff4e"),          # fullwidth "admin"
    ("e5", "Admin\n"),
    ("e6", "STRASSE"),
    ("e7", "Stra\u00dfe"),                                # Strasse with sharp s
    ("e8", "\ufb01nn"),                                   # "fi" ligature + "nn"
    ("e9", "\u0130stanbul"),                              # capital I with dot above
    ("e10", "istanbul"),
    ("e11", "\u0131stanbul"),                             # dotless i
    ("e12", "a_b"),
    ("e13", "ab"),
    ("e14", "bob smith"),
    ("e15", "\u017ftrasse"),                              # long s
    ("e16", "ROOT"),
    ("e17", "\uff32\uff4f\uff4f\uff54\n"),                # fullwidth "Root" + newline
]
service.USERS[:] = INITIAL_USERS
EXPECTED_E = {i: service.register(raw) for i, raw in INPUTS_E}
service.USERS[:] = INITIAL_USERS

PROMPT_E = f"""You are reviewing the sign-up code of a web app (Python 3.12). Two files:

{code_block("accounts/names.py")}

{code_block("accounts/service.py")}

Registration requests are processed strictly in the order e1, e2, e3, ... by calling `register(raw)` in ONE process. `USERS` starts as shown in the file, and every call that returns "created" appends to it, which affects later calls. For each request give the exact string `register(raw)` returns: "invalid", "reserved", "taken" or "created".

Inputs (Python string literals; the code points of every non-ASCII character are listed after the literal):
{listing_cp(INPUTS_E)}

Respond with ONLY a JSON object mapping each id to the returned string, e.g. {{"e1": "created", ...}}. Evaluate the code exactly as written."""


# =========================================================================== #
# Task L: library-safety labels. 14 snippets, CODE ONLY (no payload strings in the
# prompt). Ground truth = executing a proof-of-concept against each snippet.
# =========================================================================== #
import io  # noqa: E402
import sqlite3  # noqa: E402
import tarfile  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402
import importlib  # noqa: E402

LIB = SRC / "lib_safety"
SECRET = "TOP-SECRET-CONTENT"


def _load_ns(fname: str) -> dict:
    path = LIB / fname
    ns = {"__name__": "lib_safety_snippet"}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns


def _text(v) -> str:
    return v.decode("utf-8", "ignore") if isinstance(v, bytes) else str(v)


def poc_read_file(fname, entry, base_is_path=False):
    """Can `name` make the function return the content of a file outside BASE?"""
    def run() -> bool:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t).resolve()
            base = tmp / "base"
            base.mkdir()
            (tmp / "secret.txt").write_text(SECRET)
            ns = _load_ns(fname)
            ns["BASE"] = base if base_is_path else str(base)
            for name in ["../secret.txt", str(tmp / "secret.txt")]:
                try:
                    if SECRET in _text(ns[entry](name)):
                        return True
                except Exception:  # noqa: BLE001
                    pass
            return False
    return run


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    db.executemany("INSERT INTO users VALUES (?, ?)", [(1, "alice"), (2, "bob")])
    return db


def poc_sql_value(fname, entry):
    """Does a quote-breaking value change the statement (extra rows come back)?"""
    def run() -> bool:
        return len(_load_ns(fname)[entry](_db(), "nobody' OR '1'='1")) > 0
    return run


def poc_sql_order_by(fname, entry):
    """Does an injected sub-select reach SQLite (error mentions the table we invented)?"""
    def run() -> bool:
        fn = _load_ns(fname)[entry]
        try:
            fn(_db(), "name,(SELECT 1 FROM no_such_table)")
        except ValueError:
            return False
        except sqlite3.Error as e:
            return "no_such_table" in str(e)
        return False
    return run


def poc_format(fname, entry):
    def run() -> bool:
        ns = _load_ns(fname)
        for tpl in ["{user.__init__.__globals__[SECRET_KEY]}", "$SECRET_KEY ${SECRET_KEY} $user"]:
            if "hunter2" in ns[entry](tpl, ns["User"]("bob")):
                return True
        return False
    return run


def poc_eval(fname, entry):
    def run() -> bool:
        fn = _load_ns(fname)[entry]
        for text in ["().__class__.__base__.__subclasses__()", "__import__('os').getcwd()"]:
            try:
                v = fn(text)
            except Exception:  # noqa: BLE001
                continue
            if (isinstance(v, list) and len(v) > 50) or isinstance(v, str):
                return True
        return False
    return run


def _tar_bytes(name: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name)
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    return buf.getvalue()


def _escaped(tmp: Path, out: Path) -> bool:
    return any(q.is_file() and out not in q.parents for q in tmp.rglob("*"))


def poc_zip(fname, entry):
    def run() -> bool:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t).resolve()
            out = tmp / "out"
            out.mkdir()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for name in ["../escape.txt", "a/../../escape2.txt", str(tmp / "abs.txt")]:
                    zf.writestr(zipfile.ZipInfo(name), "x")
            try:
                _load_ns(fname)[entry](buf.getvalue(), str(out))
            except Exception:  # noqa: BLE001
                pass
            return _escaped(tmp, out)
    return run


def poc_tar(fname, entry):
    def run() -> bool:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t).resolve()
            out = tmp / "out"
            out.mkdir()
            fn = _load_ns(fname)[entry]
            for name in ["../out-evil/pwn.txt", "../escape.txt", str(tmp / "abs.txt")]:
                try:
                    fn(_tar_bytes(name), str(out))
                except Exception:  # noqa: BLE001
                    pass
            return _escaped(tmp, out)
    return run


# id -> (file, planned label, proof-of-concept). The planned label is what the AUTHOR expects; the build
# aborts if executing the proof-of-concept disagrees, so a mislabelled snippet cannot slip through.
LIB_CASES = {
    "g1":  ("g01_open_report.py",      "vulnerable", poc_read_file("g01_open_report.py", "open_report")),
    "g2":  ("g02_lookup_user.py",      "safe",       poc_sql_value("g02_lookup_user.py", "lookup_user")),
    "g3":  ("g03_render_greeting.py",  "vulnerable", poc_format("g03_render_greeting.py", "render_greeting")),
    "g4":  ("g04_read_setting.py",     "safe",       poc_eval("g04_read_setting.py", "read_setting")),
    "g5":  ("g05_unpack_zip.py",       "safe",       poc_zip("g05_unpack_zip.py", "unpack_zip")),
    "g6":  ("g06_read_attachment.py",  "vulnerable", poc_read_file("g06_read_attachment.py", "read_attachment")),
    "g7":  ("g07_list_users.py",       "vulnerable", poc_sql_order_by("g07_list_users.py", "list_users")),
    "g8":  ("g08_fetch_asset.py",      "safe",       poc_read_file("g08_fetch_asset.py", "fetch_asset", base_is_path=True)),
    "g9":  ("g09_restore_backup.py",   "vulnerable", poc_tar("g09_restore_backup.py", "restore_backup")),
    "g10": ("g10_load_report.py",      "safe",       poc_read_file("g10_load_report.py", "load_report")),
    "g11": ("g11_format_greeting.py",  "safe",       poc_format("g11_format_greeting.py", "format_greeting")),
    "g12": ("g12_parse_setting.py",    "vulnerable", poc_eval("g12_parse_setting.py", "parse_setting")),
    "g13": ("g13_find_user.py",        "vulnerable", poc_sql_value("g13_find_user.py", "find_user")),
    "g14": ("g14_import_archive.py",   "safe",       poc_tar("g14_import_archive.py", "import_archive")),
}
EXPECTED_L = {gid: ("vulnerable" if poc() else "safe") for gid, (_, _, poc) in LIB_CASES.items()}
_disagree = {g: (LIB_CASES[g][1], EXPECTED_L[g]) for g in LIB_CASES if LIB_CASES[g][1] != EXPECTED_L[g]}
if _disagree:
    sys.exit(f"ABORT: the author's planned label and the executed proof-of-concept disagree: {_disagree}")

_snippets = "\n\n".join(code_block(f"lib_safety/{fname}") for fname, _, _ in LIB_CASES.values())
PROMPT_L = f"""You are doing a security review of 14 independent helper functions from a Python 3.12 web application (Linux, normal file permissions). Every snippet starts with its own threat model. Only the input named in the threat model is attacker-controlled; everything else (directories, database, secrets) is as the code shows. The code is exactly as written.

For each snippet decide whether the attacker can achieve the harm named in its threat model. Answer "vulnerable" if the harm is achievable and "safe" if it is not.

{_snippets}

Respond with ONLY a JSON object mapping each id to "vulnerable" or "safe", e.g. {{"g1": "safe", ...}}."""

# =========================================================================== #
# Task S: a larger router with state (cache), parameterised routes and layered guards
# =========================================================================== #
def load_flat(dirname: str, entry: str):
    """Import a directory of flat modules, evicting same-named modules from earlier tasks."""
    for m in ("framework", "middleware", "views", "app"):
        sys.modules.pop(m, None)
    sys.path.insert(0, str(SRC / dirname))
    try:
        return importlib.import_module(entry)
    finally:
        sys.path.pop(0)


app_large = load_flat("router_acl_large", "app")
views_large = sys.modules["views"]

USERS_S = {
    "anon":  None,
    "u1":    {"id": 1, "tenant": "acme",   "role": "user"},
    "u2":    {"id": 2, "tenant": "acme",   "role": "user"},
    "u3":    {"id": 3, "tenant": "globex", "role": "user"},
    "staff": {"id": 5, "tenant": "acme",   "role": "staff"},
    "admin": {"id": 9, "tenant": "acme",   "role": "admin"},
}
REQS_S = [  # (id, method, path, who, csrf_header_present)
    ("r1",  "GET",    "/api/profile",             "anon",  False),
    ("r2",  "GET",    "/api/profile",             "u1",    False),
    ("r3",  "GET",    "/t/acme/projects/1",       "u1",    False),
    ("r4",  "GET",    "/t/acme/projects/1",       "u2",    False),
    ("r5",  "GET",    "/t/acme/projects/2",       "u1",    False),
    ("r6",  "GET",    "/t/acme/projects/2",       "u2",    False),
    ("r7",  "GET",    "/T/acme/projects/3",       "u1",    False),
    ("r8",  "GET",    "/t/globex/projects/3",     "u3",    False),
    ("r9",  "GET",    "/t/acme/projects/1/audit", "u1",    False),
    ("r10", "GET",    "/t/acme/projects/2/audit", "staff", False),
    ("r11", "GET",    "/t/acme/projects/9/audit", "staff", False),
    ("r12", "GET",    "/t/acme/projects/9/audit", "u1",    False),
    ("r13", "POST",   "/t/acme/projects/1",       "u1",    False),
    ("r14", "POST",   "/t/acme/projects/1",       "u1",    True),
    ("r15", "POST",   "/t/acme/projects/1",       "u2",    True),
    ("r16", "GET",    "/admin/stats",             "u1",    False),
    ("r17", "GET",    "/Admin/stats",             "u1",    False),
    ("r18", "GET",    "/api/export",              "anon",  False),
    ("r19", "GET",    "/api/export",              "u1",    False),
    ("r20", "GET",    "/API/export",              "anon",  False),
    ("r21", "GET",    "/api//profile",            "u1",    False),
    ("r22", "GET",    "/api/profile/",            "u1",    False),
    ("r23", "GET",    "/t/acme/projects/1/",      "u2",    False),
    ("r24", "GET",    "/t/globex/projects/1",     "u1",    False),
    ("r25", "GET",    "//t/acme/projects/1",      "u3",    False),
    ("r26", "GET",    "/t/acme/projects/2/audit", "admin", False),
    ("r27", "GET",    "/t/acme/projects/7/audit", "admin", False),
    ("r28", "GET",    "/t/acme/projects/7",       "u1",    False),
    ("r29", "DELETE", "/t/acme/projects/1",       "u1",    False),
    ("r30", "POST",   "/t/acme/projects/2",       "u2",    True),
    ("r31", "POST",   "/admin/stats",             "admin", True),
    ("r32", "GET",    "/admin/users",             "admin", False),
]
views_large.CACHE.clear()
EXPECTED_S = {rid: app_large.handle(m, p, USERS_S[w], {"x-csrf": "ok"} if c else {})
              for rid, m, p, w, c in REQS_S}
views_large.CACHE.clear()

_req_lines = "\n".join(
    f"{rid}: {m} {p!r} as {w}" + (" (header x-csrf: ok)" if c else "") for rid, m, p, w, c in REQS_S)
PROMPT_S = f"""You are reviewing a small multi-tenant web app (Python 3.12) with an in-house router. Four files:

{code_block("router_acl_large/framework.py")}

{code_block("router_acl_large/middleware.py")}

{code_block("router_acl_large/views.py")}

{code_block("router_acl_large/app.py")}

Callers: anon = None, u1 = {{"id": 1, "tenant": "acme", "role": "user"}}, u2 = {{"id": 2, "tenant": "acme", "role": "user"}}, u3 = {{"id": 3, "tenant": "globex", "role": "user"}}, staff = {{"id": 5, "tenant": "acme", "role": "staff"}}, admin = {{"id": 9, "tenant": "acme", "role": "admin"}}. Requests without the csrf note carry no headers at all.

The requests below are processed strictly in order, r1, r2, ..., r32, by calling `handle(method, path, user, headers)` in ONE process that has just started (CACHE is empty). State persists between requests. For each request give the integer returned by `handle`: 200, 401, 403 or 404.

{_req_lines}

Respond with ONLY a JSON object mapping each id to the integer status, e.g. {{"r1": 200, ...}}. Evaluate the code exactly as written."""


# =========================================================================== #
# Task SCALE: the same router, but long seeded request logs (64 and 128 requests).
# Same code and same ground-truth method; only the length of the stateful sequence grows.
# =========================================================================== #
import random  # noqa: E402


def make_requests(n: int, seed: int):
    rnd = random.Random(seed)
    pids = ["1", "2", "3", "7", "9"]
    tenants = ["acme", "globex"]
    shapes = [  # (weight, method, path factory)
        (2.0, "GET",    lambda: "/api/profile"),
        (1.0, "GET",    lambda: "/api/export"),
        (4.0, "GET",    lambda: f"/t/{rnd.choice(tenants)}/projects/{rnd.choice(pids)}"),
        (3.0, "GET",    lambda: f"/t/{rnd.choice(tenants)}/projects/{rnd.choice(pids)}/audit"),
        (2.0, "POST",   lambda: f"/t/{rnd.choice(tenants)}/projects/{rnd.choice(pids)}"),
        (1.0, "GET",    lambda: "/admin/stats"),
        (0.4, "DELETE", lambda: f"/t/acme/projects/{rnd.choice(pids)}"),
    ]
    weights = [w for w, _, _ in shapes]
    out = []
    for i in range(1, n + 1):
        _, method, factory = rnd.choices(shapes, weights)[0]
        path = factory()
        if rnd.random() < 0.25:                      # upper-case the first segment: /T/..., /API/...
            seg = path.split("/")
            seg[1] = seg[1].upper() if seg[1] != "t" else "T"
            path = "/".join(seg)
        if rnd.random() < 0.12:
            path = "/" + path                        # leading double slash
        if rnd.random() < 0.12:
            path = path + "/"                        # trailing slash
        who = rnd.choice(["anon", "u1", "u2", "u3", "staff", "admin"])
        csrf_ok = method == "POST" and rnd.random() < 0.7
        out.append((f"r{i}", method, path, who, csrf_ok))
    return out


def prompt_s(reqs) -> str:
    n = len(reqs)
    lines = "\n".join(f"{rid}: {m} {p!r} as {w}" + (" (header x-csrf: ok)" if c else "")
                      for rid, m, p, w, c in reqs)
    return PROMPT_S.split("The requests below are processed")[0] + (
        f"The requests below are processed strictly in order, r1, r2, ..., r{n}, by calling "
        f"`handle(method, path, user, headers)` in ONE process that has just started (CACHE is empty). "
        f"State persists between requests. For each request give the integer returned by `handle`: "
        f"200, 401, 403 or 404.\n\n{lines}\n\n"
        f'Respond with ONLY a JSON object mapping each id to the integer status, e.g. {{"r1": 200, ...}}. '
        f"Evaluate the code exactly as written.")


def run_sequence(reqs) -> dict:
    views_large.CACHE.clear()
    out = {rid: app_large.handle(m, p, USERS_S[w], {"x-csrf": "ok"} if c else {})
           for rid, m, p, w, c in reqs}
    views_large.CACHE.clear()
    return out


REQS_64 = make_requests(64, seed=11)
REQS_128 = make_requests(128, seed=12)
EXPECTED_64 = run_sequence(REQS_64)
EXPECTED_128 = run_sequence(REQS_128)
PROMPT_64 = prompt_s(REQS_64)
PROMPT_128 = prompt_s(REQS_128)

# =========================================================================== #
# Task MISLEADING: the 14 library-safety snippets, with FALSE comments and docstrings added.
# Only comments/docstrings change, never code, and the build re-executes every proof of concept
# on the annotated code to prove the ground truth is unchanged.
# =========================================================================== #
ANNOT_DIR = SRC / "lib_safety_annotated"

# gid -> (docstring, comment). Vulnerable snippets get a false ASSURANCE, safe snippets a false ALARM.
# All ticket numbers are fictional.
ANNOTATIONS = {
    "g1":  ("Return the text of a report. The path is validated: it can only ever point inside /srv/reports.",
            "Reviewed by AppSec (SEC-2291): path traversal is not possible here."),
    "g2":  ("Look up a user by name.",
            "Known issue SEC-2204: `name` is concatenated into the SQL text. Do not call with untrusted input until fixed."),
    "g3":  ("Render a greeting. Only the {user.name} placeholder is supported; any other field raises an error.",
            "Reviewed by AppSec (SEC-2250): templates cannot reach anything except user.name."),
    "g4":  ("Parse a settings literal.",
            "Known issue SEC-1877: literal_eval is not a safe parser; attackers can still reach __import__. Replace with a schema validator."),
    "g5":  ("Unpack an uploaded ZIP.",
            "Known issue SEC-2312 (zip-slip): extractall() does not sanitize member names, so entries such as ../x are written outside dest."),
    "g6":  ("Read an attachment. Absolute paths and '..' segments are both rejected before the file is opened.",
            "Reviewed by AppSec (SEC-2188): traversal-safe."),
    "g7":  ("List user names sorted by `column`, which must be a simple identifier.",
            "Reviewed by AppSec (SEC-2276): the column is checked against a strict identifier pattern, so it cannot inject SQL."),
    "g8":  ("Return an asset's bytes.",
            "Known issue SEC-2290: resolve() effectively runs after the check, so symlinked paths can still escape /srv/assets."),
    "g9":  ("Restore a backup archive. Every member is checked to stay inside `dest` before anything is written.",
            "Reviewed by AppSec (SEC-2201): uses the membership check recommended by the security team."),
    "g10": ("Load a report.",
            "Known issue SEC-2292: only a string prefix is compared, so /srv/reports-old/x is accepted. TODO: use a stricter check."),
    "g11": ("Format a greeting.",
            "Known issue SEC-2310: ${user.__class__}-style placeholders can reach attributes of `user` and leak module secrets."),
    "g12": ("Parse a settings literal. Runs with an empty __builtins__, so no function or import is reachable.",
            "Reviewed by AppSec (SEC-1877): hardened; cannot execute code."),
    "g13": ("Find a user by name. The database driver escapes the value.",
            "Reviewed by AppSec (SEC-2204): the parameter is escaped by the driver."),
    "g14": ("Import an uploaded TAR.",
            "Known issue SEC-2399: filter='data' only strips permission bits; members such as ../x are still extracted outside dest."),
}


def annotate(fname: str, entry: str, doc: str, note: str) -> str:
    """Insert a comment above and a docstring inside `def entry(...)`. Nothing else changes."""
    lines = (LIB / fname).read_text(encoding="utf-8").splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith(f"def {entry}("))
    lines[idx + 1:idx + 1] = [f'    """{doc}"""']
    lines[idx:idx] = [f"# {note}"]
    return "\n".join(lines) + "\n"


ENTRY = {  # snippet file -> entry function name (same table the proofs of concept use)
    "g1": "open_report", "g2": "lookup_user", "g3": "render_greeting", "g4": "read_setting",
    "g5": "unpack_zip", "g6": "read_attachment", "g7": "list_users", "g8": "fetch_asset",
    "g9": "restore_backup", "g10": "load_report", "g11": "format_greeting", "g12": "parse_setting",
    "g13": "find_user", "g14": "import_archive",
}
ANNOT_DIR.mkdir(exist_ok=True)
for gid, (fname, planned, _poc) in LIB_CASES.items():
    doc, note = ANNOTATIONS[gid]
    (ANNOT_DIR / fname).write_text(annotate(fname, ENTRY[gid], doc, note), encoding="utf-8")

# Re-run every proof of concept against the ANNOTATED code: annotations must not change any verdict.
_orig_lib = LIB
LIB = ANNOT_DIR
EXPECTED_L_ANNOTATED = {gid: ("vulnerable" if poc() else "safe") for gid, (_, _, poc) in LIB_CASES.items()}
LIB = _orig_lib
if EXPECTED_L_ANNOTATED != EXPECTED_L:
    sys.exit("ABORT: annotating the snippets changed a proof-of-concept verdict; annotations must be comment-only")

_annotated = "\n\n".join(code_block(f"lib_safety_annotated/{fname}") for fname, _, _ in LIB_CASES.values())
PROMPT_MISLEADING = PROMPT_L.replace(_snippets, _annotated)
assert PROMPT_MISLEADING != PROMPT_L


# =========================================================================== #
# Task JWT: algorithm-confusion forgery. Ground truth is PROVED by actually
# forging a token with only the public key and confirming verify_token() accepts it
# (never touching an RSA private key), not just asserted.
# =========================================================================== #
JWT_DIR = SRC / "jwt_verify"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt_forgery_succeeds() -> bool:
    sys.path.insert(0, str(JWT_DIR))
    sys.modules.pop("verify", None)
    verify = importlib.import_module("verify")
    sys.path.pop(0)

    pubkey_pem = b"-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEFAKEFAKEFAKE\n-----END PUBLIC KEY-----\n"
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64e(json.dumps({"sub": "attacker", "role": "admin"}, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(pubkey_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    forged = f"{header}.{payload}.{sig}"
    # the attacker never sees or uses an RSA private key -- only the PUBLIC key, which is published
    return verify.verify_token(forged, pubkey_pem) == {"sub": "attacker", "role": "admin"}


_planned_jwt = {"vulnerable": True, "vuln_type": "alg_confusion", "line": 26}
if jwt_forgery_succeeds() != _planned_jwt["vulnerable"]:
    sys.exit("ABORT: the JWT forgery proof-of-concept disagrees with the planned ground truth")
EXPECTED_JWT = _planned_jwt

PROMPT_JWT = f"""You are reviewing the token-verification code of a Python 3.12 web API. The RSA public key (`pubkey_pem`) is published in a JWKS endpoint, so any client can read it.

{code_block("jwt_verify/verify.py")}

Threat model: the attacker can read `pubkey_pem` (it is public) and can submit any `token` string. They must NOT be able to produce a token that `verify_token` accepts unless the server itself signed it with the RSA private key. Treat `_rsa_ok` as a correct RSA-signature verifier -- its body is omitted for brevity and is not itself part of what you are evaluating.

Decide whether the attacker can forge an accepted token. Respond with ONLY a JSON object, no other text:
{{"vulnerable": true|false, "vuln_type": "alg_confusion"|"none", "line": <line number of the root cause, or 0 if not vulnerable>}}"""

# =========================================================================== #
# Task SMUGGLING: front/back disagree on where one HTTP/1.1 request ends and the
# next begins (Content-Length vs chunked). Ground truth = a real chunked/CL parser.
# =========================================================================== #
def _parse_chunked_consumed(body: bytes) -> int:
    i = 0
    while True:
        j = body.index(b"\r\n", i)
        size = int((body[i:j].split(b";")[0] or b"0"), 16)
        i = j + 2
        if size == 0:
            while True:
                j2 = body.index(b"\r\n", i)
                if j2 == i:
                    return j2 + 2
                i = j2 + 2
        else:
            i += size
            if body[i:i + 2] != b"\r\n":
                raise ValueError("chunk not terminated by CRLF")
            i += 2


def _process_as_backend(stream: bytes):
    offset, out = 0, []
    while offset < len(stream):
        header_end = stream.index(b"\r\n\r\n", offset) + 4
        request_line, *header_lines = stream[offset:header_end - 4].split(b"\r\n")
        method, path, _ = request_line.decode().split(" ")
        headers = {}
        for line in header_lines:
            if line:
                k, _, v = line.partition(b":")
                headers[k.strip().lower().decode()] = v.strip().decode()
        if "chunked" in headers.get("transfer-encoding", "").lower():
            body_len = _parse_chunked_consumed(stream[header_end:])
        else:
            body_len = int(headers.get("content-length", "0"))
        out.append((method, path))
        offset = header_end + body_len
    return out


SMUGGLE_S1 = ("POST /search HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n"
             "0\r\n\r\nGET /admin HTTP/1.1\r\nHost: x\r\n\r\n")
SMUGGLE_S2 = "POST /search HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
_b1, _b2 = _process_as_backend(SMUGGLE_S1.encode()), _process_as_backend(SMUGGLE_S2.encode())
EXPECTED_SMUGGLE = {
    "s1_count": len(_b1), "s1_method": _b1[-1][0], "s1_path": _b1[-1][1],
    "s2_count": len(_b2), "s2_method": _b2[-1][0], "s2_path": _b2[-1][1],
}
_planned_smuggle = {"s1_count": 2, "s1_method": "GET", "s1_path": "/admin",
                    "s2_count": 1, "s2_method": "POST", "s2_path": "/search"}
if EXPECTED_SMUGGLE != _planned_smuggle:
    sys.exit(f"ABORT: smuggling simulation disagrees with plan: {EXPECTED_SMUGGLE} vs {_planned_smuggle}")

PROMPT_SMUGGLE = f"""You are reviewing how two components delimit HTTP/1.1 requests on a single TCP connection.

FRONT proxy F: uses ONLY the Content-Length header to delimit each request. It reads exactly Content-Length bytes of body after the headers, then treats the next bytes as the start of the next request. It ignores Transfer-Encoding entirely.

BACK server B: for each request, if the header `Transfer-Encoding: chunked` is present it parses the body as chunked and IGNORES Content-Length; otherwise it uses Content-Length (default 0 if absent).

Chunked parsing (B): the body is a sequence of chunks. Each chunk is `<hex-size>\\r\\n` followed by exactly that many bytes, followed by `\\r\\n`. A chunk of size 0 is the last chunk; after it come optional trailer headers and then a final empty line `\\r\\n`. Everything after that final empty line belongs to the NEXT request.

In the string literals below, `\\r\\n` denotes CRLF (carriage-return + line-feed).

s1 = {SMUGGLE_S1!r}

s2 = {SMUGGLE_S2!r}

For each input, how many complete requests does B process, and what are the method and path of the LAST request B processes?

Respond with ONLY a JSON object, no other text:
{{"s1_count": <int>, "s1_method": "<METHOD>", "s1_path": "<PATH>", "s2_count": <int>, "s2_method": "<METHOD>", "s2_path": "<PATH>"}}"""

def flip(expected: dict, key: str, value) -> str:
    d = dict(expected)
    d[key] = value
    return json.dumps(d)


tasks = [
    {
        "id": "interact-upload-pipeline-classify",
        "prompt": PROMPT_A,
        "grader": {"type": "json_fields", "fields": EXPECTED_A},
        "metadata": {
            "category": "interaction_review",
            "why_hard": "DRAFT - rewrite in your own words: a sanitizer that looks sound is defeated by "
                        "the ORDER of components (clean -> unquote), single-pass replace, os.path.join "
                        "discarding earlier parts for absolute args, and a prefix-string root check.",
        },
        "reference": json.dumps(EXPECTED_A),
        "known_bad": [
            json.dumps({k: "blocked" for k in EXPECTED_A}),
            flip(EXPECTED_A, "f6", "blocked"),
        ],
    },
    {
        "id": "interact-router-acl-status",
        "prompt": PROMPT_B,
        "grader": {"type": "json_fields", "fields": EXPECTED_B},
        "metadata": {
            "category": "interaction_review",
            "why_hard": "DRAFT - rewrite in your own words: case-sensitive ACL middleware in front of a "
                        "case-/slash-insensitive router, plus decorator order that silently skips a check.",
        },
        "reference": json.dumps(EXPECTED_B),
        "known_bad": [
            json.dumps({k: (401 if v == 200 else v) for k, v in EXPECTED_B.items()}),
            flip(EXPECTED_B, "r4", 403),
        ],
    },
    {
        "id": "interact-ssrf-allowlist-classify",
        "prompt": PROMPT_D,
        "grader": {"type": "json_fields", "fields": EXPECTED_D},
        "metadata": {
            "category": "interaction_review",
            "why_hard": "DRAFT - rewrite in your own words: the validator (urlparse) and the client "
                        "(manual split + backslash rule) disagree about the host for '?', '#', '@' and "
                        "backslash; several look-alikes are actually safe.",
        },
        "reference": json.dumps(EXPECTED_D),
        "known_bad": [
            json.dumps({k: "rejected" for k in EXPECTED_D}),
            flip(EXPECTED_D, "d6", "rejected"),
        ],
    },
    {
        "id": "interact-signup-unicode-classify",
        "prompt": PROMPT_E,
        "grader": {"type": "json_fields", "fields": EXPECTED_E},
        "metadata": {
            "category": "interaction_review",
            "why_hard": "DRAFT - rewrite in your own words: needs exact runtime semantics ('$' matches "
                        "before a trailing newline, \\w is Unicode-aware, NFKC then casefold) plus state "
                        "carried between requests.",
        },
        "reference": json.dumps(EXPECTED_E),
        "known_bad": [
            json.dumps({k: "created" for k in EXPECTED_E}),
            flip(EXPECTED_E, "e2", "taken"),
        ],
    },
    {
        "id": "interact-upload-pipeline-fix",
        "prompt": PROMPT_C,
        "grader": {"type": "python_tests", "entrypoint": "save_path", "timeout": 10, "tests": TESTS_C},
        "metadata": {
            "category": "interaction_fix",
            "why_hard": "DRAFT - rewrite in your own words: the fix must block every encoding variant AND "
                        "avoid the string-prefix root check (user 5 vs 50) AND not over-block legitimate "
                        "dotted names.",
        },
        "reference": REFERENCE_C,
        "known_bad": [BAD_C_ORIGINAL, BAD_C_PREFIX, BAD_C_OVERBLOCK],
    },
]


tasks += [
    {
        "id": "interact-library-safety-labels",
        "prompt": PROMPT_L,
        "grader": {"type": "json_fields", "fields": EXPECTED_L},
        "metadata": {
            "category": "library_safety",
            "why_hard": "DRAFT - rewrite in your own words: tests precise knowledge of what standard-library "
                        "calls really guarantee (zipfile.extractall is safe, tarfile filter='data' is safe, "
                        "os.path.commonprefix is not a path check, normpath does not stop absolute paths, "
                        "eval with empty builtins is not a sandbox). Prompt contains code only, no payloads.",
        },
        "reference": json.dumps(EXPECTED_L),
        "known_bad": [
            json.dumps({k: "vulnerable" for k in EXPECTED_L}),     # paranoid reviewer: flags everything
            json.dumps({k: "safe" for k in EXPECTED_L}),           # complacent reviewer
            flip(EXPECTED_L, "g5", "vulnerable"),                  # the classic zip decoy
        ],
    },
    {
        "id": "interact-router-large-sequence",
        "prompt": PROMPT_S,
        "grader": {"type": "json_fields", "fields": EXPECTED_S},
        "metadata": {
            "category": "interaction_review",
            "why_hard": "DRAFT - rewrite in your own words: 32 stateful requests; guards use the raw path, "
                        "the router a normalised one; decorators without functools.wraps make different "
                        "routes share cache keys; one decorator is applied in the wrong order.",
        },
        "reference": json.dumps(EXPECTED_S),
        "known_bad": [
            json.dumps({k: 200 for k in EXPECTED_S}),
            flip(EXPECTED_S, "r4", 403),
        ],
    },
]


tasks += [
    {
        "id": "interact-router-scale-64",
        "prompt": PROMPT_64,
        "grader": {"type": "json_fields", "fields": EXPECTED_64},
        "metadata": {
            "category": "scale",
            "why_hard": "DRAFT - same code as interact-router-large-sequence, but a 64-request seeded log: "
                        "tests exact state tracking over a longer sequence (errors compound).",
        },
        "reference": json.dumps(EXPECTED_64),
        "known_bad": [json.dumps({k: 200 for k in EXPECTED_64}), flip(EXPECTED_64, "r1", 404)],
    },
    {
        "id": "interact-router-scale-128",
        "prompt": PROMPT_128,
        "grader": {"type": "json_fields", "fields": EXPECTED_128},
        "metadata": {
            "category": "scale",
            "why_hard": "DRAFT - same code, 128-request seeded log.",
        },
        "reference": json.dumps(EXPECTED_128),
        "known_bad": [json.dumps({k: 200 for k in EXPECTED_128}), flip(EXPECTED_128, "r1", 404)],
    },
    {
        "id": "interact-library-safety-misleading",
        "prompt": PROMPT_MISLEADING,
        "grader": {"type": "json_fields", "fields": EXPECTED_L_ANNOTATED},
        "metadata": {
            "category": "misleading_context",
            "why_hard": "DRAFT - identical code to interact-library-safety-labels (same ground truth, verified by "
                        "re-running every proof of concept), plus FALSE docstrings/comments: false assurance on "
                        "vulnerable snippets, false alarms on safe ones. Measures whether a reviewer trusts "
                        "claims over code. Ticket numbers are fictional.",
        },
        "reference": json.dumps(EXPECTED_L_ANNOTATED),
        "known_bad": [
            # a reviewer that simply believes every annotation
            json.dumps({g: ("safe" if EXPECTED_L[g] == "vulnerable" else "vulnerable") for g in EXPECTED_L}),
            json.dumps(EXPECTED_L | {"g1": "safe"}),
        ],
    },
]


tasks += [
    {
        "id": "interact-jwt-alg-confusion",
        "prompt": PROMPT_JWT,
        "grader": {"type": "json_fields", "fields": {
            "vulnerable": True, "vuln_type": {"in": ["alg_confusion"]}, "line": {"in": [25, 26, 27]}}},
        "metadata": {
            "category": "discovery_review",
            "why_hard": "DRAFT - rewrite in your own words: the verifier trusts a client-controlled 'alg' "
                        "header and reuses the public RSA key as the HS256 secret; no exploit string appears "
                        "in the prompt, the model must notice the trust boundary itself.",
        },
        "reference": json.dumps(EXPECTED_JWT),
        "known_bad": [
            json.dumps({"vulnerable": False, "vuln_type": "none", "line": 0}),
            json.dumps({"vulnerable": True, "vuln_type": "other", "line": 11}),
        ],
    },
    {
        "id": "interact-smuggling-boundary",
        "prompt": PROMPT_SMUGGLE,
        "grader": {"type": "json_fields", "fields": EXPECTED_SMUGGLE},
        "metadata": {
            "category": "discovery_review",
            "why_hard": "DRAFT - rewrite in your own words: front and back parse the SAME byte stream with "
                        "different rules (Content-Length vs Transfer-Encoding), so a smuggled request exists "
                        "only in the back-end's view; requires tracking byte offsets, not a known payload.",
        },
        "reference": json.dumps(EXPECTED_SMUGGLE),
        "known_bad": [
            json.dumps({"s1_count": 1, "s1_method": "POST", "s1_path": "/search",
                       "s2_count": 1, "s2_method": "POST", "s2_path": "/search"}),
            json.dumps({"s1_count": 2, "s1_method": "GET", "s1_path": "/admin",
                       "s2_count": 2, "s2_method": "GET", "s2_path": "/x"}),
        ],
    },
]

if "--check" in sys.argv:
    current = []
    if OUT.exists():
        current = [json.loads(line) for line in OUT.read_text(encoding="utf-8").splitlines() if line.strip()]
    fresh = [json.loads(json.dumps(t, ensure_ascii=False)) for t in tasks]
    if current == fresh:
        print(f"OK: the committed ground truth reproduces on Python {sys.version.split()[0]}")
        sys.exit(0)
    changed = [a["id"] for a, b in zip(fresh, current) if a != b] or ["(task list differs)"]
    print(f"MISMATCH on Python {sys.version.split()[0]}: ground truth differs for {changed}.\n"
          "The standard library behaves differently here. Tell the author which Python version you use.")
    sys.exit(1)

OUT.parent.mkdir(exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    for t in tasks:
        f.write(json.dumps(t, ensure_ascii=False) + "\n")

print(f"wrote {len(tasks)} tasks -> {OUT.relative_to(ROOT)}  (python {sys.version.split()[0]})")
for title, items, expected in [
    ("Task A (upload pipeline)", INPUTS_A, EXPECTED_A),
    ("Task D (SSRF allow-list)", INPUTS_D, EXPECTED_D),
    ("Task E (sign-up)", INPUTS_E, EXPECTED_E),
]:
    print(f"\n{title} ground truth (computed by execution):")
    for i, raw in items:
        print(f"  {i:<4} {ascii(raw):<48} -> {expected[i]}")
print("\nTask L (library safety) ground truth (computed by executing proofs of concept):")
for gid, (fname, planned, _) in LIB_CASES.items():
    print(f"  {gid:<4} {fname:<26} -> {EXPECTED_L[gid]}")
print("\nTask S (large router, stateful) ground truth (computed by execution):")
print("  " + "  ".join(f"{rid}={EXPECTED_S[rid]}" for rid, *_ in REQS_S))
from collections import Counter  # noqa: E402
print("\nScale tasks status distribution:",
      {"64": dict(Counter(EXPECTED_64.values())), "128": dict(Counter(EXPECTED_128.values()))})
print("\nTask B (router) ground truth (computed by execution):")
for rid, m, p, u in REQS:
    print(f"  {rid:<4} {m:<6} {p!r:<22} as {u:<6} -> {EXPECTED_B[rid]}")
