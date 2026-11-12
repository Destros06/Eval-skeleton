# middleware.py


def require_login(method, path, user):
    if path.startswith("/api/") and user is None:
        return 401


def admin_only(method, path, user):
    if path.startswith("/admin") and not (user and user.get("role") == "admin"):
        return 403
