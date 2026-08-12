import google.auth
from google.cloud import bigquery
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
BILLING_TABLE = "project-5f47ed36-9aec-4f46-a30.billing_export.gcp_billing_export_v1_011B52_EC7045_1117AB"

# --- LÓGICA DEL AGENTE ---

def get_monthly_cost_by_service(client, project_id, start_date):
    """Muestra los costos agrupados por servicio."""
    print(f"\n--- 1. Obteniendo Resumen de Costos por Servicio ---")
    query = f"""
        SELECT service.description AS item, SUM(cost) AS total_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = @project_id AND DATE(usage_start_time) >= DATE(@start_date)
        GROUP BY item HAVING total_cost > 0 ORDER BY total_cost DESC;
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("start_date", "STRING", start_date),
        ]
    )
    query_job = client.query(query, job_config=job_config)
    
    results = list(query_job)
    if not results:
        print("No se encontraron costos para este proyecto en el periodo seleccionado.")
        return None

    total_project_cost = 0
    print(f"\nProyecto: {project_id} | Costos desde {start_date}\n")
    for row in results:
        print(f"- {row.item:<30} | ${row.total_cost:.2f}")
        total_project_cost += row.total_cost
    
    print("-------------------------------------------------")
    print(f"Costo Total del Proyecto (Últimos 30 días): ${total_project_cost:.2f}")
    return results

def get_cost_by_sku_for_service(client, project_id, start_date, service_name):
    """Muestra los costos de un servicio específico, desglosados por SKU."""
    print(f"\n--- 2. Analizando el costo de '{service_name}' en detalle (por SKU) ---")
    query = f"""
        SELECT sku.description AS item, SUM(cost) AS total_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = @project_id
          AND service.description = @service_name
          AND DATE(usage_start_time) >= DATE(@start_date)
        GROUP BY item HAVING total_cost > 0 ORDER BY total_cost DESC;
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("service_name", "STRING", service_name),
            bigquery.ScalarQueryParameter("start_date", "STRING", start_date),
        ]
    )
    query_job = client.query(query, job_config=job_config)

    results = list(query_job)
    if not results:
        print(f"No se encontraron detalles de SKU para '{service_name}'.")
        return

    print(f"\nDesglose de '{service_name}':\n")
    for row in results:
        print(f"- {row.item:<40} | ${row.total_cost:.2f}")
    print("-------------------------------------------------")

def get_daily_cost_trend(client, project_id, start_date, service_name):
    """Muestra la tendencia de costo diario para un servicio específico."""
    print(f"\n--- 3. Analizando la tendencia diaria para '{service_name}' ---")
    query = f"""
        SELECT
            DATE(usage_start_time) AS cost_date,
            SUM(cost) AS daily_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = @project_id
          AND service.description = @service_name
          AND DATE(usage_start_time) >= DATE(@start_date)
        GROUP BY cost_date
        ORDER BY cost_date ASC;
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("service_name", "STRING", service_name),
            bigquery.ScalarQueryParameter("start_date", "STRING", start_date),
        ]
    )
    query_job = client.query(query, job_config=job_config)

    results = list(query_job)
    if not results:
        print(f"No se pudo generar la tendencia diaria para '{service_name}'.")
        return

    print(f"\nCosto diario de '{service_name}':\n")
    max_cost = max(row.daily_cost for row in results) if results else 1
    for row in results:
        # Simple visualización de la barra de costo, escalada al día de mayor costo
        bar_length = int((row.daily_cost / max_cost) * 50) if max_cost > 0 else 0
        bar = "█" * bar_length
        print(f"- {row.cost_date} | ${row.daily_cost:>7.2f} | {bar}")
    print("-------------------------------------------------")


# --- EJECUCIÓN ---
if __name__ == "__main__":
    try:
        credentials, project = google.auth.default()
        client = bigquery.Client(credentials=credentials, project=project)

        today = datetime.now()
        # Analizaremos los últimos 30 días para tener una mejor tendencia
        start_date = (today - timedelta(days=30)).strftime('%Y-%m-%d')
        
        target_project_id = "project-5f47ed36-9aec-4f46-a30"

        # Paso 1: Resumen general
        summary_results = get_monthly_cost_by_service(client, target_project_id, start_date)

        if summary_results:
            most_expensive_service = summary_results[0].item
            # Paso 2: Desglose por SKU del servicio más caro
            get_cost_by_sku_for_service(client, target_project_id, start_date, most_expensive_service)
            # Paso 3: Análisis de tendencia diaria del servicio más caro
            get_daily_cost_trend(client, target_project_id, start_date, most_expensive_service)

    except Exception as e:
        print(f"\nError: Ocurrió un problema durante la ejecución.")
        print(f"Detalle: {e}")