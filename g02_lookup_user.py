# g2
# Threat model: `name` is attacker-controlled. It must never be able to change the structure of the SQL statement.


def lookup_user(db, name):
    return db.execute("SELECT id, name FROM users WHERE name = ?", (name,)).fetchall()
