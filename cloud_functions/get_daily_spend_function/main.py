import functions_framework
from google.cloud import bigquery

PROJECT_ID = "project-5f47ed36-9aec-4f46-a30"
DATASET_NAME = "billing_export"
TABLE_NAME = "gcp_billing_export_v1_011B52_EC7045_1117AB"

@functions_framework.http
def get_daily_spend(request):
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    }
    if request.method == 'OPTIONS':
        return ('', 204, headers)

    try:
        client = bigquery.Client(project=PROJECT_ID)
        
        project_id_filter = ""
        request_json = request.get_json(silent=True)
        if request_json and 'project_id' in request_json:
            project_id = request_json['project_id']
            project_id_filter = f"AND project.id = '{project_id}'"

        query = f"""
            SELECT SUM(cost) as total_cost
            FROM `{PROJECT_ID}.{DATASET_NAME}.{TABLE_NAME}`
            WHERE TRUE {project_id_filter}
        """
        query_job = client.query(query)
        rows = query_job.result()
        
        total_cost = 0
        for row in rows:
            # Si el costo es None (porque no hay datos), lo tratamos como 0
            total_cost = row.total_cost if row.total_cost is not None else 0
            break 
        
        return ({"total_cost": total_cost}, 200, headers)

    except Exception as e:
        print(f"Error: {e}")
        return ({"error": str(e)}, 500, headers)