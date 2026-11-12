# g3
# Threat model: `template` is attacker-controlled text. It must never be able to reveal anything other than the
# values of the intended placeholders (for example module-level secrets).
SECRET_KEY = "hunter2-do-not-leak"


class User:
    def __init__(self, name):
        self.name = name


def render_greeting(template, user):
    return template.format(user=user)
