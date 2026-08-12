with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if any(k in line for k in ["def _is_tag_query", "def _format_tags", "def ask_with_fallback",
                                 "tag_mode", "azure-resource-graph", "return _format_tags"]):
        print(f"{i}: {line.rstrip()}")
