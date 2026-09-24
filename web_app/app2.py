import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
warnings.filterwarnings("ignore", category=UserWarning, module="google.cloud.bigquery.table")

import json
import os
import re
import smtplib
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import google.auth
import google.generativeai as genai
import requests
import streamlit as st
from google.api_core.exceptions import ResourceExhausted
from google.cloud import bigquery

# =========================
# Config base
# =========================
DEFAULT_PROJECT_ID = "project-5f47ed36-9aec-4f46-a30"
BILLING_TABLE = "project-5f47ed36-9aec-4f46-a30.billing_export.gcp_billing_export_v1_011B52_EC7045_1117AB"
CONVERSATIONS_TABLE = "project-5f47ed36-9aec-4f46-a30.finops_agent.conversations"
DEFAULT_DAYS = 30
MODEL_NAMES = ["gemini-3.5-flash","gemini-3.5-flash-lite","gemini-3.0-flash","gemini-3.0-flash-lite","gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"]
_CREDITS_EXPR = "IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)"

_REQUIRED_TAGS = ["businessOwner", "env"]
_VALID_ENVS = ["dev", "test", "staging", "prod", "development", "production"]

POWERBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"
DEFAULT_POWERBI_WORKSPACE_ID = os.environ.get("POWERBI_WORKSPACE_ID", "").strip()

st.set_page_config(page_title="FinOps Chat Agent", layout="centered")
st.title("FinOps Chat Agent")
st.caption("Agente para costos GCP/Azure y consultas de tags")

_KB_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.txt")
if os.path.exists(_KB_PATH):
    with open(_KB_PATH, "r", encoding="utf-8") as f:
        FINOPS_KNOWLEDGE = f.read()
else:
    FINOPS_KNOWLEDGE = ""


def _get_setting(name: str, required: bool = True) -> str | None:
    value = os.environ.get(name)
    if value and str(value).strip():
        return str(value).strip()

    try:
        value = st.secrets[name]
        if value and str(value).strip():
            return str(value).strip()
    except Exception:
        pass

    if required:
        raise KeyError(f"Falta la configuracion requerida: {name}")
    return None


def send_email_smtp(to_address: str, subject: str, body: str) -> tuple[bool, str]:
    try:
        to_address = (to_address or "").strip()
        if not to_address:
            return False, "Debes indicar un correo destino."

        gmail_address = _get_setting("GMAIL_ADDRESS", required=False)
        gmail_password = _get_setting("GMAIL_APP_PASSWORD", required=False)
        if not gmail_address or not gmail_password:
            return False, "Falta configurar GMAIL_ADDRESS / GMAIL_APP_PASSWORD en el servicio."

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
    with st.expander("Enviar por correo"):
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


try:
    genai.configure(api_key=_get_setting("GEMINI_API_KEY"))
except Exception as e:
    st.error(f"No se pudo configurar Gemini: {e}")
    st.stop()


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
        raise RuntimeError(f"Azure token request failed ({response.status_code}): {response.text[:1500]}")

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
        raise RuntimeError(f"Power BI token request failed ({response.status_code}): {response.text[:1500]}")

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
    "Eres un agente FinOps para Azure, GCP y Power BI. "
    "Si la pregunta trata de reportes de Power BI, consulta inventario, paginas y dataset del reporte. "
    "Responde en espanol claro, accionable y con foco en costos. "
    "Cuando uses Power BI, indica workspace, reporte y dataset usados."
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


def ask_with_fallback(prompt: str):
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

    last_error = None
    for model_name in MODEL_NAMES:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                tools=TOOLS,
                system_instruction=SYSTEM_INSTRUCTION,
            )
            chat = model.start_chat(enable_automatic_function_calling=True)
            response = chat.send_message(enriched)
            return response.text, model_name

        except ResourceExhausted as e:
            last_error = e
            st.info(f"Cuota agotada en {model_name}. Probando siguiente modelo...")
            continue

        except Exception as e:
            last_error = e
            st.warning(f"Error con {model_name}: {e}. Probando siguiente modelo...")
            continue

    if last_error is not None:
        raise last_error

    raise RuntimeError("No se pudo generar respuesta con ningún modelo disponible.")


WELCOME_MESSAGE = (
    "Hola, soy tu FinOps Chat Agent.\n\n"
    "Puedo ayudarte con:\n"
    "- Costos en GCP y Azure (resumen, detalle y tendencias).\n"
    "- Reportes de Power BI (inventario, paginas, dataset y resumen ejecutivo).\n"
    "- Gobernanza de tags en Azure (recursos sin tags y validacion).\n"
    "- Recomendaciones FinOps (variaciones, optimizacion y acciones).\n\n"
    "Pruebas rapidas:\n"
    "1) lista reportes de power bi\n"
    "2) que paginas tiene el reporte FinOps Azure - Gasto Tenant\n"
    "3) resumen ejecutivo del reporte FinOps Azure - Gasto Tenant\n"
    "4) top servicios por variacion en el reporte FinOps Azure - Gasto Tenant"
)

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]
else:
    if (
        st.session_state.messages
        and st.session_state.messages[0].get("role") == "assistant"
        and "Como puedo ayudarte a analizar y optimizar costos de GCP o Azure" in st.session_state.messages[0].get("content", "")
    ):
        st.session_state.messages[0]["content"] = WELCOME_MESSAGE

for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_email_button(idx, message["content"])

if prompt := st.chat_input("Your message"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analizando..."):
            try:
                final_response, used_model = ask_with_fallback(prompt)
                st.markdown(final_response)
                st.caption(f"Respondido con: {used_model}")
                st.session_state.messages.append({"role": "assistant", "content": final_response})
                log_conversation(prompt, final_response, used_model)
                render_email_button(len(st.session_state.messages) - 1, final_response)
            except ResourceExhausted:
                msg = "Se agoto la cuota diaria de modelos gratuitos. Intenta mas tarde o usa paid tier."
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
            except Exception as e:
                msg = f"Ocurrio un error al procesar la consulta: {e}"
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})