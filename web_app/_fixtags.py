with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Fix get_azure_resources_without_tags: wrong API URL and KQL
for i, line in enumerate(lines):
    if "Microsoft.ResourceGraph/resources?api-version=2021-03-01" in line:
        # Resource Graph API goes at root level, not under subscriptions
        lines[i] = '        url = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"\n'
        print(f"Fixed URL at line {i}")
    if "where isnull(tags) or tags == " in line:
        # Fix KQL: use todynamic for empty object comparison
        if "resource_group" in "".join(lines[max(0,i-5):i+5]):
            lines[i] = '            query = f"Resources | where resourceGroup =~ \'{resource_group}\' | where isnull(tags) or array_length(bag_keys(tags)) == 0 | project id, name, type, resourceGroup | limit 200"\n'
        else:
            lines[i] = '            query = "Resources | where isnull(tags) or array_length(bag_keys(tags)) == 0 | project id, name, type, resourceGroup | limit 200"\n'
        print(f"Fixed KQL at line {i}")

# Also fix the body: Resource Graph needs subscriptions in the body
for i, line in enumerate(lines):
    if 'json={"query": query}' in line and "ResourceGraph" in "".join(lines[max(0,i-15):i]):
        lines[i] = '            json={"query": query, "subscriptions": [subscription_id]},\n'
        print(f"Fixed request body at line {i}")

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK: get_azure_resources_without_tags corregido")
