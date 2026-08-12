with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find ask_with_fallback function
for i, line in enumerate(lines):
    if 'def ask_with_fallback' in line:
        func_start = i
        break

print(f'ask_with_fallback starts at line: {func_start}')
for line in lines[func_start:func_start+20]:
    print(line, end='')
