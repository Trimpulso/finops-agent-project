with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if line.strip() == 'result = get_azure_resource_tags(prompt)':
        indent = line[:len(line) - len(line.lstrip())]
        lines[i] = (
            indent + 'import re as _re\n' +
            indent + 'm = _re.search(r"/subscriptions/\\S+", prompt)\n' +
            indent + 'resource_id = m.group(0) if m else prompt\n' +
            indent + 'result = get_azure_resource_tags(resource_id)\n'
        )
        print(f"Fixed resource_id extraction at line {i}")
        break

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK")
