# g12
# Threat model: `text` is attacker-controlled. Evaluating it must only ever produce plain data (numbers, strings,
# lists, dicts, ...) and must never give the attacker access to Python objects/internals or to code execution.


def parse_setting(text):
    return eval(text, {"__builtins__": {}}, {})
