with open("app.py", "r", encoding="utf-8") as f:
    content = f.read()

for fn in ["def get_azure_resources_without_tags", "def get_azure_resource_tags",
           "def _is_tag_query", "def _format_tags_response", "def ask_with_fallback", "TOOLS = ["]:
    print(f"{'OK' if fn in content else 'FALTA'}: {fn}")
