# app.py
from framework import dispatch
from middleware import require_login, admin_only
import views  # noqa: F401  (importing registers the routes)

MIDDLEWARE = [require_login, admin_only]


def handle(method, path, user):
    return dispatch(method, path, user, MIDDLEWARE)
