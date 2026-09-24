import os
import sys

print("=== DEBUG: Variables de Entorno ===")
vars_to_check = [
    "GEMINI_API_KEY",
    "AZURE_TENANT_ID", 
    "AZURE_CLIENT_ID",
    "POWERBI_TENANT_ID"
]

for var in vars_to_check:
    val = os.getenv(var, "NO CONFIGURADA")
    if val:
        print(f"✓ {var}: {val[:20]}...")
    else:
        print(f"✗ {var}: NO CONFIGURADA")

print("\n=== Probando Gemini REST API directamente ===")
import requests

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("ERROR: GEMINI_API_KEY no está configurada!")
    sys.exit(1)

url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": api_key,
}
payload = {
    "contents": [{"parts": [{"text": "Test"}]}],
}

try:
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    print(f"Status: {response.status_code}")
    if response.status_code != 200:
        print(f"Error: {response.text[:500]}")
    else:
        print("✓ Gemini funciona!")
except Exception as e:
    print(f"Exception: {e}")
