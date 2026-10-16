import os
import requests

def obtener_prediccion_costos(data_historica: list, horizon: int = 12):
    url = os.getenv("TIMESFM_API_URL", "https://scaling-yodel-9pv4w6jq5v62p977-8000.app.github.dev/api/v1/forecast")
    payload = {
        "data": data_historica,
        "horizon": horizon
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            res_json = response.json()
            return res_json.get("forecast", [])
        else:
            return []
    except Exception as e:
        print(f"Error conectando con TimesFM API: {e}")
        return []
