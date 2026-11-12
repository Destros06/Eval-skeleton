# g7
# Threat model: `column` is attacker-controlled. It must never be able to change the structure of the SQL statement.
import re


def list_users(db, column):
    if not re.match(r"[a-z_]+", column):
        raise ValueError("invalid column")
    return db.execute(f"SELECT name FROM users ORDER BY {column}").fetchall()
