# FinOps Chat Agent

Agente FinOps desplegado en **Google Cloud Platform (GCP)** para consultar costos y gobernanza multicloud (GCP, Azure, AWS y Power BI).

## Arquitectura en GCP

- **Cloud Run:** Servicio contenedorizado `finops-agent-webapp` que ejecuta la interfaz y lógica en Streamlit.
- **Secret Manager:** Almacenamiento seguro y centralizado de todas las credenciales y claves API.
- **BigQuery:** Consultas directas sobre el dataset de facturación exportada de GCP.
- **Cloud Functions (Gen 2):** Funciones auxiliares para consultas programadas y métricas de gasto diario.

## Gestión de Credenciales y Secretos (Secret Manager)

La aplicación obtiene automáticamente todas las claves desde **Google Cloud Secret Manager** en el proyecto `project-5f47ed36-9aec-4f46-a30` sin requerir archivos locales de credenciales.

Secretos requeridos en GCP Secret Manager:
- **Gemini AI:** `gemini-api-key`
- **Azure:** `azure-tenant-id`, `azure-client-id`, `azure-client-secret`, `azure-subscription-id`
- **Power BI:** `powerbi-tenant-id`, `powerbi-client-id`, `powerbi-client-secret`, `powerbi-workspace-id`
- **Gmail SMTP:** `gmail-address`, `gmail-app-password`
- **AWS:** `aws-access-key-id`, `aws-secret-access-key`, `aws-default-region`

## Despliegue en Cloud Run

Para compilar y desplegar el servicio en GCP Cloud Run:

```bash
gcloud run deploy finops-agent-webapp \
  --project=project-5f47ed36-9aec-4f46-a30 \
  --source . \
  --region us-central1 \
  --allow-unauthenticated
```

### Permisos de la Cuenta de Servicio en Cloud Run
Asegurar que la cuenta de servicio asociada tenga el rol para leer Secret Manager:

```bash
gcloud projects add-iam-policy-binding project-5f47ed36-9aec-4f46-a30 \
  --member="serviceAccount:TU_SERVICE_ACCOUNT@project-5f47ed36-9aec-4f46-a30.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## Despliegue de Cloud Functions

Las funciones auxiliares se despliegan con:

```bash
gcloud functions deploy get-daily-spend \
  --project=project-5f47ed36-9aec-4f46-a30 \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=./cloud_functions/get_daily_spend_function \
  --entry-point=get_daily_spend \
  --trigger-http \
  --allow-unauthenticated

gcloud functions deploy list-projects \
  --project=project-5f47ed36-9aec-4f46-a30 \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=./cloud_functions/list_projects_function \
  --entry-point=list_projects \
  --trigger-http \
  --allow-unauthenticated
```
