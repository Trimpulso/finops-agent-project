@'
#!/usr/bin/env python3
import google.generativeai as genai
import subprocess
import os

# Configura Gemini con tu API key
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_KEY:
    raise ValueError("❌ GEMINI_API_KEY no configurada")

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

def read_file(filepath):
    """Lee un archivo"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(filepath, content):
    """Escribe un archivo"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

def auto_modify_app(task: str):
    """Agent que modifica app.py automáticamente usando Gemini"""
    
    print(f"📝 Tarea: {task}")
    
    # 1. Lee app.py actual
    print("📖 Leyendo app.py...")
    current_code = read_file('web_app/app.py')
    code_preview = current_code[:2000]
    
    # 2. Pide a Gemini que genere el cambio
    print("🤖 Consultando Gemini...")
    prompt = f"""Eres un experto en Python y Streamlit.

TAREA: {task}

CÓDIGO ACTUAL (inicio):
```python
{code_preview}
...