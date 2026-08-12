with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('GESTIÓN DE TAGS')
if idx > 0:
    print(repr(content[idx-10:idx+500]))
else:
    print('NOT FOUND')
    idx2 = content.find('SYSTEM_INSTRUCTION')
    print(repr(content[idx2:idx2+300]))
