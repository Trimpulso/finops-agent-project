with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find start and end of SYSTEM_INSTRUCTION
start = None
end = None
for i, line in enumerate(lines):
    if line.strip().startswith('SYSTEM_INSTRUCTION = ('):
        start = i
    if start is not None and i > start and line.strip() == ')':
        end = i
        break

print(f'Found SYSTEM_INSTRUCTION at lines {start}-{end}')
if start and end:
    print(''.join(lines[start:end+1]))
