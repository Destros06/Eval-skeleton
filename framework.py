# framework.py  (tiny in-house router)
ROUTES = {}


def route(method, path):
    def register(fn):
        ROUTES[(method, path)] = fn
        return fn
    return register


def normalize(path):
    # The router is deliberately forgiving: case-insensitive, ignores
    # repeated and trailing slashes.
    parts = [p for p in path.lower().split("/") if p]
    return "/" + "/".join(parts)


def dispatch(method, path, user, middleware=()):
    for mw in middleware:
        response = mw(method, path, user)
        if response is not None:
            return response
    handler = ROUTES.get((method, normalize(path)))
    if handler is None:
        return 404
    return handler(user)
