with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "DEFAULT_PROJECT_ID =" in line or "CONVERSATIONS_TABLE" in line or "_KB_PATH" in line or "FINOPS_KNOWLEDGE =" in line or "_REQUIRED_TAGS" in line:
        print(f"{i}: {line.rstrip()}")
