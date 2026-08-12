import functions_framework
from google.cloud import bigquery

PROJECT_ID = "project-5f47ed36-9aec-4f46-a30"
DATASET_NAME = "billing_export"
TABLE_NAME = "gcp_billing_export_v1_011B52_EC7045_1117AB"

@functions_framework.http
def list_projects(request):
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    }
    if request.method == 'OPTIONS':
        return ('', 204, headers)

    try:
        client = bigquery.Client(project=PROJECT_ID)
        query = f"""
            SELECT DISTINCT project.id
            FROM `{PROJECT_ID}.{DATASET_NAME}.{TABLE_NAME}`
            WHERE project.id IS NOT NULL
        """
        query_job = client.query(query)
        rows = query_job.result()
        
        project_ids = [row.id for row in rows]
        
        # Si no se encuentran proyectos, devolvemos una lista vacía
        # en lugar de un error.
        return ({"project_ids": project_ids}, 200, headers)

    except Exception as e:
        print(f"Error: {e}")
        return ({"error": str(e)}, 500, headers)