import os
import requests

# URL pública de tu API de TimesFM activa en el puerto 8000
TIMESFM_API_URL = os.getenv("TIMESFM_API_URL", "https://crispy-space-funicular-69rxj49x5wghw6r-8000.app.github.dev/api/v1/forecast")

def obtener_prediccion_costos(fechas: list[str], valores: list[float], horizonte: int = 3):
    """
    Envía los datos de costos históricos al modelo fundacional TimesFM 2.5
    para proyectar el gasto futuro de la infraestructura multicloud.
    """
    payload = {
        "fechas": fechas,
        "valores": valores,
        "horizonte": horizonte
    }
    
    try:
        response = requests.post(TIMESFM_API_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("prediccion")
    except requests.exceptions.RequestException as e:
        print(f"Error al conectar con la API de TimesFM: {e}")
        return None