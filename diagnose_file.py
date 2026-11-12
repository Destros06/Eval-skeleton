"""Diagnóstico completo do arquivo. Não modifica nada."""
import json
from pathlib import Path

P = Path("tasks/security_pilot.jsonl")

print(f"Arquivo: {P.resolve()}")
print(f"Existe?  {P.exists()}")
if not P.exists():
    print("\nERRO: arquivo não existe no caminho acima.")
    print(f"cwd atual: {Path.cwd()}")
    raise SystemExit(1)

content = P.read_text(encoding="utf-8")
lines = content.split("\n")
print(f"Tamanho: {len(content)} caracteres")
print(f"Linhas (split \\n): {len(lines)}")
print(f"Ocorrências de 'mt-cache-poison-turn1': {content.count('mt-cache-poison-turn1')}")
print()

# Procura cada linha que menciona o id
found_any = False
for i, line in enumerate(lines):
    if "mt-cache-poison-turn1" not in line:
        continue
    found_any = True
    print(f"--- Linha {i+1} (índice {i}) ---")
    print(f"  Tamanho: {len(line)} chars")
    print(f"  Primeiros 80 chars: {line[:80]!r}")
    print(f"  Últimos 40 chars:   {line[-40:]!r}")
    try:
        obj = json.loads(line)
        print(f"  ✓ Parse OK")
        print(f"  id: {obj.get('id')!r}")
        print(f"  grader: {obj.get('grader', {}).get('fields', {})}")
    except json.JSONDecodeError as e:
        print(f"  ✗ JSONDecodeError: {e}")
        print(f"  (mas a linha contém o id como substring)")

if not found_any:
    print("\nNenhuma linha contém 'mt-cache-poison-turn1' como substring.")
    print("Primeiras 3 linhas do arquivo:")
    for i, line in enumerate(lines[:3], 1):
        print(f"  {i}: {line[:100]!r}")
