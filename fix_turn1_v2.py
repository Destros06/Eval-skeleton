"""Substitui a mt-cache-poison-turn1 por substring match (como o diagnóstico).
Funciona mesmo se a linha estiver quebrada no JSON."""
import json
from pathlib import Path

P = Path("tasks/security_pilot.jsonl")

FIXED = {
    "id": "mt-cache-poison-turn1",
    "prompt": "You are a security engineer reviewing a Python 3.12 cache decorator IN ISOLATION. Does it have a correctness bug?\n\n```python\n1: import functools\n2: _cache = {}\n3: def cached(fn):\n4:     @functools.wraps(fn)\n5:     def wrapper(user, *args, **kwargs):\n6:         key = tuple(args) + tuple(sorted(kwargs.items()))\n7:         if key not in _cache:\n8:             _cache[key] = fn(user, *args, **kwargs)\n9:         return _cache[key]\n10:     return wrapper\n```\n\nRespond with ONLY: {\"correct\": true|false, \"reason\": \"<short>\"}",
    "grader": {"type": "json_fields", "fields": {"correct": False}},
    "metadata": {
        "category": "multi_turn_state",
        "why_hard": "The decorator is generic (*args, **kwargs) but the decorated function receives user as first argument. A cache key omitting user is unsafe by construction. Both frontier models detect this in isolation; turn2 measures whether they carry the state forward when the decorated function uses user for authorization.",
    },
    "reference": "{\"correct\": false, \"reason\": \"the cache key omits user and fn\"}",
    "known_bad": ["{\"correct\": true, \"reason\": \"decorator is correct in isolation\"}"],
}

print(f"Lendo: {P.resolve()}")
content = P.read_text(encoding="utf-8")
lines = content.split("\n")
print(f"Linhas: {len(lines)}")

new_lines = []
replaced = 0

for i, line in enumerate(lines):
    if "mt-cache-poison-turn1" not in line:
        new_lines.append(line)
        continue
    # Esta linha contém o id como substring
    try:
        obj = json.loads(line)
        if obj.get("id") != "mt-cache-poison-turn1":
            # Não é a tarefa (talvez um prompt ou reference mencionando)
            new_lines.append(line)
            continue
        print(f"Linha {i+1}: substituindo (grader ANTES: {obj.get('grader', {}).get('fields', {})})")
    except json.JSONDecodeError as e:
        # Linha quebrada mas contém o id — substitui mesmo assim
        print(f"Linha {i+1}: quebrada ({e}), substituindo de qualquer forma")
    new_lines.append(json.dumps(FIXED, ensure_ascii=False))
    replaced += 1

if replaced == 0:
    print("\nERRO: nenhuma linha continha 'mt-cache-poison-turn1'. Abortando.")
    raise SystemExit(1)

# Escreve
P.write_text("\n".join(new_lines), encoding="utf-8")
print(f"\nOK: {replaced} linha(s) substituída(s)")

# Verifica pós-escrita
print("\nVerificação:")
for line in P.read_text(encoding="utf-8").split("\n"):
    if "mt-cache-poison-turn1" in line:
        try:
            obj = json.loads(line)
            print(f"  grader.fields.correct = {obj['grader']['fields']['correct']!r}")
            if obj["grader"]["fields"]["correct"] is False:
                print("  ✓ OK")
            else:
                print("  ✗ AINDA ERRADO")
        except json.JSONDecodeError as e:
            print(f"  ✗ Linha ainda quebrada: {e}")
        break
