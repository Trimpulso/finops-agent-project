import os
import requests

api_key = os.getenv("GEMINI_API_KEY")
print(f"Testing API key: {api_key[:20]}...")

url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": api_key,
}
payload = {
    "contents": [{"parts": [{"text": "Hola, test"}]}],
}

response = requests.post(url, headers=headers, json=payload, timeout=10)
print(f"Status: {response.status_code}")
print(f"Response: {response.text[:500]}")
