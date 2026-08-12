with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if line.strip().startswith("_REQUIRED_TAGS"):
        insert_at = i
        break

kb_lines = [
    "\n",
    "# FinOps knowledge base cargado al inicio\n",
    "_KB_PATH = os.path.join(os.path.dirname(__file__), \"knowledge_base.txt\")\n",
    "FINOPS_KNOWLEDGE = open(_KB_PATH, encoding=\"utf-8\").read() if os.path.exists(_KB_PATH) else \"\"\n",
    "\n",
    "# Tabla BigQuery donde se registran las conversaciones para mejoras futuras\n",
    "CONVERSATIONS_TABLE = f\"{DEFAULT_PROJECT_ID}.finops_agent.conversations\"\n",
    "\n",
    "\n",
    "def log_conversation(pregunta: str, respuesta: str, modelo: str, categoria: str = \"general\") -> None:\n",
    "    try:\n",
    "        client = _bq_client()\n",
    "        row = {\n",
    "            \"timestamp\": datetime.utcnow().isoformat(),\n",
    "            \"pregunta_usuario\": pregunta,\n",
    "            \"respuesta_agente\": respuesta,\n",
    "            \"modelo\": modelo,\n",
    "            \"categoria_detectada\": categoria,\n",
    "        }\n",
    "        client.insert_rows_json(CONVERSATIONS_TABLE, [row])\n",
    "    except Exception:\n",
    "        pass  # el logging nunca debe romper la respuesta al usuario\n",
    "\n",
]

lines[insert_at:insert_at] = kb_lines

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK: KB loader y log_conversation insertados")
