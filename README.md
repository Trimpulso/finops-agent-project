# FinOps Chat Agent

Agente FinOps en Streamlit para consultar costos y gobernanza multicloud.

## Capacidades actuales

- GCP de prueba: costos desde BigQuery Billing Export y revision de labels obligatorios en recursos facturados.
- Azure de prueba: Cost Management API y Azure Resource Graph para costos y tags.
- Power BI Prosegur: inventario de reportes, paginas, datasets y playbooks DAX guiados.
- Pildoras FinOps: base de conocimiento local en `web_app/knowledge_base.txt`.
- Email: envio SMTP de respuestas del chat si se configuran credenciales Gmail.
- Voz: entrada por microfono en el navegador usando Web Speech API.
- Futuro: AWS Cost Explorer o CUR/Athena para analisis de costos AWS.

## Ejecucion local

```powershell
cd D:\Github\finops-agent-project
.\.venv\Scripts\python.exe -m streamlit run .\web_app\app.py
```

## Credenciales locales

Durante la etapa de pruebas se pueden usar variables de entorno, `.env` o `web_app/.streamlit/secrets.toml`.
No subir credenciales al repositorio. Usa `.env.example` como plantilla.

Variables requeridas segun capacidad:

- `GEMINI_API_KEY`
- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `AZURE_SUBSCRIPTION_ID`
- `POWERBI_TENANT_ID`
- `POWERBI_CLIENT_ID`
- `POWERBI_CLIENT_SECRET`
- `POWERBI_WORKSPACE_ID`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`

## Despliegue Cloud Run

Servicio unico recomendado:

- `finops-agent-webapp`

```powershell
cd D:\Github\finops-agent-project
gcloud run deploy finops-agent-webapp --source . --region us-central1 --allow-unauthenticated
```

## Cloud Functions GCP

Las funciones auxiliares se despliegan desde:

- `cloud_functions/get_daily_spend_function`
- `cloud_functions/list_projects_function`

```powershell
gcloud functions deploy get-daily-spend --gen2 --runtime=python312 --region=us-central1 --source=.\cloud_functions\get_daily_spend_function --entry-point=get_daily_spend --trigger-http --allow-unauthenticated
gcloud functions deploy list-projects --gen2 --runtime=python312 --region=us-central1 --source=.\cloud_functions\list_projects_function --entry-point=list_projects --trigger-http --allow-unauthenticated
```
