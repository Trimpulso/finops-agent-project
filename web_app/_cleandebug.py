with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

lines = [l for l in lines if "DEBUG tag_mode" not in l]

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK: DEBUG removido")
