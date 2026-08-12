with open("app.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    'CONVERSATIONS_TABLE = f"{DEFAULT_PROJECT_ID}.finops_agent.conversations"',
    'CONVERSATIONS_TABLE = "project-5f47ed36-9aec-4f46-a30.finops_agent.conversations"'
)

with open("app.py", "w", encoding="utf-8") as f:
    f.write(content)

print("OK: CONVERSATIONS_TABLE corregido con literal")
