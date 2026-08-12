with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find ask_with_fallback and replace it completely
start = end = None
for i, line in enumerate(lines):
    if "def _is_tag_query" in line:
        start = i
    if start and "def ask_with_fallback" in line:
        func_start = i
    if start and i > func_start + 5 and line.strip() == "":
        end = i
        break

new_func = [
    'def _is_tag_query(prompt: str) -> str:\n',
    '    p = prompt.lower()\n',
    '    no_tags = ["sin tag", "sin etiqueta", "no tiene tag", "no tienen tag",\n',
    '               "sin tags", "sin etiquetas", "recursos sin", "without tag",\n',
    '               "no tienen etiqueta", "recursos que no tienen"]\n',
    '    has_tags = ["tags del recurso", "etiquetas del recurso", "que tags tiene",\n',
    '                "ver tags", "mostrar tags", "tags asignados a", "que etiquetas tiene"]\n',
    '    if any(k in p for k in no_tags):\n',
    '        return "without"\n',
    '    if any(k in p for k in has_tags):\n',
    '        return "get"\n',
    '    return ""\n',
    '\n',
    '\n',
    'def _format_tags_response(result_json: str, query_type: str) -> str:\n',
    '    import json as _json\n',
    '    try:\n',
    '        data = _json.loads(result_json)\n',
    '        if "error" in data:\n',
    '            return f"Error al consultar Azure: {data[\'error\']}"\n',
    '        if query_type == "without":\n',
    '            total = data.get("total_without_tags", 0)\n',
    '            resources = data.get("resources", [])\n',
    '            if total == 0:\n',
    '                return "Todos los recursos en Azure tienen tags asignados."\n',
    '            lines_out = [f"Se encontraron **{total} recursos sin tags** en Azure:\\n"]\n',
    '            for r in resources[:20]:\n',
    '                name = r.get("name", r.get(1, "?"))\n',
    '                rtype = r.get("type", r.get(2, "?"))\n',
    '                rg = r.get("resourceGroup", r.get(3, "?"))\n',
    '                lines_out.append(f"- **{name}** | {rtype} | RG: {rg}")\n',
    '            if total > 20:\n',
    '                lines_out.append(f"\\n...y {total - 20} recursos mas.")\n',
    '            lines_out.append("\\n**Recomendacion FinOps:** Asigna al menos businessOwner y env a cada recurso para habilitar chargeback y governance.")\n',
    '            return "\\n".join(lines_out)\n',
    '        if query_type == "get":\n',
    '            tags = data.get("tags", {})\n',
    '            has = data.get("has_tags", False)\n',
    '            if not has:\n',
    '                return f"El recurso **no tiene tags asignados**. Recomendacion: asignar businessOwner, env, businessUnit y ac."\n',
    '            lines_out = [f"Tags del recurso ({data.get(\'tags_count\', 0)} tags):\\n"]\n',
    '            for k, v in tags.items():\n',
    '                lines_out.append(f"- **{k}**: {v}")\n',
    '            return "\\n".join(lines_out)\n',
    '    except Exception as e:\n',
    '        return f"Error procesando respuesta: {e}"\n',
    '\n',
    '\n',
    'def ask_with_fallback(prompt: str):\n',
    '    # Bypass Gemini entirely for tag queries - format response directly\n',
    '    tag_mode = _is_tag_query(prompt)\n',
    '    if tag_mode == "without":\n',
    '        result = get_azure_resources_without_tags()\n',
    '        return _format_tags_response(result, "without"), "azure-resource-graph"\n',
    '    if tag_mode == "get":\n',
    '        resource_id = prompt\n',
    '        result = get_azure_resource_tags(resource_id)\n',
    '        return _format_tags_response(result, "get"), "azure-resource-graph"\n',
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
    '            response = chat.send_message(prompt)\n',
    '            return response.text, model_name\n',
    '        except ResourceExhausted as e:\n',
    '            last_error = e\n',
    '            st.info(f"Cuota agotada en \'{model_name}\'. Intentando con el siguiente modelo...")\n',
    '            continue\n',
    '    raise last_error\n',
    '\n',
]

lines[start:end] = new_func

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK: bypass completo de Gemini para tag queries")
