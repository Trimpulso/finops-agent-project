with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "final_response, used_model = ask_with_fallback" in line:
        print(f"Linea {i}: {line.rstrip()}")
        for j in range(i, min(i+6, len(lines))):
            print(f"{j}: {lines[j].rstrip()}")
        break
