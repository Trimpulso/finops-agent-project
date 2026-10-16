
def send_email_smtp(to_address: str, subject: str, body: str) -> tuple[bool, str]:
    try:
        to_address = (to_address or "").strip()
        if not to_address:
            return False, "Debes indicar un correo destino."

        gmail_address = _get_setting("GMAIL_ADDRESS", required=False)
        gmail_password = _get_setting("GMAIL_APP_PASSWORD", required=False)
        if not gmail_address or not gmail_password:
            return False, "Falta configurar GMAIL_ADDRESS / GMAIL_APP_PASSWORD en Secret Manager o variables de entorno."

        msg = MIMEMultipart()
        msg["From"] = gmail_address
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, [to_address], msg.as_string())

        return True, f"Correo enviado a {to_address}."
    except Exception as e:
        return False, f"No se pudo enviar el correo: {e}"

def render_email_button(message_index: int, content: str) -> None:
    with st.expander("📧 Enviar esta respuesta por correo"):
        to_address = st.text_input(
            "Correo destino",
            key=f"email_to_{message_index}",
            placeholder="destinatario@ejemplo.com",
        )
        if st.button("Enviar por correo", key=f"email_send_{message_index}"):
            ok, info = send_email_smtp(
                to_address,
                subject="Respuesta FinOps Chat Agent",
                body=content,
            )
            if ok:
                st.success(info)
            else:
                st.error(info)

import warnings
warnings.filterwarnings("ignore")

import json
import os
import re
import smtplib
import time
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    import boto3
except ImportError:
    boto3 = None

import google.auth
import requests
import streamlit as st
from file_analysis import ALLOWED_FILE_TYPES, analyze_files
import powerbi_report
import powerbi_studio
try:
    from streamlit_mic_recorder import mic_recorder
except ImportError:
    mic_recorder = None
try:
    from speech_recognition import AudioData, Recognizer, RequestError, UnknownValueError
except ImportError:
    AudioData = Recognizer = RequestError = UnknownValueError = None
from google.api_core.exceptions import ResourceExhausted
from google.cloud import bigquery

DEFAULT_DAYS = 30

# Mapeo de nombres de variables de entorno a nombres de Secret Manager
SECRET_MANAGER_MAP = {
    "GEMINI_API_KEY": "gemini-api-key",
    "AZURE_CLIENT_SECRET": "azure-client-secret",
    "AZURE_CLIENT_ID": "azure-client-id",
    "AZURE_TENANT_ID": "azure-tenant-id",
    "AZURE_SUBSCRIPTION_ID": "azure-subscription-id",
    "POWERBI_CLIENT_SECRET": "powerbi-client-secret",
    "POWERBI_CLIENT_ID": "powerbi-client-id",
    "POWERBI_TENANT_ID": "powerbi-tenant-id",
    "POWERBI_WORKSPACE_ID": "powerbi-workspace-id",
    "GMAIL_ADDRESS": "gmail-address",
    "GMAIL_APP_PASSWORD": "gmail-app-password",
    "AWS_ACCESS_KEY_ID": "aws-access-key-id",
    "AWS_SECRET_ACCESS_KEY": "aws-secret-access-key",
    "AWS_DEFAULT_REGION": "aws-default-region",
}

def _fetch_from_secret_manager(secret_name: str, project_id: str = "project-5f47ed36-9aec-4f46-a30") -> str | None:
    """Intenta obtener un secreto desde Google Cloud Secret Manager (SDK o REST)."""
    # 1. Intento con SDK
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("UTF-8").strip()
    except Exception:
        pass

    # 2. Intento mediante REST API con Application Default Credentials
    try:
        import base64
        import google.auth
        import google.auth.transport.requests
        credentials, gcp_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
        
        target_project = gcp_project or project_id
        url = f"https://secretmanager.googleapis.com/v1/projects/{target_project}/secrets/{secret_name}/versions/latest:access"
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "x-goog-user-project": target_project
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            payload_data = resp.json().get("payload", {}).get("data", "")
            return base64.b64decode(payload_data).decode("UTF-8").strip()
    except Exception:
        pass

    return None

def _get_setting(name: str, required: bool = True) -> str | None:
    # 1. Variables de entorno inyectadas por Cloud Run / sistema
    value = os.environ.get(name)
    if value and str(value).strip():
        return str(value).strip().lstrip("\ufeff").strip()
    
    # 2. Streamlit secrets
    try:
        value = st.secrets[name]
        if value and str(value).strip():
            return str(value).strip().lstrip("\ufeff").strip()
    except Exception:
        pass

    # 3. Google Cloud Secret Manager
    sm_secret_name = SECRET_MANAGER_MAP.get(name)
    if sm_secret_name:
        sm_value = _fetch_from_secret_manager(sm_secret_name)
        if sm_value:
            sm_value = str(sm_value).lstrip("\ufeff").strip()
            os.environ[name] = sm_value  # Guardar en memoria para no repetir la llamada de red
            return sm_value

    if required:
        st.warning(f"Falta configurar: `{name}`")
    return None

# ==========================================
# CONECTOR NATIVO AWS (EC2, RDS, Lambda)
# ==========================================
def _get_boto3_session():
    if not boto3:
        return None
    ak = _get_setting("AWS_ACCESS_KEY_ID", required=False) or os.environ.get("AWS_ACCESS_KEY_ID")
    sk = _get_setting("AWS_SECRET_ACCESS_KEY", required=False) or os.environ.get("AWS_SECRET_ACCESS_KEY")
    reg = _get_setting("AWS_DEFAULT_REGION", required=False) or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    
    if ak and sk:
        return boto3.Session(
            aws_access_key_id=ak.strip(),
            aws_secret_access_key=sk.strip(),
            region_name=reg.strip()
        )
    return boto3.Session(region_name=reg)

def get_aws_resources_summary() -> dict:
    if not boto3:
        return {"status": "error", "message": "boto3 no está instalado en el entorno"}
    try:
        session = _get_boto3_session()
        ec2 = session.client('ec2')
        rds = session.client('rds')
        lam = session.client('lambda')
        reporte = []

        # EC2
        res_ec2 = ec2.describe_instances()
        for res in res_ec2.get('Reservations', []):
            for inst in res.get('Instances', []):
                name_tag = next((t['Value'] for t in inst.get('Tags', []) if t['Key'] == 'Name'), inst['InstanceId'])
                reporte.append({
                    "Servicio": "EC2",
                    "Recurso": f"{name_tag} ({inst['InstanceId']})",
                    "Estado": inst['State']['Name'],
                    "Tipo": inst['InstanceType']
                })

        # RDS
        res_rds = rds.describe_db_instances()
        for db in res_rds.get('DBInstances', []):
            reporte.append({
                "Servicio": "RDS",
                "Recurso": db['DBInstanceIdentifier'],
                "Estado": db['DBInstanceStatus'],
                "Tipo": db['DBInstanceClass']
            })

        # Lambda
        res_lam = lam.list_functions()
        for fn in res_lam.get('Functions', []):
            reporte.append({
                "Servicio": "Lambda",
                "Recurso": fn['FunctionName'],
                "Estado": "Activa",
                "Tipo": f"{fn.get('Runtime', 'Serverless')} ({fn.get('MemorySize', 128)}MB)"
            })

        return {"status": "ok", "total_recursos": len(reporte), "recursos": reporte}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_aws_cost_summary(days: int = DEFAULT_DAYS) -> dict:
    if not boto3:
        return {"status": "error", "message": "boto3 no está instalado en el entorno"}
    try:
        session = _get_boto3_session()
        client = session.client("ce", region_name="us-east-1")
        end_date = datetime.now(UTC).date()
        start_date = end_date - timedelta(days=max(1, int(days)))
        result = client.get_cost_and_usage(
            TimePeriod={"Start": start_date.isoformat(), "End": end_date.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        rows = []
        for period in result.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                amount = float(group.get("Metrics", {}).get("UnblendedCost", {}).get("Amount", 0) or 0)
                if amount:
                    rows.append({"service": group.get("Keys", ["Unknown"])[0], "cost": amount, "currency": "USD"})
        return {"status": "ok", "days": days, "rows": rows, "total": sum(row["cost"] for row in rows)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# =========================
# Config base
# =========================
DEFAULT_PROJECT_ID = "project-5f47ed36-9aec-4f46-a30"
BILLING_TABLE = "project-5f47ed36-9aec-4f46-a30.billing_export.gcp_billing_export_v1_011B52_EC7045_1117AB"
CONVERSATIONS_TABLE = "project-5f47ed36-9aec-4f46-a30.finops_agent.conversations"
MODEL_NAMES = ["gemini-3.5-flash","gemini-3.5-flash-lite","gemini-3.0-flash","gemini-3.0-flash-lite","gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"]
_CREDITS_EXPR = "IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)"

_REQUIRED_TAGS = ["businessOwner", "env"]
_VALID_ENVS = ["dev", "test", "staging", "prod", "development", "production"]

POWERBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"

# GCP Infrastructure
try:
    _, DEFAULT_GCP_PROJECT = google.auth.default()
except:
    DEFAULT_GCP_PROJECT = "project-5f47ed36-9aec-4f46-a30"
DEFAULT_POWERBI_WORKSPACE_ID = os.environ.get("POWERBI_WORKSPACE_ID", "").strip()

st.set_page_config(page_title="FinOps Chat Agent", layout="centered")
st.title("FinOps Chat Agent")
st.caption("Agente para costos GCP/Azure y consultas de tags")

# ==========================================================
# CENTRO DE MANDO: PESTAÑAS FINOPS PRINCIPALES
# ==========================================================
tab_chat, tab_gcp, tab_azure, tab_aws, tab_powerbi, tab_tags, tab_pildoras = st.tabs([
    "💬 Chat FinOps",
    "☁️ GCP (BigQuery)",
    "🔷 Azure (Cost)",
    "🟧 AWS Cloud",
    "📊 Power BI",
    "🏷️ Auditoría Tags",
    "💡 Píldoras FinOps"
])


def queue_chat_prompt(prompt: str) -> None:
    st.session_state.pending_prompt = prompt
    st.rerun()

# Sidebar Diagnóstico
with st.sidebar.expander("🔍 Diagnóstico de Conexiones", expanded=False):
    def _chk(name):
        return "✅ Listo" if _get_setting(name, required=False) else "❌ Falta"
    st.write(f"Gemini API: {_chk('GEMINI_API_KEY')}")
    st.write(f"Azure Secret: {_chk('AZURE_CLIENT_SECRET')}")
    st.write(f"Power BI Secret: {_chk('POWERBI_CLIENT_SECRET')}")
    st.write(f"AWS Key: {_chk('AWS_ACCESS_KEY_ID')}")
    st.write(f"Gmail SMTP: {_chk('GMAIL_APP_PASSWORD')}")


_KB_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.txt")
if os.path.exists(_KB_PATH):
    with open(_KB_PATH, "r", encoding="utf-8") as f:
        FINOPS_KNOWLEDGE = f.read()
else:
    FINOPS_KNOWLEDGE = ""




# Gemini se configura al momento de usar (REST API, no SDK)


def _bq_client() -> bigquery.Client:
    credentials, project = google.auth.default()
    return bigquery.Client(credentials=credentials, project=project)


def log_conversation(pregunta: str, respuesta: str, modelo: str, categoria: str = "general") -> None:
    try:
        client = _bq_client()
        row = {
            "timestamp": datetime.now(UTC).isoformat(),
            "pregunta_usuario": pregunta,
            "respuesta_agente": respuesta,
            "modelo": modelo,
            "categoria_detectada": categoria,
        }
        client.insert_rows_json(CONVERSATIONS_TABLE, [row])
    except Exception:
        # Nunca romper la app por logging
        pass


def _azure_date_range(days: int) -> tuple[str, str]:
    days = max(1, int(days))
    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=days - 1)
    return start_date.isoformat(), end_date.isoformat()


def _assert_valid_tenant(tenant_id: str):
    if not tenant_id or any(x in tenant_id.lower() for x in ["your", "replace", "placeholder", "tenant_id"]):
        raise KeyError(
            "Tenant invalido o no configurado. Usa el Directory (tenant) ID (GUID) o un dominio valido "
            "(ej. contoso.onmicrosoft.com). Revisa AZURE_TENANT_ID / POWERBI_TENANT_ID."
        )


def _get_azure_access_token() -> str:
    tenant_id = _get_setting("AZURE_TENANT_ID")
    client_id = _get_setting("AZURE_CLIENT_ID")
    client_secret = _get_setting("AZURE_CLIENT_SECRET")

    _assert_valid_tenant(tenant_id)
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    response = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://management.azure.com/.default",
        },
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        if response.status_code == 400 and "AADSTS700016" in response.text:
            raise RuntimeError(
                "La aplicación registrada de Azure no existe en el tenant configurado. "
                "Revisa AZURE_CLIENT_ID y AZURE_TENANT_ID."
            )
        raise RuntimeError(f"Azure token request failed ({response.status_code}).")

    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"No se pudo obtener access_token de Azure: {payload}")
    return token


def _run_azure_cost_query(payload: dict) -> dict:
    subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")
    token = _get_azure_access_token()
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        "/providers/Microsoft.CostManagement/query?api-version=2023-03-01"
    )

    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _azure_records(result: dict) -> list[dict]:
    properties = result.get("properties", {})
    columns = [c.get("name") for c in properties.get("columns", [])]
    rows = properties.get("rows", [])
    out = []
    for row in rows:
        record = {}
        for i, col_name in enumerate(columns):
            record[col_name] = row[i] if i < len(row) else None
        out.append(record)
    return out


def _azure_cost_value(record: dict) -> float:
    value = record.get("Cost")
    if value is None:
        value = record.get("PreTaxCost")
    return float(value or 0)


def _normalize_azure_date(value) -> str:
    raw = str(value)
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _query_to_json(query: str, params: list[bigquery.ScalarQueryParameter]) -> str:
    try:
        client = _bq_client()
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        df = client.query(query, job_config=job_config).to_dataframe()
        if df.empty:
            return "[]"
        if "cost_date" in df.columns:
            df["cost_date"] = df["cost_date"].astype(str)
        return df.to_json(orient="records")
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_cost_summary_by_service(project_id: str = DEFAULT_PROJECT_ID, days: int = DEFAULT_DAYS):
    start_date = (datetime.now(UTC) - timedelta(days=days)).date()
    query = f"""
        SELECT
            service.description AS service,
            SUM(cost) AS gross_cost,
            SUM({_CREDITS_EXPR}) AS credits,
            SUM(cost) + SUM({_CREDITS_EXPR}) AS net_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = @project_id
          AND DATE(usage_start_time) >= @start_date
        GROUP BY service
        HAVING gross_cost > 0.01
        ORDER BY gross_cost DESC
    """
    return _query_to_json(
        query,
        [
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        ],
    )


@st.cache_data(ttl=3600)
def get_cost_by_sku_for_service(service_name: str, project_id: str = DEFAULT_PROJECT_ID, days: int = DEFAULT_DAYS):
    start_date = (datetime.now(UTC) - timedelta(days=days)).date()
    query = f"""
        SELECT
            sku.description AS sku,
            SUM(usage.amount) AS usage_amount,
            ANY_VALUE(usage.unit) AS usage_unit,
            SUM(cost) AS gross_cost,
            SUM({_CREDITS_EXPR}) AS credits,
            SUM(cost) + SUM({_CREDITS_EXPR}) AS net_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = @project_id
          AND service.description = @service_name
          AND DATE(usage_start_time) >= @start_date
        GROUP BY sku
        HAVING gross_cost > 0.001
        ORDER BY gross_cost DESC
    """
    return _query_to_json(
        query,
        [
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("service_name", "STRING", service_name),
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        ],
    )


@st.cache_data(ttl=3600)
def get_daily_trend_for_service(service_name: str, project_id: str = DEFAULT_PROJECT_ID, days: int = DEFAULT_DAYS):
    start_date = (datetime.now(UTC) - timedelta(days=days)).date()
    query = f"""
        SELECT DATE(usage_start_time) AS cost_date, SUM(cost) AS daily_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = @project_id
          AND service.description = @service_name
          AND DATE(usage_start_time) >= @start_date
        GROUP BY cost_date
        ORDER BY cost_date ASC
    """
    return _query_to_json(
        query,
        [
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("service_name", "STRING", service_name),
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        ],
    )


@st.cache_data(ttl=3600)
def get_azure_cost_summary_by_service(days: int = DEFAULT_DAYS):
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": start_date, "to": end_date},
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "grouping": [{"type": "Dimension", "name": "ServiceName"}],
                "sorting": [{"direction": "descending", "name": "Cost"}],
            },
        }
        records = _azure_records(_run_azure_cost_query(payload))
        normalized = [
            {
                "service": r.get("ServiceName", "Unknown"),
                "total_cost": _azure_cost_value(r),
                "currency": r.get("Currency", "USD"),
            }
            for r in records
        ]
        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_azure_cost_by_meter_for_service(service_name: str, days: int = DEFAULT_DAYS):
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": start_date, "to": end_date},
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "filter": {
                    "dimensions": {"name": "ServiceName", "operator": "In", "values": [service_name]}
                },
                "grouping": [{"type": "Dimension", "name": "Meter"}],
                "sorting": [{"direction": "descending", "name": "Cost"}],
            },
        }
        records = _azure_records(_run_azure_cost_query(payload))
        normalized = [
            {
                "meter": r.get("Meter", "Unknown"),
                "total_cost": _azure_cost_value(r),
                "currency": r.get("Currency", "USD"),
            }
            for r in records
        ]
        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_azure_daily_trend_for_service(service_name: str, days: int = DEFAULT_DAYS):
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": start_date, "to": end_date},
            "dataset": {
                "granularity": "Daily",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "filter": {
                    "dimensions": {"name": "ServiceName", "operator": "In", "values": [service_name]}
                },
                "sorting": [{"direction": "ascending", "name": "UsageDate"}],
            },
        }
        records = _azure_records(_run_azure_cost_query(payload))
        normalized = [
            {
                "cost_date": _normalize_azure_date(r.get("UsageDate")),
                "daily_cost": _azure_cost_value(r),
                "currency": r.get("Currency", "USD"),
            }
            for r in records
        ]
        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_azure_cost_detail(days: int = DEFAULT_DAYS) -> str:
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": start_date, "to": end_date},
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "grouping": [
                    {"type": "Dimension", "name": "ServiceName"},
                    {"type": "Dimension", "name": "Meter"},
                ],
                "sorting": [{"direction": "descending", "name": "Cost"}],
            },
        }
        records = _azure_records(_run_azure_cost_query(payload))
        normalized = [
            {
                "service": r.get("ServiceName", "Unknown"),
                "meter": r.get("Meter", "Unknown"),
                "total_cost": _azure_cost_value(r),
                "currency": r.get("Currency", "USD"),
            }
            for r in records
        ]
        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_azure_daily_trend(days: int = DEFAULT_DAYS) -> str:
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": start_date, "to": end_date},
            "dataset": {
                "granularity": "Daily",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "sorting": [{"direction": "ascending", "name": "UsageDate"}],
            },
        }
        records = _azure_records(_run_azure_cost_query(payload))
        normalized = [
            {
                "cost_date": _normalize_azure_date(r.get("UsageDate")),
                "daily_cost": _azure_cost_value(r),
                "currency": r.get("Currency", "USD"),
            }
            for r in records
        ]
        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def get_azure_resources_without_tags(resource_group: str | None = None) -> str:
    try:
        subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")
        token = _get_azure_access_token()

        if resource_group and resource_group.strip():
            query = (
                "Resources "
                f"| where resourceGroup =~ '{resource_group.strip()}' "
                "| where isnull(tags) or array_length(bag_keys(tags)) == 0 "
                "| project id, name, type, resourceGroup "
                "| limit 200"
            )
        else:
            query = (
                "Resources "
                "| where isnull(tags) or array_length(bag_keys(tags)) == 0 "
                "| project id, name, type, resourceGroup "
                "| limit 200"
            )

        url = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"query": query, "subscriptions": [subscription_id]},
            timeout=30,
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        return json.dumps({"total_without_tags": len(rows), "resources": rows}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def get_azure_resource_tags(resource_id: str) -> str:
    try:
        subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")
        token = _get_azure_access_token()
        rid = resource_id.strip().rstrip("/")

        query = f"Resources | where id =~ '{rid}' | project id, name, type, tags | limit 1"
        url = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"query": query, "subscriptions": [subscription_id]},
            timeout=30,
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        if not rows:
            return json.dumps({"error": "Recurso no encontrado en la suscripcion", "resource_id": rid}, ensure_ascii=False)

        tags = rows[0].get("tags") or {}
        return json.dumps(
            {"resource_id": rid, "has_tags": len(tags) > 0, "tags": tags, "tags_count": len(tags)},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e), "resource_id": resource_id}, ensure_ascii=False)


def _is_tag_query(prompt: str) -> str:
    p = prompt.lower()
    no_tags = ["sin tag", "sin etiqueta", "no tiene tag", "no tienen tag", "sin tags", "sin etiquetas", "without tag"]
    has_tags = ["tags del recurso", "etiquetas del recurso", "que tags tiene", "ver tags", "mostrar tags"]
    if any(k in p for k in no_tags):
        return "without"
    if any(k in p for k in has_tags):
        return "get"
    return ""


def _format_tags_response(result_json: str, query_type: str) -> str:
    try:
        data = json.loads(result_json)
        if "error" in data:
            return f"Error al consultar Azure: {data.get('error', 'desconocido')}"

        if query_type == "without":
            total = data.get("total_without_tags", 0)
            resources = data.get("resources", [])
            if total == 0:
                return "Todos los recursos en Azure tienen tags asignados."
            out = [f"Se encontraron {total} recursos sin tags en Azure:"]
            for r in resources[:20]:
                if isinstance(r, dict):
                    out.append(f"- {r.get('name','?')} | {r.get('type','?')} | RG: {r.get('resourceGroup','?')}")
            if total > 20:
                out.append(f"... y {total - 20} recursos mas.")
            out.append("Recomendacion: asignar businessOwner y env.")
            return "\n".join(out)

        if query_type == "get":
            tags = data.get("tags", {})
            if not data.get("has_tags", False):
                return "El recurso no tiene tags asignados. Recomendado: businessOwner, env, businessUnit, ac."
            out = [f"Tags encontrados ({data.get('tags_count', 0)}):"]
            for k, v in tags.items():
                out.append(f"- {k}: {v}")
            return "\n".join(out)

        return "No se pudo interpretar la consulta de tags."
    except Exception as e:
        return f"Error formateando respuesta de tags: {e}"


POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"


def _get_powerbi_access_token(scope: str = POWERBI_SCOPE) -> str:
    env_token = os.environ.get("POWERBI_ACCESS_TOKEN", "").strip()
    if env_token and scope == POWERBI_SCOPE:
        return env_token

    # 1. Intentar con variables específicas de Power BI
    pbi_tenant = _get_setting("POWERBI_TENANT_ID", required=False)
    pbi_client = _get_setting("POWERBI_CLIENT_ID", required=False)
    pbi_secret = _get_setting("POWERBI_CLIENT_SECRET", required=False)

    candidates = []
    if pbi_tenant and pbi_client and pbi_secret:
        candidates.append((pbi_tenant, pbi_client, pbi_secret, "PowerBI Credentials"))

    # 2. Intentar con credenciales de Azure (por si comparten Service Principal o Tenant)
    az_tenant = _get_setting("AZURE_TENANT_ID", required=False)
    az_client = _get_setting("AZURE_CLIENT_ID", required=False)
    az_secret = _get_setting("AZURE_CLIENT_SECRET", required=False)
    if az_tenant and az_client and az_secret:
        candidates.append((az_tenant, az_client, az_secret, "Azure Credentials"))

    if not candidates:
        raise RuntimeError(
            "No hay autenticación Power BI o Azure válida. Configura POWERBI_CLIENT_ID/POWERBI_CLIENT_SECRET o AZURE_CLIENT_ID/AZURE_CLIENT_SECRET en Secret Manager."
        )

    last_error = None
    for t_id, c_id, c_sec, label in candidates:
        try:
            _assert_valid_tenant(t_id)
            token_url = f"https://login.microsoftonline.com/{t_id}/oauth2/v2.0/token"
            response = requests.post(
                token_url,
                data={
                    "client_id": c_id,
                    "client_secret": c_sec,
                    "grant_type": "client_credentials",
                    "scope": scope,
                },
                timeout=30,
            )
            if response.status_code == 200:
                payload = response.json()
                token = payload.get("access_token")
                if token:
                    return token
            elif response.status_code == 400 and "AADSTS700016" in response.text:
                last_error = f"La aplicación ({c_id}) no existe en el tenant ({t_id})."
            elif response.status_code == 401 or response.status_code == 400:
                err_desc = response.json().get("error_description", response.text[:200])
                last_error = f"Error autenticando ({label}): {err_desc}"
            else:
                last_error = f"Error Azure AD ({response.status_code}): {response.text[:200]}"
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"Fallo de autenticación Power BI: {last_error}")


def _pbi_request(method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
    token = _get_powerbi_access_token()
    url = f"{POWERBI_API_BASE}{path}"
    response = requests.request(
        method=method,
        url=url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params=params,
        json=body,
        timeout=45,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        if response.status_code == 401:
            raise RuntimeError(
                "Power BI 401 Unauthorized: El Service Principal no tiene permisos en Power BI. "
                "Asegúrate de que 'Permitir que las entidades de servicio usen las API de Power BI' esté habilitado en el Portal de Administración de Power BI y que la app esté agregada como Miembro/Admin en el Workspace."
            )
        elif response.status_code == 403:
            raise RuntimeError(
                f"Power BI 403 Forbidden: La aplicación no tiene permisos suficientes sobre el Workspace ({path}). "
                "Agrega la aplicación registrada como Administrador o Miembro en el Workspace de Power BI."
            )
        raise RuntimeError(f"Power BI API error ({response.status_code}) en {url}: {response.text[:1500]}")
    return response.json() if response.text else {}


def _pbi_list_workspaces() -> list[dict]:
    data = _pbi_request("GET", "/groups")
    return data.get("value", [])


def get_powerbi_reports_inventory(workspace_id: str = "") -> str:
    try:
        ws_id = workspace_id.strip() or DEFAULT_POWERBI_WORKSPACE_ID
        targets = [{"id": ws_id, "name": "selected-workspace"}] if ws_id else _pbi_list_workspaces()

        reports_out = []
        for ws in targets:
            gid = ws.get("id")
            if not gid:
                continue
            reports = _pbi_request("GET", f"/groups/{gid}/reports").get("value", [])
            for r in reports:
                reports_out.append(
                    {
                        "workspace_id": gid,
                        "workspace_name": ws.get("name", ""),
                        "report_id": r.get("id"),
                        "report_name": r.get("name"),
                        "dataset_id": r.get("datasetId"),
                        "web_url": r.get("webUrl"),
                    }
                )

        return json.dumps({"total_reports": len(reports_out), "reports": reports_out}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _resolve_powerbi_report(report_ref: str, workspace_id: str = "") -> tuple[dict | None, str | None]:
    raw = get_powerbi_reports_inventory(workspace_id=workspace_id)
    data = json.loads(raw)
    if "error" in data:
        return None, data["error"]

    reports = data.get("reports", [])
    needle = (report_ref or "").strip().lower()

    exact = next((r for r in reports if str(r.get("report_id", "")).lower() == needle), None)
    if exact:
        return exact, None

    by_name = next((r for r in reports if needle and needle in str(r.get("report_name", "")).lower()), None)
    if by_name:
        return by_name, None

    return None, f"No se encontro reporte con referencia: {report_ref}"


def get_powerbi_report_pages(report_ref: str, workspace_id: str = "") -> str:
    try:
        report, err = _resolve_powerbi_report(report_ref, workspace_id)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

        gid = report["workspace_id"]
        rid = report["report_id"]
        pages = _pbi_request("GET", f"/groups/{gid}/reports/{rid}/pages").get("value", [])
        out = [{"name": p.get("name"), "display_name": p.get("displayName"), "order": p.get("order")} for p in pages]

        return json.dumps(
            {
                "report_name": report.get("report_name"),
                "report_id": rid,
                "workspace_id": gid,
                "pages": out,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def get_powerbi_dataset_info_from_report(report_ref: str, workspace_id: str = "") -> str:
    try:
        report, err = _resolve_powerbi_report(report_ref, workspace_id)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

        gid = report["workspace_id"]
        dataset_id = report.get("dataset_id")
        if not dataset_id:
            return json.dumps({"error": "El reporte no tiene dataset_id asociado"}, ensure_ascii=False)

        ds = _pbi_request("GET", f"/groups/{gid}/datasets/{dataset_id}")
        datasources = _pbi_request("GET", f"/groups/{gid}/datasets/{dataset_id}/datasources").get("value", [])

        return json.dumps(
            {
                "report_name": report.get("report_name"),
                "workspace_id": gid,
                "dataset": {
                    "id": ds.get("id"),
                    "name": ds.get("name"),
                    "configured_by": ds.get("configuredBy"),
                    "is_refreshable": ds.get("isRefreshable"),
                    "add_rows_api_enabled": ds.get("addRowsAPIEnabled"),
                },
                "datasources": datasources,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def execute_powerbi_dax_query(workspace_id: str, dataset_id: str, dax_query: str) -> str:
    try:
        gid = workspace_id.strip()
        dsid = dataset_id.strip()
        query = dax_query.strip()

        if not gid or not dsid or not query:
            return json.dumps(
                {"error": "workspace_id, dataset_id y dax_query son obligatorios"},
                ensure_ascii=False,
            )

        payload = {
            "queries": [{"query": query}],
            "serializerSettings": {"includeNulls": True},
        }
        result = _pbi_request("POST", f"/groups/{gid}/datasets/{dsid}/executeQueries", body=payload)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _is_powerbi_query(prompt: str) -> bool:
    p = prompt.lower()
    keys = ["power bi", "powerbi", "reporte", "reportes", "dataset", "pagina", "página"]
    return any(k in p for k in keys)


def _extract_report_ref(prompt: str) -> str:
    p = prompt.strip()
    m = re.search(r"reporte\s+(.+)$", p, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return p.strip()


def get_powerbi_report_business_summary(report_ref: str, workspace_id: str = "") -> str:
    try:
        raw = get_powerbi_report_pages(report_ref, workspace_id)
        data = json.loads(raw)
        if "error" in data:
            return json.dumps({"error": data["error"]}, ensure_ascii=False)

        pages = data.get("pages", [])
        report_name = data.get("report_name", "N/A")

        buckets = {
            "facturacion": [],
            "consumo": [],
            "marketplace": [],
            "reservas": [],
            "seguridad": [],
            "otros": [],
        }

        for p in pages:
            name = (p.get("display_name") or p.get("name") or "").lower()
            item = {"name": p.get("display_name") or p.get("name"), "order": p.get("order")}
            if "factur" in name:
                buckets["facturacion"].append(item)
            elif "consumo" in name:
                buckets["consumo"].append(item)
            elif "market" in name:
                buckets["marketplace"].append(item)
            elif "reserva" in name or "saving" in name:
                buckets["reservas"].append(item)
            elif "defender" in name or "sentinel" in name or "seguridad" in name:
                buckets["seguridad"].append(item)
            else:
                buckets["otros"].append(item)

        out = {
            "report_name": report_name,
            "total_pages": len(pages),
            "sections": buckets,
            "recommended_questions": [
                "por que subio el costo en los ultimos 30 dias",
                "top servicios por variacion mensual",
                "tendencia de costo 7 y 30 dias",
            ],
        }
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def get_powerbi_dax_guided_templates(report_ref: str, days: int = 30, workspace_id: str = "") -> str:
    try:
        report, err = _resolve_powerbi_report(report_ref, workspace_id)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

        payload = {
            "report_name": report.get("report_name"),
            "workspace_id": report.get("workspace_id"),
            "dataset_id": report.get("dataset_id"),
            "templates": [
                {
                    "name": "por_que_subio_costo",
                    "intent": "Detectar impulsores de incremento de costo",
                    "dax_template": "EVALUATE TOPN(10, SUMMARIZECOLUMNS('Service'[ServiceName], ""Variacion"", [VariacionCosto]), [Variacion], DESC)"
                },
                {
                    "name": "top_servicios_por_variacion",
                    "intent": "Top servicios con mayor variacion",
                    "dax_template": "EVALUATE TOPN(10, SUMMARIZECOLUMNS('Service'[ServiceName], ""Variacion"", [VariacionCosto]), [Variacion], DESC)"
                },
                {
                    "name": "tendencia_7_30_dias",
                    "intent": "Tendencia de costo por fecha",
                    "dax_template": "EVALUATE SUMMARIZECOLUMNS('Date'[Date], ""Costo"", [CostoTotal])"
                },
            ],
        }
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def execute_powerbi_dax_query_for_report(report_ref: str, dax_query: str, workspace_id: str = "") -> str:
    try:
        report, err = _resolve_powerbi_report(report_ref, workspace_id)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

        gid = report.get("workspace_id")
        dsid = report.get("dataset_id")
        if not gid or not dsid:
            return json.dumps({"error": "No se pudo resolver workspace_id/dataset_id del reporte"}, ensure_ascii=False)

        return execute_powerbi_dax_query(gid, dsid, dax_query)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _create_fabric_report(ws_id: str, dataset_id: str, report_name: str, parts: list | None = None) -> str:
    """Crea el reporte (3 páginas con visuales) vía API pública de Fabric y devuelve su URL."""
    headers = {
        "Authorization": f"Bearer {_get_powerbi_access_token(FABRIC_SCOPE)}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        f"{FABRIC_API_BASE}/workspaces/{ws_id}/reports",
        headers=headers,
        json={"displayName": report_name, "definition": {"parts": parts or powerbi_report.build_report_parts(dataset_id)}},
        timeout=60,
    )
    if resp.status_code == 401 or resp.status_code == 403:
        raise RuntimeError(
            f"Fabric API {resp.status_code}: habilita 'Las entidades de servicio pueden llamar a las API públicas de Fabric' "
            f"en el Portal de Administración. Detalle: {resp.text[:300]}"
        )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"Fabric API {resp.status_code}: {resp.text[:500]}")

    operation_url = resp.headers.get("Location")
    if resp.status_code == 202 and operation_url:
        for _ in range(30):
            time.sleep(2)
            op = requests.get(operation_url, headers=headers, timeout=30).json()
            if op.get("status") == "Succeeded":
                break
            if op.get("status") == "Failed":
                raise RuntimeError(f"Fabric no pudo crear el reporte: {(op.get('error') or {}).get('message', op)}")
        else:
            raise RuntimeError("Tiempo de espera agotado creando el reporte en Fabric.")

    for rep in _pbi_request("GET", f"/groups/{ws_id}/reports").get("value", []):
        if rep.get("name") == report_name:
            return rep.get("webUrl") or f"https://app.powerbi.com/groups/{ws_id}/reports/{rep.get('id')}"
    raise RuntimeError("El reporte se creó pero no se encontró en el workspace.")


# ==========================================
# CONSTRUCTOR DE REPORTES POWER BI (multicloud, a medida)
# ==========================================
def _fetch_gcp_cost_rows(days: int) -> list[dict]:
    credentials, project = google.auth.default()
    bq_client = bigquery.Client(credentials=credentials, project=project)
    query = f"""
        SELECT
            CAST(DATE(usage_start_time) AS STRING) AS usage_date,
            IFNULL(service.description, 'Unknown') AS service_name,
            IFNULL(sku.description, 'Unknown') AS sku_name,
            IFNULL(project.id, 'Unknown') AS project_id,
            CAST(SUM(cost) AS FLOAT64) AS gross_cost,
            CAST(SUM(cost) + SUM({_CREDITS_EXPR}) AS FLOAT64) AS net_cost,
            IF((SELECT COUNT(1) FROM UNNEST(labels) l WHERE l.key = 'businessOwner') > 0
               AND (SELECT COUNT(1) FROM UNNEST(labels) l WHERE l.key = 'env') > 0,
               '{powerbi_studio.TAGGED}', '{powerbi_studio.ORPHAN}') AS tagging_status,
            IFNULL((SELECT value FROM UNNEST(labels) WHERE key = 'businessOwner' LIMIT 1), 'Sin Asignar') AS business_owner,
            IFNULL((SELECT value FROM UNNEST(labels) WHERE key = 'env' LIMIT 1), 'Sin Asignar') AS env
        FROM `{BILLING_TABLE}`
        WHERE DATE(usage_start_time) >= @start_date
        GROUP BY usage_date, service_name, sku_name, project_id, tagging_status, business_owner, env
        HAVING gross_cost > 0.001
        ORDER BY usage_date DESC
        LIMIT 50000
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("start_date", "DATE", (datetime.now(UTC) - timedelta(days=days)).date())]
    )
    df = bq_client.query(query, job_config=job_config).to_dataframe()
    return [
        {
            "Date": f"{r['usage_date']}T00:00:00Z", "Cloud": "GCP", "Service": str(r["service_name"]),
            "Detail": str(r["sku_name"]), "Scope": str(r["project_id"]), "Environment": str(r["env"]),
            "BusinessOwner": str(r["business_owner"]), "TaggingStatus": str(r["tagging_status"]),
            "Cost": float(r["gross_cost"] or 0), "NetCost": float(r["net_cost"] or 0),
        }
        for _, r in df.iterrows()
    ]


def _fetch_azure_cost_rows(days: int) -> list[dict]:
    start_date, end_date = _azure_date_range(days)
    payload = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start_date, "to": end_date},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ServiceName"},
                {"type": "Dimension", "name": "ResourceGroupName"},
                {"type": "TagKey", "name": "businessOwner"},
                {"type": "TagKey", "name": "env"},
            ],
        },
    }
    out = []
    for r in powerbi_studio.merge_azure_tag_records(_azure_records(_run_azure_cost_query(payload))):
        out.append({
            "Date": f"{_normalize_azure_date(r['UsageDate'])}T00:00:00Z", "Cloud": "Azure",
            "Service": str(r.get("ServiceName") or "Unknown"), "Detail": str(r.get("ResourceGroupName") or "Sin grupo"),
            "Scope": "Suscripción Azure", "Environment": r["Environment"], "BusinessOwner": r["BusinessOwner"],
            "TaggingStatus": r["TaggingStatus"], "Cost": r["Cost"], "NetCost": r["Cost"],
        })
    return out


def _fetch_aws_cost_rows(days: int) -> list[dict]:
    session = _get_boto3_session()
    if not session:
        raise RuntimeError("boto3 no disponible o sin credenciales AWS")
    client = session.client("ce", region_name="us-east-1")
    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=max(1, int(days)))
    out, token = [], None
    while True:
        kwargs = {
            "TimePeriod": {"Start": start_date.isoformat(), "End": end_date.isoformat()},
            "Granularity": "DAILY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        }
        if token:
            kwargs["NextPageToken"] = token
        res = client.get_cost_and_usage(**kwargs)
        for period in res.get("ResultsByTime", []):
            for g in period.get("Groups", []):
                cost = float(g["Metrics"]["UnblendedCost"]["Amount"] or 0)
                if cost:
                    out.append({
                        "Date": f"{period['TimePeriod']['Start']}T00:00:00Z", "Cloud": "AWS",
                        "Service": g["Keys"][0], "Detail": g["Keys"][1], "Scope": "Cuenta AWS",
                        "Environment": "Sin Asignar", "BusinessOwner": "Sin Asignar",
                        "TaggingStatus": powerbi_studio.NOT_EVALUATED, "Cost": cost, "NetCost": cost,
                    })
        token = res.get("NextPageToken")
        if not token:
            return out


def _fetch_unified_cost_rows(clouds: list[str], days: int) -> tuple[list[dict], list[str]]:
    fetchers = {"gcp": _fetch_gcp_cost_rows, "azure": _fetch_azure_cost_rows, "aws": _fetch_aws_cost_rows}
    rows, notes = [], []
    for cloud in clouds:
        try:
            cloud_rows = fetchers[cloud](days)
            rows.extend(cloud_rows)
            if not cloud_rows:
                notes.append(f"{cloud.upper()}: sin consumo en el periodo.")
        except Exception as e:
            notes.append(f"{cloud.upper()}: no se pudo leer ({str(e)[:160]}).")
    return rows, notes


def _design_report_spec(request_text: str, clouds: list[str] | None, days: int | None) -> tuple[dict, list[str]]:
    """Pide a Gemini una especificación de reporte y la valida; si falla usa una plantilla por defecto."""
    notes = []
    hints = []
    if clouds:
        hints.append(f"Nubes elegidas en la interfaz: {', '.join(clouds)}.")
    if days:
        hints.append(f"Días de historial elegidos en la interfaz: {days}.")
    user_text = request_text + ("\n" + " ".join(hints) if hints else "")
    try:
        gemini_key = _get_setting("GEMINI_API_KEY")
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": gemini_key},
            json={
                "contents": [{"parts": [{"text": user_text}]}],
                "systemInstruction": {"parts": [{"text": powerbi_studio.SPEC_INSTRUCTION}]},
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            },
            timeout=45,
        )
        response.raise_for_status()
        spec = powerbi_studio.validate_spec(json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"]))
    except Exception as e:
        notes.append(f"No pude interpretar el diseño con IA ({str(e)[:120]}); usé la plantilla multicloud por defecto.")
        spec = powerbi_studio.validate_spec(powerbi_studio.default_spec(clouds or powerbi_studio.CLOUDS, days or 30))
        return spec, notes
    if clouds:
        spec["clouds"] = [c for c in clouds if c in powerbi_studio.CLOUDS] or spec["clouds"]
    if days:
        spec["days"] = days
    return spec, notes


def build_custom_powerbi_report(request_text: str, clouds: list[str] | None = None, days: int | None = None, workspace_id: str = "") -> str:
    """Diseña (IA) y construye en Power BI un reporte a medida con datos de GCP/Azure/AWS."""
    try:
        ws_id = workspace_id.strip() or _get_setting("POWERBI_WORKSPACE_ID", required=False) or DEFAULT_POWERBI_WORKSPACE_ID
        if not ws_id:
            return json.dumps({"error": "No hay un Workspace de Power BI configurado (POWERBI_WORKSPACE_ID)."}, ensure_ascii=False)

        spec, notes = _design_report_spec(request_text, clouds, days)
        rows, fetch_notes = _fetch_unified_cost_rows(spec["clouds"], spec["days"])
        notes += fetch_notes
        if not rows:
            return json.dumps({"error": "No se obtuvieron datos de costos de las nubes seleccionadas. " + " ".join(notes)}, ensure_ascii=False)

        report_name = spec["report_name"]
        dataset_name = f"{report_name} - Dataset"
        for rep in _pbi_request("GET", f"/groups/{ws_id}/reports").get("value", []):
            if rep.get("name") == report_name:
                _pbi_request("DELETE", f"/groups/{ws_id}/reports/{rep.get('id')}")
        for ds in _pbi_request("GET", f"/groups/{ws_id}/datasets").get("value", []):
            if ds.get("name") == dataset_name:
                _pbi_request("DELETE", f"/groups/{ws_id}/datasets/{ds.get('id')}")

        new_ds = _pbi_request(
            "POST", f"/groups/{ws_id}/datasets?defaultRetentionPolicy=basicFIFO",
            body=powerbi_studio.build_dataset_schema(dataset_name),
        )
        dataset_id = new_ds.get("id")
        if not dataset_id:
            return json.dumps({"error": "No se pudo crear el dataset en Power BI."}, ensure_ascii=False)

        for i in range(0, len(rows), 5000):
            _pbi_request("POST", f"/groups/{ws_id}/datasets/{dataset_id}/tables/{powerbi_studio.TABLE}/rows", body={"rows": rows[i:i + 5000]})

        report_url, report_error = None, None
        try:
            report_url = _create_fabric_report(ws_id, dataset_id, report_name, powerbi_studio.build_report_parts(dataset_id, spec))
        except Exception as e:
            report_error = str(e)

        by_cloud = {}
        for r in rows:
            by_cloud[r["Cloud"]] = by_cloud.get(r["Cloud"], 0.0) + r["Cost"]
        return json.dumps({
            "status": "success", "kind": "custom", "report_name": report_name, "report_url": report_url,
            "report_error": report_error, "dataset_name": dataset_name, "dataset_id": dataset_id,
            "web_url": f"https://app.powerbi.com/groups/{ws_id}/datasets/{dataset_id}",
            "rows_inserted": len(rows), "days": spec["days"], "clouds": spec["clouds"],
            "cost_by_cloud": {k: round(v, 2) for k, v in by_cloud.items()},
            "pages": [{"title": p["title"], "visuals": len(p["visuals"])} for p in spec["pages"]],
            "notes": notes,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Error al construir el reporte a medida: {str(e)}"}, ensure_ascii=False)


_CREATE_VERBS = ("crea", "crear", "genera", "generar", "arma", "armar", "construye", "construir", "fabrica", "fabricar", "haz", "hazme", "diseña", "disena", "diseñar")
_REPORT_NOUNS = ("reporte", "informe", "dashboard", "tablero", "power bi", "powerbi")


def _is_report_creation_request(prompt: str) -> bool:
    p = prompt.lower()
    return any(re.search(rf"\b{v}\b", p) for v in _CREATE_VERBS) and any(n in p for n in _REPORT_NOUNS)


def _is_standard_report_request(prompt: str) -> bool:
    p = prompt.lower()
    return "estándar" in p or "estandar" in p


def create_and_sync_finops_powerbi_report(workspace_id: str = "", days: int = 30) -> str:
    """Crea automáticamente el Dataset FinOps Multicloud en Power BI e inyecta los datos de consumo y tags desde BigQuery."""
    try:
        ws_id = workspace_id.strip() or _get_setting("POWERBI_WORKSPACE_ID", required=False) or DEFAULT_POWERBI_WORKSPACE_ID
        if not ws_id:
            workspaces = _pbi_list_workspaces()
            if workspaces:
                ws_id = workspaces[0].get("id")
            else:
                return json.dumps({"error": "No se encontró ningún Workspace de Power BI configurado."}, ensure_ascii=False)

        # 1. Consultar datos de facturación y tags en BigQuery
        credentials, project = google.auth.default()
        bq_client = bigquery.Client(credentials=credentials, project=project)
        
        start_date = (datetime.now(UTC) - timedelta(days=days)).date()
        query = f"""
            SELECT
                CAST(DATE(usage_start_time) AS STRING) AS usage_date,
                'GCP' AS cloud_provider,
                IFNULL(service.description, 'Unknown') AS service_name,
                IFNULL(sku.description, 'Unknown') AS sku_name,
                IFNULL(project.id, 'Unknown') AS project_id,
                CAST(SUM(cost) AS FLOAT64) AS gross_cost,
                CAST(SUM({_CREDITS_EXPR}) AS FLOAT64) AS credits,
                CAST(SUM(cost) + SUM({_CREDITS_EXPR}) AS FLOAT64) AS net_cost,
                CASE 
                    WHEN (SELECT COUNT(1) FROM UNNEST(labels) l WHERE l.key = 'businessOwner') > 0 
                     AND (SELECT COUNT(1) FROM UNNEST(labels) l WHERE l.key = 'env') > 0 
                    THEN 'Etiquetado' 
                    ELSE 'Gasto Huérfano' 
                END AS tagging_status,
                IFNULL((SELECT value FROM UNNEST(labels) WHERE key = 'businessOwner' LIMIT 1), 'Sin Asignar') AS business_owner,
                IFNULL((SELECT value FROM UNNEST(labels) WHERE key = 'env' LIMIT 1), 'Sin Asignar') AS env
            FROM `{BILLING_TABLE}`
            WHERE DATE(usage_start_time) >= @start_date
            GROUP BY usage_date, cloud_provider, service_name, sku_name, project_id, tagging_status, business_owner, env
            HAVING gross_cost > 0.001
            ORDER BY usage_date DESC
            LIMIT 5000
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            ]
        )
        df = bq_client.query(query, job_config=job_config).to_dataframe()
        
        if df.empty:
            return json.dumps({"error": "No se encontraron registros de facturación en BigQuery para el periodo seleccionado."}, ensure_ascii=False)

        dataset_name = "FinOps Multicloud Dashboard"
        report_name = "FinOps Multicloud Report"

        # 2. Recrear Dataset y Reporte para aplicar siempre el modelo (columnas + medidas DAX) vigente
        for rep in _pbi_request("GET", f"/groups/{ws_id}/reports").get("value", []):
            if rep.get("name") == report_name:
                _pbi_request("DELETE", f"/groups/{ws_id}/reports/{rep.get('id')}")
        for ds in _pbi_request("GET", f"/groups/{ws_id}/datasets").get("value", []):
            if ds.get("name") == dataset_name:
                _pbi_request("DELETE", f"/groups/{ws_id}/datasets/{ds.get('id')}")

        schema = powerbi_report.build_dataset_schema(dataset_name)
        new_ds = _pbi_request("POST", f"/groups/{ws_id}/datasets?defaultRetentionPolicy=basicFIFO", body=schema)
        dataset_id = new_ds.get("id")

        if not dataset_id:
            return json.dumps({"error": "No se pudo obtener o crear el Dataset en Power BI."}, ensure_ascii=False)

        # 3. Formatear y enviar filas
        rows = []
        total_net_cost = 0.0
        total_gross_cost = 0.0
        orphan_cost = 0.0
        
        for _, r in df.iterrows():
            gross = float(r["gross_cost"]) if r["gross_cost"] is not None else 0.0
            cred = float(r["credits"]) if r["credits"] is not None else 0.0
            net = float(r["net_cost"]) if r["net_cost"] is not None else 0.0
            tag_status = str(r["tagging_status"])
            
            total_net_cost += net
            total_gross_cost += gross
            if tag_status == powerbi_report.ORPHAN:
                orphan_cost += gross
                
            rows.append({
                "Date": f"{r['usage_date']}T00:00:00Z",
                "CloudProvider": str(r["cloud_provider"]),
                "Service": str(r["service_name"]),
                "SKU": str(r["sku_name"]),
                "Project": str(r["project_id"]),
                "ResourceCategory": powerbi_report.classify_resource(str(r["service_name"]), str(r["sku_name"])),
                "GrossCost": gross,
                "Credits": cred,
                "NetCost": net,
                "TaggingStatus": tag_status,
                "BusinessOwner": str(r["business_owner"]),
                "Environment": str(r["env"])
            })

        batch_size = 5000
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i+batch_size]
            _pbi_request("POST", f"/groups/{ws_id}/datasets/{dataset_id}/tables/FinOpsBilling/rows", body={"rows": batch})

        direct_url = f"https://app.powerbi.com/groups/{ws_id}/datasets/{dataset_id}"
        report_url, report_error = None, None
        try:
            report_url = _create_fabric_report(ws_id, dataset_id, report_name)
        except Exception as e:
            report_error = str(e)

        return json.dumps({
            "status": "success",
            "workspace_id": ws_id,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "report_name": report_name,
            "report_url": report_url,
            "report_error": report_error,
            "rows_inserted": len(rows),
            "total_gross_cost": round(total_gross_cost, 2),
            "total_net_cost": round(total_net_cost, 2),
            "orphan_cost": round(orphan_cost, 2),
            "tag_compliance_pct": round(((total_gross_cost - orphan_cost) / total_gross_cost * 100) if total_gross_cost > 0 else 100.0, 1),
            "web_url": direct_url
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Error al generar reporte en Power BI: {str(e)}"}, ensure_ascii=False)


SYSTEM_INSTRUCTION = (
    "Eres un agente FinOps multicloud. Respeta estrictamente la fuente indicada por el usuario: "
    "si menciona GCP, responde solo sobre GCP y sus consumos; si menciona Azure, responde solo sobre Azure; "
    "si menciona AWS, responde solo sobre AWS. No presentes Power BI como fuente ni recomendacion en consultas cloud. "
    "Solo usa Power BI cuando el usuario mencione Power BI, reporte, reportes, pagina o dataset. "
    "Si no hay datos o una integracion disponible, dilo claramente y no inventes cifras. "
    "Responde en espanol claro, accionable y con foco en costos."
)

INTENT_CLASSIFIER_INSTRUCTION = (
    "Eres el planificador de un agente FinOps multicloud. "
    "Analiza la pregunta y devuelve SOLO JSON válido, sin markdown, con esta forma: "
    '{"clouds": ["gcp|azure|aws|powerbi"], "intent": "summary|detail|trend|resources|tags|optimization|report_inventory|report_pages|report_dataset|general", "days": 30, "service": null, "needs_powerbi": false}. '
    "Incluye todas las nubes mencionadas. Usa powerbi solo si el usuario pide Power BI, reportes, paginas o datasets. "
    "Si dice costo, gasto, consumo o precio usa summary; si dice detalle, desglose o SKU usa detail; "
    "si dice tendencia, diario o evolucion usa trend; si dice recursos, inventario u ociosos usa resources. "
    "No inventes nubes ni servicios."
)

TOOLS = [
    get_cost_summary_by_service,
    get_cost_by_sku_for_service,
    get_daily_trend_for_service,
    get_azure_cost_summary_by_service,
    get_azure_cost_by_meter_for_service,
    get_azure_daily_trend_for_service,
    get_azure_resources_without_tags,
    get_azure_resource_tags,
    get_powerbi_reports_inventory,
    get_powerbi_report_pages,
    get_powerbi_dataset_info_from_report,
    execute_powerbi_dax_query,
    create_and_sync_finops_powerbi_report,
]


def _format_powerbi_response(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return raw_json

    if isinstance(data, dict) and "error" in data:
        return f"Error Power BI: {data.get('error')}"

    if isinstance(data, dict) and data.get("status") == "success" and data.get("kind") == "custom":
        out = [f"🎉 **Reporte «{data.get('report_name')}» generado en Power BI**", ""]
        out.append(f"- **Nubes:** {', '.join(c.upper() for c in data.get('clouds', []))} · **Periodo:** {data.get('days')} días")
        out.append(f"- **Filas cargadas:** {data.get('rows_inserted'):,}")
        for cloud, cost in (data.get("cost_by_cloud") or {}).items():
            out.append(f"- **Costo {cloud}:** ${cost:,.2f}")
        out.append("- **Páginas:** " + " · ".join(f"{p['title']} ({p['visuals']} visuales)" for p in data.get("pages", [])))
        out.append("")
        if data.get("report_url"):
            out.append(f"🔗 **[Abrir reporte en Power BI]({data.get('report_url')})**")
        else:
            out.append(f"⚠️ El dataset se creó pero el reporte no: {data.get('report_error')}  \n🔗 [Abrir dataset]({data.get('web_url')})")
        for n in data.get("notes", []):
            out.append(f"\n> ⚠️ {n}")
        return "\n".join(out)

    if isinstance(data, dict) and data.get("status") == "success" and "dataset_id" in data:
        if data.get("report_url"):
            link = f"🔗 **[Abrir el reporte «{data.get('report_name')}» en Power BI]({data.get('report_url')})**\n\n"
            note = (
                "*Páginas: Resumen y Tagging · Operación Limpieza (Zombies) · Rightsizing. "
                "Zombies y rightsizing son estimaciones basadas en facturación (sin métricas de CPU/RAM): validar antes de actuar.*"
            )
        else:
            link = f"🔗 **[Abrir Dataset en Power BI]({data.get('web_url')})**\n\n"
            note = f"⚠️ El dataset se creó, pero no se pudo generar el reporte automáticamente: {data.get('report_error')}"
        return (
            f"🎉 **Dataset y reporte FinOps generados en Power BI**\n\n"
            f"- **Dataset:** `{data.get('dataset_name')}` (ID: `{data.get('dataset_id')}`)\n"
            f"- **Filas de facturación inyectadas:** {data.get('rows_inserted'):,}\n"
            f"- **Costo bruto:** ${data.get('total_gross_cost', 0):,.2f} · **Costo neto:** ${data.get('total_net_cost'):,.2f}\n"
            f"- **Gasto huérfano (sin tags):** ${data.get('orphan_cost'):,.2f}\n"
            f"- **% Cumplimiento tagging:** {data.get('tag_compliance_pct')}%\n\n"
            f"{link}{note}"
        )

    if isinstance(data, dict) and "total_reports" in data and "reports" in data:
        reports = data.get("reports", [])
        out = [f"Se encontraron {data.get('total_reports', 0)} reportes en Power BI."]
        for r in reports[:20]:
            out.append(
                f"- {r.get('report_name','?')} | dataset: {r.get('dataset_id','?')} | report_id: {r.get('report_id','?')}"
            )
        if len(reports) > 20:
            out.append(f"... y {len(reports) - 20} reportes mas.")
        out.append("Tip: pide 'que paginas tiene el reporte <nombre>'.")
        return "\n".join(out)

    if isinstance(data, dict) and "pages" in data and "report_name" in data:
        pages = data.get("pages", [])
        out = [f"Reporte: {data.get('report_name','?')}", f"Paginas encontradas: {len(pages)}"]
        for p in pages:
            out.append(f"- [{p.get('order','?')}] {p.get('display_name','?')} ({p.get('name','?')})")
        return "\n".join(out)

    if isinstance(data, dict) and "dataset" in data and "report_name" in data:
        ds = data.get("dataset", {}) or {}
        out = [
            f"Reporte: {data.get('report_name','?')}",
            f"Dataset: {ds.get('name','?')}",
            f"Dataset ID: {ds.get('id','?')}",
            f"Refreshable: {ds.get('is_refreshable','?')}",
        ]
        return "\n".join(out)

    if isinstance(data, dict) and "sections" in data and "recommended_questions" in data:
        out = [
            f"Reporte: {data.get('report_name','?')}",
            f"Paginas totales: {data.get('total_pages', 0)}",
            "Resumen por secciones:",
        ]
        sections = data.get("sections", {})
        for k in ["facturacion", "consumo", "marketplace", "reservas", "seguridad", "otros"]:
            out.append(f"- {k}: {len(sections.get(k, []))} pagina(s)")
        out.append("")
        out.append("Preguntas sugeridas:")
        for q in data.get("recommended_questions", []):
            out.append(f"- {q}")
        return "\n".join(out)

    if isinstance(data, dict) and "templates" in data and "dataset_id" in data:
        out = [
            f"Reporte: {data.get('report_name','?')}",
            f"Dataset ID: {data.get('dataset_id','?')}",
            "Playbook DAX sugerido:",
        ]
        for t in data.get("templates", []):
            out.append(f"- {t.get('name')}: {t.get('intent')}")
        return "\n".join(out)

    return raw_json


def _is_explicit_cloud_query(prompt: str, cloud: str) -> bool:
    p = prompt.lower()
    aliases = {
        "gcp": ["gcp", "google cloud", "google cloud platform"],
        "azure": ["azure"],
        "aws": ["aws", "amazon web services"],
    }
    return any(alias in p for alias in aliases[cloud])


def _format_aws_chat_response(result: dict) -> str:
    if result.get("status") != "ok":
        return f"No se pudo consultar AWS: {result.get('message', 'error desconocido')}"
    resources = result.get("recursos", [])
    lines = [f"Recursos AWS detectados: {result.get('total_recursos', len(resources))}"]
    for resource in resources[:20]:
        lines.append(
            f"- {resource.get('Servicio')}: {resource.get('Recurso')} | "
            f"estado: {resource.get('Estado')} | tipo: {resource.get('Tipo')}"
        )
    return "\n".join(lines)


def _format_aws_cost_response(result: dict) -> str:
    if result.get("status") != "ok":
        return f"No se pudo consultar costos AWS: {result.get('message', 'error desconocido')}"
    lines = [
        f"Costos AWS de los últimos {result.get('days', DEFAULT_DAYS)} días",
        f"Total: ${result.get('total', 0):,.2f} USD",
        "",
        "Por servicio:",
    ]
    rows = sorted(result.get("rows", []), key=lambda row: row["cost"], reverse=True)
    if not rows:
        lines.append("- No hay costos registrados en el periodo consultado.")
    else:
        for row in rows[:30]:
            lines.append(f"- {row['service']}: ${row['cost']:,.2f} {row['currency']}")
    return "\n".join(lines)


def _format_azure_cost_response(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return raw_json
    if isinstance(data, dict) and "error" in data:
        return f"Error consultando costos Azure: {data.get('error')}"
    if not isinstance(data, list) or not data:
        return "No hay datos de costos Azure para el periodo consultado."
    total = sum(float(item.get("total_cost") or 0) for item in data)
    lines = ["Resumen de costos Azure (Cost Management)", f"Total: ${total:,.2f}", "", "Por servicio:"]
    for item in data[:20]:
        lines.append(f"- {item.get('service', 'Unknown')}: ${float(item.get('total_cost') or 0):,.2f} {item.get('currency', '')}")
    return "\n".join(lines)


def _format_azure_detail_response(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return raw_json
    if isinstance(data, dict) and "error" in data:
        return f"Error consultando detalle Azure: {data.get('error')}"
    if not isinstance(data, list) or not data:
        return "No hay detalle de costos Azure para el periodo consultado."
    lines = ["Detalle de costos Azure por servicio y medidor:"]
    for item in data[:50]:
        lines.append(
            f"- {item.get('service', 'Unknown')} / {item.get('meter', 'Unknown')}: "
            f"${float(item.get('total_cost') or 0):,.2f} {item.get('currency', '')}"
        )
    return "\n".join(lines)


def _format_azure_trend_response(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return raw_json
    if isinstance(data, dict) and "error" in data:
        return f"Error consultando tendencia Azure: {data.get('error')}"
    if not isinstance(data, list) or not data:
        return "No hay datos diarios de Azure para el periodo consultado."
    lines = ["Tendencia diaria de costos Azure:"]
    for item in data:
        lines.append(
            f"- {item.get('cost_date', '?')}: ${float(item.get('daily_cost') or 0):,.2f} "
            f"{item.get('currency', '')}"
        )
    return "\n".join(lines)


def _route_explicit_cloud_query(prompt: str) -> tuple[str, str] | None:
    p = prompt.lower()

    if _is_explicit_cloud_query(prompt, "gcp"):
        if any(word in p for word in ["ocioso", "ociosos", "zombie", "sin uso", "idle"]):
            return get_gcp_idle_resources_real(), "gcp-bigquery"
        if any(word in p for word in ["tendencia", "diario", "diaria", "evolución", "evolucion"]):
            return get_gcp_daily_trend_real(), "gcp-bigquery"
        if any(word in p for word in ["detalle", "desglose", "sku"]):
            return get_gcp_cost_detail_real(), "gcp-bigquery"
        if any(word in p for word in ["servicio", "servicios", "top", "desglose", "detalle"]):
            return get_gcp_top_services_real(), "gcp-bigquery"
        return get_gcp_cost_summary_real(), "gcp-bigquery"

    if _is_explicit_cloud_query(prompt, "azure"):
        if any(word in p for word in ["tendencia", "diario", "diaria", "evolución", "evolucion"]):
            return _format_azure_trend_response(get_azure_daily_trend()), "azure-cost-management"
        if any(word in p for word in ["detalle", "desglose", "medidor", "meter", "sku"]):
            return _format_azure_detail_response(get_azure_cost_detail()), "azure-cost-management"
        return _format_azure_cost_response(get_azure_cost_summary_by_service()), "azure-cost-management"

    if _is_explicit_cloud_query(prompt, "aws"):
        if any(word in p for word in ["costo", "costos", "gasto", "tendencia", "consumo", "precio", "detalle"]):
            return _format_aws_cost_response(get_aws_cost_summary()), "aws-cost-explorer"
        return _format_aws_chat_response(get_aws_resources_summary()), "aws"

    return None


def _classify_finops_request(prompt: str) -> dict | None:
    """Usa Gemini para interpretar la intención antes de elegir herramientas."""
    try:
        gemini_key = _get_setting("GEMINI_API_KEY")
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": INTENT_CLASSIFIER_INSTRUCTION}]},
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": gemini_key},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        plan = json.loads(text)
        clouds = [cloud for cloud in plan.get("clouds", []) if cloud in {"gcp", "azure", "aws", "powerbi"}]
        intent = plan.get("intent", "general")
        if intent not in {"summary", "detail", "trend", "resources", "tags", "optimization", "report_inventory", "report_pages", "report_dataset", "general"}:
            intent = "general"
        return {
            "clouds": clouds,
            "intent": intent,
            "days": max(1, min(int(plan.get("days", 30) or 30), 365)),
            "service": plan.get("service"),
            "needs_powerbi": bool(plan.get("needs_powerbi", False)),
        }
    except Exception:
        return None


def _execute_finops_plan(prompt: str, plan: dict) -> tuple[str, str] | None:
    """Ejecuta el plan interpretado y combina resultados de varias fuentes."""
    intent = plan["intent"]
    days = plan["days"]
    clouds = plan["clouds"]
    results = []

    for cloud in clouds:
        if cloud == "gcp":
            if intent == "trend":
                results.append(("GCP", get_gcp_daily_trend_real(days=days)))
            elif intent == "detail":
                results.append(("GCP", get_gcp_cost_detail_real(days=days)))
            elif intent == "resources":
                results.append(("GCP", get_gcp_idle_resources_real()))
            else:
                results.append(("GCP", get_gcp_cost_summary_real()))
        elif cloud == "azure":
            if intent == "trend":
                results.append(("Azure", _format_azure_trend_response(get_azure_daily_trend(days))))
            elif intent == "detail":
                results.append(("Azure", _format_azure_detail_response(get_azure_cost_detail(days))))
            else:
                results.append(("Azure", _format_azure_cost_response(get_azure_cost_summary_by_service(days))))
        elif cloud == "aws":
            if intent in {"summary", "detail", "trend"}:
                results.append(("AWS", _format_aws_cost_response(get_aws_cost_summary(days))))
            else:
                results.append(("AWS", _format_aws_chat_response(get_aws_resources_summary())))

    if not results:
        return None
    if len(results) == 1:
        return results[0][1], results[0][0].lower()
    combined = "\n\n".join(f"## {source}\n{result}" for source, result in results)
    return combined, "multicloud-planner"


def _handle_conversational_prompt(prompt: str) -> tuple[str, str] | None:
    """Responde directamente a mensajes conversacionales básicos."""
    normalized = re.sub(r"[^a-záéíóúüñ ]", "", prompt.lower()).strip()
    greetings = {"hola", "holá", "buenas", "buenos dias", "buenas tardes", "buenas noches"}
    if normalized in greetings:
        return (
            "Hola. Soy tu agente FinOps. Puedo consultar costos y consumo de GCP, Azure y AWS, "
            "revisar tags, analizar tendencias y consultar reportes de Power BI. ¿Qué necesitas revisar?",
            "conversation",
        )
    return None


# ==========================================
# GCP INFRASTRUCTURE QUERIES
# ==========================================

def get_gcp_top_services_real(project_id: str = None, days: int = 30) -> str:
    """Obtiene los servicios más costosos desde BigQuery Billing"""
    try:
        if not project_id:
            project_id = DEFAULT_GCP_PROJECT
        
        client = _bq_client()
        start_date = (datetime.now(UTC) - timedelta(days=days)).date()
        
        query = f"""
        SELECT
            service.description AS service,
            ROUND(SUM(cost), 2) as gross_cost,
            ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) as credits,
            ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) as net_cost,
        FROM `{BILLING_TABLE}`
        WHERE project.id = '{project_id}'
          AND DATE(usage_start_time) >= '{start_date}'
        GROUP BY service
        ORDER BY net_cost DESC
        LIMIT 10
        """
        df = client.query(query).to_dataframe()
        
        if df.empty:
            return "❌ No hay datos de costos disponibles."
        
        total_cost = df['net_cost'].sum()
        out = [f"🏆 **Top 10 Servicios de GCP (Últimos {days} días)**"]
        out.append(f"💰 Total: ${total_cost:,.2f}\n")
        
        for i, (_, row) in enumerate(df.iterrows(), 1):
            pct = (row['net_cost'] / total_cost * 100) if total_cost > 0 else 0
            out.append(f"{i}. **{row['service']}**: ${row['net_cost']:,.2f} ({pct:.1f}%)")
        
        return "\n".join(out)
    except Exception as e:
        return f"❌ Error consultando servicios GCP: {str(e)}"


def get_gcp_cost_detail_real(project_id: str = None, days: int = 30) -> str:
    """Desglose de costos GCP por servicio y SKU."""
    try:
        if not project_id:
            project_id = DEFAULT_GCP_PROJECT
        client = _bq_client()
        start_date = (datetime.now(UTC) - timedelta(days=days)).date()
        query = f"""
        SELECT
            service.description AS service,
            sku.description AS sku,
            ROUND(SUM(cost), 2) AS gross_cost,
            ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS credits,
            ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = '{project_id}'
          AND DATE(usage_start_time) >= '{start_date}'
        GROUP BY service, sku
        HAVING gross_cost > 0.001
        ORDER BY net_cost DESC
        LIMIT 50
        """
        df = client.query(query).to_dataframe()
        if df.empty:
            return f"No hay detalle de costos GCP para los últimos {days} días."
        lines = [f"Detalle de costos GCP por servicio y SKU (Últimos {days} días):"]
        for _, row in df.iterrows():
            lines.append(
                f"- {row['service']} / {row['sku']}: bruto ${row['gross_cost']:,.2f} | "
                f"créditos ${abs(row['credits']):,.2f} | neto ${row['net_cost']:,.2f}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Error consultando detalle GCP: {str(e)}"

def get_gcp_idle_resources_real(project_id: str = None) -> str:
    """Identifica VMs, discos y IPs ociosas en GCP"""
    return (
        "La tabla de Billing Export configurada no contiene inventario de recursos ni métricas de uso. "
        "Para detectar recursos ociosos GCP necesitamos consultar Compute Engine, Cloud Storage y direcciones "
        "mediante sus APIs o Cloud Asset Inventory."
    )


def get_gcp_daily_trend_real(project_id: str = None, days: int = 30) -> str:
    """Muestra la evolución diaria del costo neto GCP."""
    try:
        if not project_id:
            project_id = DEFAULT_GCP_PROJECT

        client = _bq_client()
        query = f"""
        SELECT
            DATE(usage_start_time) AS cost_date,
            ROUND(SUM(cost), 2) AS gross_cost,
            ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS credits,
            ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost
        FROM `{BILLING_TABLE}`
        WHERE project.id = '{project_id}'
          AND DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)
        GROUP BY cost_date
        ORDER BY cost_date ASC
        """
        df = client.query(query).to_dataframe()
        if df.empty:
            return f"No hay datos diarios de costos GCP para los últimos {days} días."

        out = [f"📈 **Tendencia diaria de costos GCP (Últimos {days} días)**"]
        for _, row in df.iterrows():
            out.append(
                f"- {row['cost_date']}: bruto ${row['gross_cost']:,.2f} | "
                f"créditos ${abs(row['credits']):,.2f} | neto ${row['net_cost']:,.2f}"
            )
        return "\n".join(out)
    except Exception as e:
        return f"❌ Error consultando tendencia GCP: {str(e)}"

def get_gcp_cost_summary_real(project_id: str = None) -> str:
    """Resumen de costos GCP del mes actual"""
    try:
        if not project_id:
            project_id = DEFAULT_GCP_PROJECT
        
        client = _bq_client()
        
        query = f"""
        SELECT
            ROUND(SUM(cost), 2) as gross_cost,
            ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) as credits,
            ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) as net_cost,
            COUNT(DISTINCT DATE(usage_start_time)) as days_with_costs
        FROM `{BILLING_TABLE}`
        WHERE project.id = '{project_id}'
          AND DATE(usage_start_time) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
        """
        
        df = client.query(query).to_dataframe()
        
        if df.empty or len(df) == 0:
            return "❌ No hay datos de costos para este mes."
        
        row = df.iloc[0]
        out = ["📊 **Resumen de Costos GCP - Mes Actual**\n"]
        out.append(f"💰 Costo Bruto: ${row['gross_cost']:,.2f}")
        out.append(f"🎁 Créditos: ${abs(row['credits']):,.2f}")
        out.append(f"💳 Costo Neto: ${row['net_cost']:,.2f}")
        out.append(f"📅 Días con actividad: {int(row['days_with_costs'])}")
        
        return "\n".join(out)
    except Exception as e:
        return f"❌ Error obteniendo resumen: {str(e)}"

def ask_with_fallback(prompt: str):
    conversational_response = _handle_conversational_prompt(prompt)
    if conversational_response:
        return conversational_response

    tag_mode = _is_tag_query(prompt)
    if tag_mode == "without":
        result = get_azure_resources_without_tags()
        return _format_tags_response(result, "without"), "azure-resource-graph"

    if tag_mode == "get":
        import re as _re
        m = _re.search(r"/subscriptions/\S+", prompt)
        resource_id = m.group(0) if m else prompt
        result = get_azure_resource_tags(resource_id)
        return _format_tags_response(result, "get"), "azure-resource-graph"

    if _is_report_creation_request(prompt):
        if _is_standard_report_request(prompt):
            return _format_powerbi_response(create_and_sync_finops_powerbi_report()), "powerbi-creator"
        return _format_powerbi_response(build_custom_powerbi_report(prompt)), "powerbi-studio"

    plan = _classify_finops_request(prompt)
    if plan and plan.get("clouds") and not plan.get("needs_powerbi"):
        planned_response = _execute_finops_plan(prompt, plan)
        if planned_response:
            return planned_response

    if not _is_powerbi_query(prompt):
        cloud_response = _route_explicit_cloud_query(prompt)
        if cloud_response:
            return cloud_response

    # Rutas deterministicas Power BI (evita caer al LLM para intents conocidos)
    if _is_powerbi_query(prompt):
        p = prompt.lower()

        if ("crea" in p or "generar" in p or "crear" in p or "construir" in p or "publica" in p or "sincroniz" in p) and ("reporte" in p or "power bi" in p or "powerbi" in p or "dataset" in p or "dashboard" in p or "esto" in p):
            return _format_powerbi_response(create_and_sync_finops_powerbi_report()), "powerbi-creator"

        if "lista" in p and ("reporte" in p or "reportes" in p):
            return _format_powerbi_response(get_powerbi_reports_inventory()), "powerbi-api"

        if ("pagina" in p or "pág" in p) and "reporte" in p:
            report_ref = _extract_report_ref(prompt)
            return _format_powerbi_response(get_powerbi_report_pages(report_ref)), "powerbi-api"

        if ("dataset" in p or "contenido" in p) and "reporte" in p:
            report_ref = _extract_report_ref(prompt)
            return _format_powerbi_response(get_powerbi_dataset_info_from_report(report_ref)), "powerbi-api"

        if ("resumen ejecutivo" in p or "interpret" in p) and "reporte" in p:
            report_ref = _extract_report_ref(prompt)
            return _format_powerbi_response(get_powerbi_report_business_summary(report_ref)), "powerbi-insight"

        if "por que subio" in p or "top servicios por variacion" in p or "tendencia 7" in p or "tendencia 30" in p:
            report_ref = _extract_report_ref(prompt)
            return _format_powerbi_response(get_powerbi_dax_guided_templates(report_ref)), "powerbi-playbook"

    enriched = prompt
    if FINOPS_KNOWLEDGE:
        enriched = (
            "Usa este contexto FinOps como referencia:\n"
            f"{FINOPS_KNOWLEDGE}\n\n"
            f"Consulta del usuario: {prompt}"
        )

    gemini_key = _get_setting("GEMINI_API_KEY")
    last_error = None
    
    for model_name in MODEL_NAMES:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": gemini_key,
            }
            payload = {
                "contents": [{"parts": [{"text": enriched}]}],
                "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            }
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            
            if response.status_code == 401:
                last_error = Exception("401 Unauthorized - Check API key")
                st.info(f"Auth error en {model_name}. Probando siguiente...")
                continue
            elif response.status_code == 429:
                last_error = ResourceExhausted("Cuota agotada")
                st.info(f"Cuota agotada en {model_name}. Probando siguiente modelo...")
                continue
            
            response.raise_for_status()
            result = response.json()
            
            if "candidates" in result and result["candidates"]:
                text = result["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "")
                if text:
                    return text, model_name
            
        except Exception as e:
            last_error = e
            st.warning(f"Error con {model_name}: {str(e)[:100]}. Probando siguiente...")
            continue

    if last_error is not None:
        raise last_error

    raise RuntimeError("No se pudo generar respuesta con ningún modelo disponible.")


WELCOME_MESSAGE = (
    "Hola, soy tu FinOps Chat Agent.\n\n"
    "Puedo ayudarte con:\n"
    "- Recomendaciones FinOps (variaciones, optimizacion, pildoras y acciones)."
)

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]
else:
    if (
        st.session_state.messages
        and st.session_state.messages[0].get("role") == "assistant"
        and (
            "Como puedo ayudarte a analizar y optimizar costos de GCP o Azure" in st.session_state.messages[0].get("content", "")
            or "Costos en GCP y Azure (resumen, detalle y tendencias)" in st.session_state.messages[0].get("content", "")
        )
    ):
        st.session_state.messages[0]["content"] = WELCOME_MESSAGE


def process_chat_prompt(prompt: str, source: str = "chat", files: list | None = None) -> None:
    files = files or []
    shown_prompt = prompt + (("\n\n📎 " + ", ".join(f.name for f in files)) if files else "")
    st.session_state.messages.append({"role": "user", "content": shown_prompt})
    with st.chat_message("user"):
        st.markdown(shown_prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analizando archivos..." if files else ("Analizando..." if source == "chat" else "Procesando voz...")):
            try:
                if files:
                    final_response, used_model = analyze_files(
                        prompt, files, _get_setting("GEMINI_API_KEY"), MODEL_NAMES, SYSTEM_INSTRUCTION
                    )
                else:
                    final_response, used_model = ask_with_fallback(prompt)
                st.markdown(final_response)
                st.caption(f"Respondido con: {used_model}")
                st.session_state.messages.append({"role": "assistant", "content": final_response})
                render_email_button(len(st.session_state.messages) - 1, final_response)
                log_conversation(shown_prompt, final_response, used_model)
            except ResourceExhausted:
                msg = "Se agoto la cuota diaria de modelos gratuitos. Intenta mas tarde o usa paid tier."
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
            except Exception as e:
                msg = f"Ocurrio un error al procesar la consulta: {e}"
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})


def transcribe_voice_audio(audio: dict) -> tuple[str | None, str | None]:
    if not audio or not Recognizer:
        return None, "El componente de voz no está disponible en esta versión."
    try:
        recognizer = Recognizer()
        audio_data = AudioData(audio["bytes"], audio["sample_rate"], audio["sample_width"])
        return recognizer.recognize_google(audio_data, language="es-ES"), None
    except UnknownValueError:
        return None, "No pude entender el audio. Habla más cerca del micrófono y vuelve a intentarlo."
    except RequestError as exc:
        return None, f"El servicio de transcripción no está disponible: {exc}"
    except Exception as exc:
        return None, f"No se pudo transcribir el audio: {exc}"


with tab_gcp:
    st.subheader("☁️ Consultas directas GCP")
    st.markdown("""Hola, soy tu FinOps Chat Agent.

Puedo ayudarte con:

- Costos en GCP (resumen, detalle y tendencias).
- Reportes de GCP (inventario, paginas, dataset y resumen ejecutivo).
- Gobernanza de tags en GCP (recursos sin tags y validacion).
- Recomendaciones FinOps para GCP (variaciones, optimizacion y acciones).""")

with tab_azure:
    st.subheader("☁️ Consultas directas AZURE")
    st.markdown("""Hola, soy tu FinOps Chat Agent.

Puedo ayudarte con:

- Costos en Azure (resumen, detalle y tendencias).
- Reportes de Azure (inventario, paginas, dataset y resumen ejecutivo).
- Gobernanza de tags en Azure (recursos sin tags y validacion).
- Recomendaciones FinOps para Azure (variaciones, optimizacion y acciones).""")

with tab_powerbi:
    st.subheader("📊 Generador y Consultas de Power BI")
    st.markdown(
        "El Agente crea automáticamente el **Dataset FinOps Multicloud** (con medidas DAX) y el **Reporte** "
        "de 3 páginas (Tagging, Zombies y Rightsizing) en tu Workspace de Power BI, con los datos de BigQuery."
    )
    col_pbi1, col_pbi2 = st.columns([2, 1])
    with col_pbi1:
        pbi_days = st.slider("Días de historial a sincronizar:", min_value=7, max_value=90, value=30, step=7, key="pbi_sync_days")
    with col_pbi2:
        st.write("")
        st.write("")
        if st.button("🚀 Crear / Sincronizar en Power BI", key="btn_create_pbi_dataset"):
            with st.spinner("Creando Dataset e inyectando datos en Power BI..."):
                res_json = create_and_sync_finops_powerbi_report(days=pbi_days)
                formatted_pbi = _format_powerbi_response(res_json)
                st.markdown(formatted_pbi)

    st.markdown("---")
    st.subheader("🧩 Constructor de reportes a medida")
    st.markdown(
        "Describe el reporte que necesitas y el Agente lo diseña y lo construye en Power BI "
        "(datos reales de GCP, Azure y AWS, medidas DAX y gráficos)."
    )
    examples = {
        "Comparativo multicloud": "Compara el costo de GCP y Azure por servicio, con la tendencia diaria y los 10 servicios más caros",
        "Gobierno de tags (GCP)": "Reporte de cumplimiento de tags en GCP: costo sin etiquetar por proyecto, entorno y dueño, con detalle para remediar",
        "Optimización por servicio": "Reporte para optimizar costos: costo por servicio y detalle (SKU o grupo de recursos), con costo diario promedio por nube",
    }
    ex_cols = st.columns(len(examples))
    for col, (label, text) in zip(ex_cols, examples.items()):
        if col.button(label, key=f"pbi_ex_{label}"):
            st.session_state["pbi_studio_request"] = text
    studio_request = st.text_area(
        "¿Qué reporte necesitas?",
        key="pbi_studio_request",
        height=100,
        placeholder="Ej: Un reporte de costos por servicio y nube de los últimos 60 días, con tendencia diaria y detalle por proyecto",
    )
    sc1, sc2 = st.columns([2, 1])
    with sc1:
        studio_clouds = st.multiselect(
            "Nubes a incluir (vacío = que la IA decida según tu texto)",
            ["gcp", "azure", "aws"],
            key="pbi_studio_clouds",
            format_func=lambda c: {"gcp": "GCP (BigQuery)", "azure": "Azure", "aws": "AWS"}[c],
        )
    with sc2:
        studio_days = st.number_input("Días (0 = que la IA decida)", min_value=0, max_value=365, value=0, step=7, key="pbi_studio_days")
    if st.button("🧩 Construir mi reporte", key="btn_build_custom_pbi", type="primary", disabled=not (studio_request or "").strip()):
        with st.spinner("Diseñando el reporte, cargando datos y creando visuales en Power BI (puede tardar ~1 min)..."):
            res_json = build_custom_powerbi_report(studio_request, clouds=studio_clouds or None, days=int(studio_days) or None)
            st.markdown(_format_powerbi_response(res_json))

    st.markdown("---")
    st.markdown("""**Consultas interactivas en el chat:**

1. *'crea un reporte de ...'* -> Describe el reporte y el Agente lo construye (ej: *'crea un reporte comparando GCP y Azure por servicio'*). Con *'estándar'* regenera el reporte FinOps GCP fijo.
2. *'lista reportes de power bi'* -> Inventario de reportes y datasets.
3. *'que paginas tiene el reporte <nombre>'* -> Estructura y secciones del reporte.
4. *'resumen ejecutivo del reporte <nombre>'* -> Análisis de áreas y recomendaciones.""")

with tab_tags:
    st.subheader("🏷️ Auditoría de Tags y Etiquetas")

with tab_pildoras:
    st.subheader("💡 Píldoras y Mejores Prácticas")

with tab_aws:
    st.subheader("☁️ Consultas directas AWS CLOUD")
    st.markdown("""Hola, soy tu FinOps Chat Agent.

Puedo ayudarte con:

- Costos en AWS (resumen, detalle y tendencias).
- Reportes de AWS (inventario, paginas, dataset y resumen ejecutivo).
- Gobernanza de tags en AWS (recursos sin tags y validacion).
- Recomendaciones FinOps para AWS (variaciones, optimizacion y acciones).""")


with tab_chat:
    with st.chat_message(st.session_state.messages[0]["role"]):
        st.markdown(st.session_state.messages[0]["content"])
        render_email_button(0, st.session_state.messages[0]["content"])

# El mensaje de bienvenida solo se muestra en la pestaña Chat FinOps;
# el resto del historial (preguntas y respuestas) es global a todas las pestañas.
for idx, message in enumerate(st.session_state.messages[1:], start=1):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_email_button(idx, message["content"])

chat_col, voice_col = st.columns([8, 1], vertical_alignment="bottom")
with chat_col:
    chat_value = st.chat_input(
        "Escribe tu mensaje o adjunta archivos",
        accept_file="multiple",
        file_type=ALLOWED_FILE_TYPES,
    )
with voice_col:
    voice_audio = mic_recorder(
        start_prompt="🎙️",
        stop_prompt="⏹️",
        just_once=True,
        use_container_width=True,
        format="wav",
        key="finops_voice_input_row",
    ) if mic_recorder else None

voice_prompt, voice_error = transcribe_voice_audio(voice_audio)
if voice_error:
    st.warning(voice_error)

pending_prompt = st.session_state.pop("pending_prompt", None)
chat_prompt = chat_value.text if chat_value else None
chat_files = list(chat_value.files) if chat_value else []
prompt = voice_prompt or chat_prompt or pending_prompt
if prompt or chat_files:
    process_chat_prompt(prompt or "", source="voice" if voice_prompt else "chat", files=chat_files)

