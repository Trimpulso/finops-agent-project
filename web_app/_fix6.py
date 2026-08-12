with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find end of ask_with_fallback
func_start = 491
func_end = func_start
for i in range(func_start+1, len(lines)):
    if lines[i].strip() == '' and i > func_start + 5:
        func_end = i
        break

print(f'Function ends at line: {func_end}')

new_func = [
    'def _is_tag_query(prompt: str) -> str:\n',
    '    p = prompt.lower()\n',
    '    no_tags = ["sin tag", "sin etiqueta", "no tiene tag", "no tienen tag", "without tag",\n',
    '               "sin tags", "sin etiquetas", "no tienen etiqueta", "recursos sin"]\n',
    '    has_tags = ["tags del recurso", "etiquetas del recurso", "que tags tiene",\n',
    '                "ver tags", "mostrar tags", "tags asignados a"]\n',
    '    if any(k in p for k in no_tags):\n',
    '        return "without"\n',
    '    if any(k in p for k in has_tags):\n',
    '        return "get"\n',
    '    return ""\n',
    '\n',
    '\n',
    'def ask_with_fallback(prompt: str):\n',
    '    # Direct routing for tag queries to bypass Gemini function calling decision\n',
    '    tag_mode = _is_tag_query(prompt)\n',
    '    tool_result = None\n',
    '    if tag_mode == "without":\n',
    '        tool_result = get_azure_resources_without_tags()\n',
    '        enriched = f"{prompt}\\n\\n[Datos de herramienta get_azure_resources_without_tags]: {tool_result}"\n',
    '    elif tag_mode == "get":\n',
    '        tool_result = None\n',
    '        enriched = prompt\n',
    '    else:\n',
    '        enriched = prompt\n',
    '\n',
    '    last_error = None\n',
    '    for model_name in MODEL_NAMES:\n',
    '        try:\n',
    '            model = genai.GenerativeModel(\n',
    '                model_name=model_name,\n',
    '                tools=TOOLS,\n',
    '                system_instruction=SYSTEM_INSTRUCTION,\n',
    '            )\n',
    '            chat = model.start_chat(enable_automatic_function_calling=True)\n',
    '            response = chat.send_message(enriched)\n',
    '            return response.text, model_name\n',
    '        except ResourceExhausted as e:\n',
    '            last_error = e\n',
    '            st.info(f"Cuota agotada en \'{model_name}\'. Intentando con el siguiente modelo...")\n',
    '            continue\n',
    '    raise last_error\n',
    '\n',
]

lines[func_start:func_end+1] = new_func

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('OK: ask_with_fallback actualizado con routing de tags')
