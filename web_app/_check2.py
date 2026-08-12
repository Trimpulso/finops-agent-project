with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('GESTIÓN DE TAGS')
print(repr(content[idx-5:idx+600]))
