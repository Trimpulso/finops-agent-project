with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find TAG MANAGEMENT section start and TOOLS start
tag_start = None
tools_start = None
for i, line in enumerate(lines):
    if '# ============ TAG MANAGEMENT FUNCTIONS ============' in line:
        tag_start = i
    if line.strip().startswith('TOOLS = ['):
        tools_start = i
        break

print(f'TAG section starts at line: {tag_start}')
print(f'TOOLS starts at line: {tools_start}')
