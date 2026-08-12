with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Fix the broken error access with chr() codes
for i, line in enumerate(lines):
    if "chr(39)+chr(101)" in line:
        lines[i] = '            return f"Error al consultar Azure: {data.get(\'error\', \'desconocido\')}"\n'
        print(f"Fixed error access at line {i}")

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK")
