with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_tag_functions = [
    '# ============ TAG QUERY FUNCTIONS ============\n',
    '\n',
    'def get_azure_resources_without_tags(resource_group: str = None) -> str:\n',
    '    """\n',
    '    Consulta recursos de Azure que NO tienen tags asignados.\n',
    '    Usa Azure Resource Graph para encontrar recursos sin etiquetas.\n',
    '\n',
    '    Args:\n',
    '        resource_group: Nombre del resource group a filtrar (opcional).\n',
    '\n',
    '    Returns:\n',
    '        JSON con lista de recursos sin tags: name, type, resourceGroup, id.\n',
    '    """\n',
    '    st.write(f"Consultando recursos Azure sin tags...")\n',
    '    try:\n',
    '        subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")\n',
    '        token = _get_azure_access_token()\n',
    '        if resource_group:\n',
    '            query = f"Resources | where resourceGroup == \'{resource_group}\' | where isnull(tags) or tags == \'{{}}\' | project id, name, type, resourceGroup | limit 200"\n',
    '        else:\n',
    '            query = "Resources | where isnull(tags) or tags == \'{}\' | project id, name, type, resourceGroup | limit 200"\n',
    '        url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"\n',
    '        response = requests.post(\n',
    '            url,\n',
    '            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},\n',
    '            json={"query": query},\n',
    '            timeout=30,\n',
    '        )\n',
    '        response.raise_for_status()\n',
    '        data = response.json()\n',
    '        rows = data.get("data", [])\n',
    '        return json.dumps({"total_without_tags": len(rows), "resources": rows}, ensure_ascii=False)\n',
    '    except Exception as e:\n',
    '        return json.dumps({"error": str(e)}, ensure_ascii=False)\n',
    '\n',
    '\n',
    'def get_azure_resource_tags(resource_id: str) -> str:\n',
    '    """\n',
    '    Consulta los tags asignados a un recurso especifico de Azure.\n',
    '\n',
    '    Args:\n',
    '        resource_id: Full Azure resource ID (/subscriptions/{sub}/resourceGroups/{rg}/providers/...).\n',
    '\n',
    '    Returns:\n',
    '        JSON con los tags del recurso: resource_id, has_tags, tags dict.\n',
    '    """\n',
    '    st.write(f"Consultando tags del recurso Azure...")\n',
    '    try:\n',
    '        token = _get_azure_access_token()\n',
    '        url = f"https://management.azure.com{resource_id}?api-version=2023-07-01"\n',
    '        response = requests.get(\n',
    '            url,\n',
    '            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},\n',
    '            timeout=30,\n',
    '        )\n',
    '        response.raise_for_status()\n',
    '        data = response.json()\n',
    '        tags = data.get("tags") or {}\n',
    '        return json.dumps({"resource_id": resource_id, "has_tags": len(tags) > 0, "tags": tags, "tags_count": len(tags)}, ensure_ascii=False)\n',
    '    except Exception as e:\n',
    '        return json.dumps({"error": str(e), "resource_id": resource_id}, ensure_ascii=False)\n',
    '\n',
    '\n',
]

# Replace old tag section (399-449) with new functions
lines[399:450] = new_tag_functions

# Fix TOOLS list - find it and update
for i, line in enumerate(lines):
    if line.strip().startswith('TOOLS = ['):
        tools_start = i
        break

# Find end of TOOLS list
tools_end = tools_start
for i in range(tools_start+1, len(lines)):
    if lines[i].strip() == ']':
        tools_end = i
        break

new_tools = [
    'TOOLS = [\n',
    '    get_cost_summary_by_service,\n',
    '    get_cost_by_sku_for_service,\n',
    '    get_daily_trend_for_service,\n',
    '    get_azure_cost_summary_by_service,\n',
    '    get_azure_cost_by_meter_for_service,\n',
    '    get_azure_daily_trend_for_service,\n',
    '    get_azure_resources_without_tags,\n',
    '    get_azure_resource_tags,\n',
    ']\n',
]

lines[tools_start:tools_end+1] = new_tools

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('OK: tag query functions y TOOLS actualizados')
