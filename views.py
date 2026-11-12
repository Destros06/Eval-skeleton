# views.py
from framework import route


def staff_only(fn):
    def wrapper(user):
        if not (user and user["role"] in ("staff", "admin")):
            return 403
        return fn(user)
    return wrapper


@route("GET", "/api/profile")
def profile(user):
    return 200


@route("GET", "/admin/users")
def admin_list_users(user):
    return 200


@route("POST", "/admin/users")
def admin_create_user(user):
    return 200


@staff_only
@route("GET", "/reports/summary")
def report_summary(user):
    return 200


@route("GET", "/reports/export")
@staff_only
def report_export(user):
    return 200
