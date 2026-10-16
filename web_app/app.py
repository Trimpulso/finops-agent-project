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
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("UTF-8").strip()
    except Exception:
        pass

    try:
        import base64
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
    value = os.environ.get(name)
    if value and str(value).strip():
        return str(value).strip().lstrip("\ufeff").strip()
    
    try:
        value = st.secrets[name]
        if value and str(value).strip():
            return str(value).strip().lstrip("\ufeff").strip()
    except Exception:
        pass

    sm_secret_name = SECRET_MANAGER_MAP.get(name)
    if sm_secret_name:
        sm_value = _fetch_from_secret_manager(sm_secret_name)
        if sm_value:
            sm_value = str(sm_value).lstrip("\ufeff").strip()
            os.environ[name] = sm_value
            return sm_value

    if required:
        st.warning(f"Falta configurar: `{name}`")
    return None

# ==========================================
# CONECTOR TIMESFM 2.5 (Proyección Multicloud)
# ==========================================
def obtener_prediccion_timesfm(fechas: list[str], valores: list[float], horizonte: int = 3) -> list[float] | None:
    """
    Consulta el microservicio de TimesFM 2.5 para proyectar costos futuros.
    """
    timesfm_url = os.getenv("TIMESFM_API_URL", "https://crispy-space-funicular-69rxj49x5wghw6r-8000.app.github.dev/api/v1/forecast")
    
    payload = {
        "fechas": fechas,
        "valores": valores,
        "horizonte": horizonte
    }
    
    try:
        response = requests.post(timesfm_url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("prediccion")
    except Exception as e:
        print(f"Error conectando con TimesFM: {e}")
        return None

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

        res_rds = rds.describe_db_instances()
        for db in res_rds.get('DBInstances', []):
            reporte.append({
                "Servicio": "RDS",
                "Recurso": db['DBInstanceIdentifier'],
                "Estado": db['DBInstanceStatus'],
                "Tipo": db['DBInstanceClass']
            })

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

POWERBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"

try:
    _, DEFAULT_GCP_PROJECT = google.auth.default()
except:
    DEFAULT_GCP_PROJECT = "project-5f47ed36-9aec-4f46-a30"
DEFAULT_POWERBI_WORKSPACE_ID = os.environ.get("POWERBI_WORKSPACE_ID", "").strip()

st.set_page_config(page_title="FinOps Chat Agent", layout="centered")
st.title("FinOps Chat Agent")
st.caption("Agente para costos GCP/Azure y consultas de tags")

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
    response.raise_for_status()
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

def get_azure_resources_without_tags(resource_group: str | None = None) -> str:
    try:
        subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")
        token = _get_azure_access_token()
        query = "Resources | where isnull(tags) or array_length(bag_keys(tags)) == 0 | project id, name, type, resourceGroup | limit 200"
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
            return json.dumps({"error": "Recurso no encontrado", "resource_id": rid}, ensure_ascii=False)
        tags = rows[0].get("tags") or {}
        return json.dumps({"resource_id": rid, "has_tags": len(tags) > 0, "tags": tags, "tags_count": len(tags)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def _is_tag_query(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in ["sin tag", "sin etiqueta", "sin tags"]):
        return "without"
    if any(k in p for k in ["tags del recurso", "etiquetas del recurso"]):
        return "get"
    return ""

def _format_tags_response(result_json: str, query_type: str) -> str:
    try:
        data = json.loads(result_json)
        if "error" in data:
            return f"Error: {data.get('error')}"
        if query_type == "without":
            total = data.get("total_without_tags", 0)
            return f"Se encontraron {total} recursos sin tags en Azure."
        if query_type == "get":
            tags = data.get("tags", {})
            return f"Tags encontrados: {tags}"
    except Exception as e:
        return f"Error formateando tags: {e}"
    return ""

def ask_with_fallback(prompt: str):
    conversational_response = _handle_conversational_prompt(prompt)
    if conversational_response:
        return conversational_response

    tag_mode = _is_tag_query(prompt)
    if tag_mode == "without":
        return _format_tags_response(get_azure_resources_without_tags(), "without"), "azure-resource-graph"
    if tag_mode == "get":
        return _format_tags_response(get_azure_resource_tags(prompt), "get"), "azure-resource-graph"

    enriched = prompt
    if FINOPS_KNOWLEDGE:
        enriched = f"Usa este contexto FinOps:\n{FINOPS_KNOWLEDGE}\n\nConsulta: {prompt}"

    gemini_key = _get_setting("GEMINI_API_KEY")
    for model_name in MODEL_NAMES:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": gemini_key}
            payload = {"contents": [{"parts": [{"text": enriched}]}], "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]}}
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            if "candidates" in result and result["candidates"]:
                text = result["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "")
                if text:
                    return text, model_name
        except Exception:
            continue
    raise RuntimeError("No se pudo generar respuesta con ningún modelo.")

def _handle_conversational_prompt(prompt: str) -> tuple[str, str] | None:
    normalized = re.sub(r"[^a-záéíóúüñ ]", "", prompt.lower()).strip()
    if normalized in {"hola", "holá", "buenas", "buenos dias", "buenas tardes", "buenas noches"}:
        return "Hola. Soy tu agente FinOps. ¿Qué necesitas revisar?", "conversation"
    return None

SYSTEM_INSTRUCTION = "Eres un agente FinOps multicloud. Responde en español claro."

WELCOME_MESSAGE = "Hola, soy tu FinOps Chat Agent."

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]

def process_chat_prompt(prompt: str, source: str = "chat", files: list | None = None) -> None:
    files = files or []
    shown_prompt = prompt + (("\n\n📎 " + ", ".join(f.name for f in files)) if files else "")
    st.session_state.messages.append({"role": "user", "content": shown_prompt})
    with st.chat_message("user"):
        st.markdown(shown_prompt)

    with st.chat_message("assistant"):
        with st.spinner("Procesando..."):
            try:
                final_response, used_model = ask_with_fallback(prompt)
                st.markdown(final_response)
                st.caption(f"Respondido con: {used_model}")
                st.session_state.messages.append({"role": "assistant", "content": final_response})
                render_email_button(len(st.session_state.messages) - 1, final_response)
            except Exception as e:
                st.error(f"Error: {e}")

with tab_chat:
    with st.chat_message(st.session_state.messages[0]["role"]):
        st.markdown(st.session_state.messages[0]["content"])
        render_email_button(0, st.session_state.messages[0]["content"])

for idx, message in enumerate(st.session_state.messages[1:], start=1):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_email_button(idx, message["content"])

chat_col, voice_col = st.columns([8, 1], vertical_alignment="bottom")
with chat_col:
    chat_value = st.chat_input("Escribe tu mensaje", accept_file="multiple", file_type=ALLOWED_FILE_TYPES)

pending_prompt = st.session_state.pop("pending_prompt", None)
chat_prompt = chat_value.text if chat_value else None
chat_files = list(chat_value.files) if chat_value else []
prompt = chat_prompt or pending_prompt
if prompt or chat_files:
    process_chat_prompt(prompt or "", source="chat", files=chat_files)