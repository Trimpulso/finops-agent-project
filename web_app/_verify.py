with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'TOOLS' in line or 'get_azure_resources_without' in line or 'get_azure_resource_tags' in line:
        print(f'{i}: {line}', end='')
