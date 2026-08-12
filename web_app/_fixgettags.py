with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find get_azure_resource_tags function boundaries
start = None
for i, line in enumerate(lines):
    if line.startswith("def get_azure_resource_tags"):
        start = i
    if start is not None and i > start and (lines[i].startswith("def ") or lines[i].startswith("# ====")) and i != start:
        end = i
        break

new_fn = [
    'def get_azure_resource_tags(resource_id: str) -> str:\n',
    '    """Consulta los tags de un recurso Azure usando Resource Graph (no requiere rol Reader)."""\n',
    '    st.write("Consultando tags del recurso Azure...")\n',
    '    try:\n',
    '        subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")\n',
    '        token = _get_azure_access_token()\n',
    '        rid = resource_id.strip().rstrip("/")\n',
    '        query = f"Resources | where id =~ \'{rid}\' | project id, name, type, tags | limit 1"\n',
    '        url = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"\n',
    '        response = requests.post(\n',
    '            url,\n',
    '            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},\n',
    '            json={"query": query, "subscriptions": [subscription_id]},\n',
    '            timeout=30,\n',
    '        )\n',
    '        response.raise_for_status()\n',
    '        rows = response.json().get("data", [])\n',
    '        if not rows:\n',
    '            return json.dumps({"error": "Recurso no encontrado en la suscripcion", "resource_id": rid}, ensure_ascii=False)\n',
    '        tags = rows[0].get("tags") or {}\n',
    '        return json.dumps({"resource_id": rid, "has_tags": len(tags) > 0, "tags": tags, "tags_count": len(tags)}, ensure_ascii=False)\n',
    '    except Exception as e:\n',
    '        return json.dumps({"error": str(e), "resource_id": resource_id}, ensure_ascii=False)\n',
    '\n',
    '\n',
]

lines[start:end] = new_fn

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"OK: get_azure_resource_tags reescrito con Resource Graph (lineas {start}-{end})")
