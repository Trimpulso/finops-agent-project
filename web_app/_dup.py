with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

print("=== Todas las definiciones de funciones clave ===")
for i, line in enumerate(lines):
    s = line.strip()
    if s.startswith("def ask_with_fallback") or s.startswith("def _is_tag_query") or s.startswith("def _format_tags"):
        print(f"{i}: {s}")

print("\n=== Ocurrencias de tag_mode y DEBUG ===")
for i, line in enumerate(lines):
    if "tag_mode" in line or "DEBUG" in line:
        print(f"{i}: {line.rstrip()}")

print(f"\nTotal lineas: {len(lines)}")
