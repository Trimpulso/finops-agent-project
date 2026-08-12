with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

start = end = None
for i, line in enumerate(lines):
    if line.strip().startswith("SYSTEM_INSTRUCTION = ("):
        start = i
    if start is not None and i > start and line.strip() == ")":
        end = i
        break

# Insertar linea de KB justo antes del cierre )
kb_inject = "    f\"\\n\\n=== CONOCIMIENTO FINOPS DE PROSEGUR ===\\n{FINOPS_KNOWLEDGE}\"\n"
lines.insert(end, kb_inject)

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK: KB inyectado en SYSTEM_INSTRUCTION")
