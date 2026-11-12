# g11
# Threat model: `template` is attacker-controlled text. It must never be able to reveal anything other than the
# values of the intended placeholders (for example module-level secrets).
from string import Template

SECRET_KEY = "hunter2-do-not-leak"


class User:
    def __init__(self, name):
        self.name = name


def format_greeting(template, user):
    return Template(template).safe_substitute(name=user.name)
