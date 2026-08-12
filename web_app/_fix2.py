with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = (
    '*** GESTIÓN DE TAGS (NUEVO) ***"\n'
    '    "Si el usuario pregunta sobre asignar tags a recursos en Azure, usa la función assign_tags_azure. "\n'
    '    "Primero valida los tags con validate_azure_tags para asegurar que cumplen el esquema organizacional (businessOwner, env, etc.). "\n'
    '    "Los tags requeridos en Azure son: businessOwner (email del propietario) y env (dev, test, prod, etc.). "\n'
    '    "Siempre pide confirmación antes de asignar tags, especialmente en producción. "\n'
    '    "\\n\\nNo inventes cifras. Usa herramientas cuando la pregunta requiera datos de costos reales o acciones sobre tags. "\n'
    '    "Cierra con recomendaciones FinOps concretas y accionables. "\n'
    '    "Formatea montos en USD, usa listas y negritas para claridad, e interpreta los números como lo haría un experto enoptimización de costos."'
)

new = (
    'REGLAS OBLIGATORIAS:"\n'
    '    "\\n- SIEMPRE llama validate_azure_tags cuando el usuario pida validar un tag."\n'
    '    "\\n- SIEMPRE llama assign_tags_azure cuando el usuario pida asignar un tag."\n'
    '    "\\n- NUNCA digas que no puedes validar o asignar tags."\n'
    '    "\\n- Costos GCP: usa get_cost_*. Costos Azure: usa get_azure_cost_*."\n'
    '    "\\n\\nEsquema: businessOwner (email), env (dev/test/staging/prod), businessUnit, ac."\n'
    '    "\\nNo inventes cifras. Cierra con recomendaciones FinOps concretas."'
)

if old in content:
    content = content.replace(old, new)
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('OK: actualizado')
else:
    print('FAIL')
