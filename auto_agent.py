import google.generativeai as genai
import subprocess
import os
import sys

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_KEY:
    raise ValueError("GEMINI_API_KEY no configurada")

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

def auto_modify_app(task: str):
    print(f"📝 Tarea: {task}\n")
    print("📖 Leyendo app.py...")
    with open('web_app/app.py', 'r', encoding='utf-8') as f:
        current_code = f.read()
    
    print("🤖 Consultando Gemini...")
    prompt = f"""Eres experto en Python y Streamlit. 
TAREA: {task}

El código actual tiene ~835 líneas con:
- Streamlit UI para chat
- Integración con BigQuery, Azure, Power BI
- Gemini como modelo de IA
- Sistema de memoria conversacional

REQUISITOS PARA EL NUEVO CÓDIGO:
1. MANTÉN toda la funcionalidad existente
2. AGREGA soporte de audio:
   - st.file_uploader para WAV/MP3
   - google.cloud.speech para transcripción
   - El texto transcrito se usa como pregunta
3. La sintaxis DEBE ser Python válido (sin try sin except)
4. Retorna SOLO código Python, sin markdown

IMPORTANTE: Asegúrate de que TODOS los try tengan except o finally"""

    response = model.generate_content(prompt)
    new_code = response.text.strip()
    
    if "```python" in new_code:
        new_code = new_code.split("```python")[1].split("```")[0].strip()
    elif "```" in new_code:
        new_code = new_code.split("```")[1].split("```")[0].strip()
    
    try:
        compile(new_code, '<string>', 'exec')
        print("✅ Código válido\n")
    except SyntaxError as e:
        print(f"❌ Error de sintaxis: {e}")
        print("Reintentando con correcciones...")
        sys.exit(1)
    
    with open('web_app/app.py', 'w', encoding='utf-8') as f:
        f.write(new_code)
    print("💾 app.py guardado")
    print("🚀 Desplegando...")
    
    os.system("gcloud run deploy finops-agent --source . --region us-central1 --platform managed --allow-unauthenticated")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python auto_agent.py '<tarea>'")
        sys.exit(1)
    auto_modify_app(sys.argv[1])
