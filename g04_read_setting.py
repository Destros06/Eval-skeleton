# g4
# Threat model: `text` is attacker-controlled. Evaluating it must only ever produce plain data (numbers, strings,
# lists, dicts, ...) and must never give the attacker access to Python objects/internals or to code execution.
import ast


def read_setting(text):
    return ast.literal_eval(text)
