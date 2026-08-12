import functions_framework
from google.cloud import bigquery
import os
import json

# --- Configuración ---
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT", "project-5f47ed36-9aec-4f46-a30")
DATASET_ID = "billing_export"
TABLE_ID = "gcp_billing_export_v1_011B52_EC7045_1117AB"

@functions_framework.http
def list_projects(request):
    """
    Consulta la tabla de facturación y devuelve una lista de IDs de proyecto únicos.
    """
    try:
        client = bigquery.Client()

        query = f"""
            SELECT DISTINCT project.id
            FROM `{GCP_PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`
            WHERE project.id IS NOT NULL
            ORDER BY project.id
        """

        query_job = client.query(query)
        results = query_job.result()

        project_ids = [row.id for row in results]

        # Devuelve la lista de proyectos como una respuesta JSON
        return json.dumps(project_ids), 200, {'Content-Type': 'application/json'}

    except Exception as e:
        error_message = f"Error al listar los proyectos: {e}"
        print(error_message)
        return json.dumps({"error": str(e)}), 500, {'Content-Type': 'application/json'}