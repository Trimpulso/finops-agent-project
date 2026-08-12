import google.auth
from google.cloud import bigquery
from datetime import datetime, timedelta
import google.generativeai as genai
import json
import os

# --- HERRAMIENTAS (Nuestras funciones que consultan BigQuery) ---

def get_cost_summary_by_service(project_id: str, days: int = 30):
    """
    Obtiene un resumen de costos agrupados por servicio para un proyecto de GCP en los últimos 'days' días.
    """
    try:
        credentials, project = google.auth.default()
        client = bigquery.Client(credentials=credentials, project=project)
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        query = f"""
            SELECT service.description AS service, SUM(cost) AS total_cost
            FROM `project-5f47ed36-9aec-4f46-a30.billing_export.gcp_billing_export_v1_011B52_EC7045_1117AB`
            WHERE project.id = '{project_id}' AND DATE(usage_start_time) >= '{start_date}'
            GROUP BY service HAVING total_cost > 0.01 ORDER BY total_cost DESC;
        """
        df = client.query(query).to_dataframe()
        return df.to_json(orient='records')
    except Exception as e:
        return json.dumps({"error": str(e)})

def get_daily_trend_for_service(project_id: str, service_name: str, days: int = 30):
    """
    Obtiene la tendencia de costo diario para un servicio específico en un proyecto de GCP en los últimos 'days' días.
    """
    try:
        credentials, project = google.auth.default()
        client = bigquery.Client(credentials=credentials, project=project)
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        query = f"""
            SELECT DATE(usage_start_time) AS cost_date, SUM(cost) AS daily_cost
            FROM `project-5f47ed36-9aec-4f46-a30.billing_export.gcp_billing_export_v1_011B52_EC7045_1117AB`
            WHERE project.id = '{project_id}' AND service.description = '{service_name}'
            AND DATE(usage_start_time) >= '{start_date}'
            GROUP BY cost_date ORDER BY cost_date ASC;
        """
        df = client.query(query).to_dataframe()
        df['cost_date'] = df['cost_date'].astype(str)
        return df.to_json(orient='records')
    except Exception as e:
        return json.dumps({"error": str(e)})

# --- CLASE DEL AGENTE ---

class FinOpsAgent:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Se requiere una clave de API de Gemini.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name='gemini-1.5-flash-latest',
            tools=[get_cost_summary_by_service, get_daily_trend_for_service]
        )
        # El project_id es fijo por ahora, pero podría ser un parámetro
        self.project_id = "project-5f47ed36-9aec-4f46-a30"

    def get_response(self, user_prompt: str) -> str:
        """
        Procesa un prompt del usuario y devuelve la respuesta del LLM.
        """
        try:
            # Pasamos el project_id a las herramientas que lo necesiten
            prompt_with_context = f"Para el proyecto '{self.project_id}', {user_prompt}"
            
            chat = self.model.start_chat(enable_automatic_function_calling=True)
            response = chat.send_message(prompt_with_context)
            
            return response.text
        except Exception as e:
            return f"Error al procesar la solicitud: {str(e)}"
