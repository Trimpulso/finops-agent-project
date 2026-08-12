with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

start = None
func_start = None
end = None

for i, line in enumerate(lines):
    if "def _is_tag_query" in line:
        start = i
    if start is not None and "def ask_with_fallback" in line:
        func_start = i
    if func_start is not None and i > func_start + 5 and line.strip() == "":
        end = i
        break

print(f"start={start}, func_start={func_start}, end={end}")

if start is None or func_start is None or end is None:
    print("ERROR: no se encontraron las funciones. Buscando manualmente...")
    for i, line in enumerate(lines):
        if "def ask_with_fallback" in line or "_is_tag_query" in line:
            print(f"{i}: {line.rstrip()}")
