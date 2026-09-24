
def send_email_smtp(to_address: str, subject: str, body: str) -> tuple[bool, str]:
    try:
        to_address = (to_address or "").strip()
        if not to_address:
            return False, "Debes indicar un correo destino."

        gmail_address = _get_setting("GMAIL_ADDRESS", required=False)
        gmail_password = _get_setting("GMAIL_APP_PASSWORD", required=False)
        if not gmail_address or not gmail_password:
            return False, "Falta configurar GMAIL_ADDRESS / GMAIL_APP_PASSWORD en credenciales.env."

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

def _load_credenciales_env():
    possible_paths = [
        Path(__file__).resolve().parent.parent / "credenciales.env",
        Path(__file__).resolve().parent / "credenciales.env",
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent / ".env",
    ]
    for env_path in possible_paths:
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'").strip('"')
                        if k and not os.environ.get(k):
                            os.environ[k] = v
            break

_load_credenciales_env()

def _get_setting(name: str, required: bool = True) -> str | None:
    value = os.environ.get(name)
    if value and str(value).strip():
        return str(value).strip().lstrip("\ufeff").strip()
    try:
        value = st.secrets[name]
        if value and str(value).strip():
            return str(value).strip().lstrip("\ufeff").strip()
    except Exception:
        pass
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
tab_chat, tab_gcp, tab_azure, tab_powerbi, tab_tags, tab_pildoras, tab_aws = st.tabs([
    "💬 Chat FinOps",
    "☁️ GCP (BigQuery)",
    "🔷 Azure (Cost)",
    "📊 Power BI",
    "🏷️ Auditoría Tags",
    "💡 Píldoras FinOps",
    "🟧 AWS Cloud"
])


def queue_chat_prompt(prompt: str) -> None:
    st.session_state.pending_prompt = prompt
    st.rerun()

with tab_gcp:
    st.subheader("☁️ Consultas directas GCP")
    st.markdown(
        "Hola, soy tu FinOps Chat Agent.\n\n"
        "Puedo ayudarte con:\n\n"
        "- Costos en GCP (resumen, detalle y tendencias).\n"
        "- Reportes de GCP (inventario, paginas, dataset y resumen ejecutivo).\n"
        "- Gobernanza de tags en GCP (recursos sin tags y validacion).\n"
        "- Recomendaciones FinOps para GCP (variaciones, optimizacion y acciones)."
    )

with tab_azure:
    st.subheader("☁️ Consultas directas AZURE")
    st.markdown(
        "Hola, soy tu FinOps Chat Agent.\n\n"
        "Puedo ayudarte con:\n\n"
        "- Costos en Azure (resumen, detalle y tendencias).\n"
        "- Reportes de Azure (inventario, paginas, dataset y resumen ejecutivo).\n"
        "- Gobernanza de tags en Azure (recursos sin tags y validacion).\n"
        "- Recomendaciones FinOps para Azure (variaciones, optimizacion y acciones)."
    )

with tab_powerbi:
    st.subheader("📊 Reportes y Datasets Power BI")
    st.markdown(
        "**Pruebas rapidas:**\n\n"
        "1. lista reportes de power bi\n"
        "2. que paginas tiene el reporte FinOps GCP - Gasto Tenant\n"
        "3. resumen ejecutivo del reporte FinOps GCP - Gasto Tenant\n"
        "4. top servicios por variacion en el reporte FinOps GCP - Gasto Tenant"
    )

with tab_tags:
    st.subheader("🏷️ Auditoría de Tags y Etiquetas")

with tab_pildoras:
    st.subheader("💡 Píldoras y Mejores Prácticas")

with tab_aws:
    st.subheader("🟧 AWS Cloud (Recursos Reales)")

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


def _get_setting(name: str, required: bool = True) -> str | None:
    value = os.environ.get(name)
    if value and str(value).strip():
        return str(value).strip().lstrip("\ufeff").strip()

    try:
        value = st.secrets[name]
        if value and str(value).strip():
            return str(value).strip().lstrip("\ufeff").strip()
    except Exception:
        pass

    if required:
        raise KeyError(f"Falta la configuracion requerida: {name}")
    return None


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


def _get_powerbi_access_token() -> str:
    env_token = os.environ.get("POWERBI_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token

    tenant_id = _get_setting("POWERBI_TENANT_ID", required=False) or _get_setting("AZURE_TENANT_ID")
    client_id = _get_setting("POWERBI_CLIENT_ID", required=False)
    client_secret = _get_setting("POWERBI_CLIENT_SECRET", required=False)

    if not client_id or not client_secret:
        raise RuntimeError(
            "No hay autenticacion Power BI valida. Define POWERBI_ACCESS_TOKEN para desarrollo local "
            "o POWERBI_CLIENT_ID/POWERBI_CLIENT_SECRET para produccion."
        )

    _assert_valid_tenant(tenant_id)
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    response = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://analysis.windows.net/powerbi/api/.default",
        },
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        # sin esto solo veriamos "400 Client Error: Bad Request", no la razon real de Azure AD
        if response.status_code == 400 and "AADSTS700016" in response.text:
            raise RuntimeError(
                "La aplicación registrada de Power BI no existe en el tenant configurado. "
                "Revisa POWERBI_CLIENT_ID y POWERBI_TENANT_ID."
            )
        raise RuntimeError(f"Power BI token request failed ({response.status_code}).")

    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"No se pudo obtener access_token de Power BI: {payload}")
    return token


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
]


def _format_powerbi_response(raw_json: str) -> str:
    try:
        data = json.loads(raw_json)
    except Exception:
        return raw_json

    if isinstance(data, dict) and "error" in data:
        return f"Error Power BI: {data.get('error')}"

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


def process_chat_prompt(prompt: str, source: str = "chat") -> None:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analizando..." if source == "chat" else "Procesando voz..."):
            try:
                final_response, used_model = ask_with_fallback(prompt)
                st.markdown(final_response)
                st.caption(f"Respondido con: {used_model}")
                st.session_state.messages.append({"role": "assistant", "content": final_response})
                render_email_button(len(st.session_state.messages) - 1, final_response)
                log_conversation(prompt, final_response, used_model)
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
    chat_prompt = st.chat_input("Escribe tu mensaje")
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
prompt = voice_prompt or chat_prompt or pending_prompt
if prompt:
    process_chat_prompt(prompt, source="voice" if voice_prompt else "chat")



