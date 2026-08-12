import json
import os
from datetime import datetime, timedelta

import google.auth
import google.generativeai as genai
import requests
import streamlit as st
from google.api_core.exceptions import ResourceExhausted
from google.cloud import bigquery

# Importar módulo de tags
# Tag validation schema

# FinOps knowledge base cargado al inicio
_KB_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.txt")
FINOPS_KNOWLEDGE = open(_KB_PATH, encoding="utf-8").read() if os.path.exists(_KB_PATH) else ""

# Tabla BigQuery donde se registran las conversaciones para mejoras futuras
CONVERSATIONS_TABLE = "project-5f47ed36-9aec-4f46-a30.finops_agent.conversations"


def log_conversation(pregunta: str, respuesta: str, modelo: str, categoria: str = "general") -> None:
    try:
        client = _bq_client()
        row = {
            "timestamp": datetime.utcnow().isoformat(),
            "pregunta_usuario": pregunta,
            "respuesta_agente": respuesta,
            "modelo": modelo,
            "categoria_detectada": categoria,
        }
        client.insert_rows_json(CONVERSATIONS_TABLE, [row])
    except Exception:
        pass  # el logging nunca debe romper la respuesta al usuario

_REQUIRED_TAGS = ["businessOwner", "env"]
_VALID_ENVS = ["dev", "test", "staging", "prod", "development", "production"]

st.set_page_config(page_title="FinOps Chat Agent", layout="centered")
st.title("💬 FinOps Chat Agent")
st.caption("Un agente inteligente para consultar y analizar costos en GCP y Azure + gestión de tags")

DEFAULT_PROJECT_ID = "project-5f47ed36-9aec-4f46-a30"
BILLING_TABLE = "project-5f47ed36-9aec-4f46-a30.billing_export.gcp_billing_export_v1_011B52_EC7045_1117AB"
DEFAULT_DAYS = 30

MODEL_NAMES = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"]

_CREDITS_EXPR = "IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)"


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
        raise KeyError(f"Falta la configuración requerida: {name}")
    return None


try:
    genai.configure(api_key=_get_setting("GEMINI_API_KEY"))
except Exception as e:
    st.error(f"Error: No se pudo configurar Gemini. Detalle: {e}")
    st.stop()


def _bq_client():
    credentials, project = google.auth.default()
    return bigquery.Client(credentials=credentials, project=project)


def _azure_date_range(days: int) -> tuple[str, str]:
    days = max(1, int(days))
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days - 1)
    return start_date.isoformat(), end_date.isoformat()


def _get_azure_access_token() -> str:
    tenant_id = _get_setting("AZURE_TENANT_ID")
    client_id = _get_setting("AZURE_CLIENT_ID")
    client_secret = _get_setting("AZURE_CLIENT_SECRET")

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

    if "access_token" not in payload:
        raise RuntimeError(f"No se pudo obtener access_token de Azure: {payload}")

    return payload["access_token"]


def _run_azure_cost_query(payload: dict) -> dict:
    subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")
    token = _get_azure_access_token()

    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        "/providers/Microsoft.CostManagement/query?api-version=2023-03-01"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _azure_records(result: dict) -> list[dict]:
    properties = result.get("properties", {})
    columns = [column.get("name") for column in properties.get("columns", [])]
    rows = properties.get("rows", [])

    records = []
    for row in rows:
        record = {}
        for index, column_name in enumerate(columns):
            record[column_name] = row[index] if index < len(row) else None
        records.append(record)
    return records


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


@st.cache_data(ttl=3600)
def get_cost_summary_by_service(project_id: str = DEFAULT_PROJECT_ID, days: int = DEFAULT_DAYS):
    """
    Resumen de costos por servicio de GCP en los últimos 'days' días.
    Devuelve costo bruto (gross_cost), créditos aplicados (credits) y costo neto real (net_cost) en USD.
    """
    st.write(f"🔧 Ejecutando GCP: get_cost_summary_by_service (proyecto={project_id}, días={days})...")
    try:
        client = _bq_client()
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"""
            SELECT
                service.description AS service,
                SUM(cost) AS gross_cost,
                SUM({_CREDITS_EXPR}) AS credits,
                SUM(cost) + SUM({_CREDITS_EXPR}) AS net_cost
            FROM `{BILLING_TABLE}`
            WHERE project.id = '{project_id}' AND DATE(usage_start_time) >= '{start_date}'
            GROUP BY service
            HAVING gross_cost > 0.01
            ORDER BY gross_cost DESC
        """
        return client.query(query).to_dataframe().to_json(orient="records")
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_cost_by_sku_for_service(service_name: str, project_id: str = DEFAULT_PROJECT_ID, days: int = DEFAULT_DAYS):
    """
    Desglose detallado por SKU para un servicio específico de GCP en los últimos 'days' días.
    """
    st.write(f"🔧 Ejecutando GCP: get_cost_by_sku_for_service (servicio='{service_name}', días={days})...")
    try:
        client = _bq_client()
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"""
            SELECT
                sku.description AS sku,
                SUM(usage.amount) AS usage_amount,
                ANY_VALUE(usage.unit) AS usage_unit,
                SUM(cost) AS gross_cost,
                SUM({_CREDITS_EXPR}) AS credits,
                SUM(cost) + SUM({_CREDITS_EXPR}) AS net_cost
            FROM `{BILLING_TABLE}`
            WHERE project.id = '{project_id}'
              AND service.description = '{service_name}'
              AND DATE(usage_start_time) >= '{start_date}'
            GROUP BY sku
            HAVING gross_cost > 0.001
            ORDER BY gross_cost DESC
        """
        return client.query(query).to_dataframe().to_json(orient="records")
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_daily_trend_for_service(service_name: str, project_id: str = DEFAULT_PROJECT_ID, days: int = DEFAULT_DAYS):
    """
    Tendencia diaria de costo para un servicio específico de GCP.
    """
    st.write(f"🔧 Ejecutando GCP: get_daily_trend_for_service (servicio='{service_name}', días={days})...")
    try:
        client = _bq_client()
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"""
            SELECT DATE(usage_start_time) AS cost_date, SUM(cost) AS daily_cost
            FROM `{BILLING_TABLE}`
            WHERE project.id = '{project_id}'
              AND service.description = '{service_name}'
              AND DATE(usage_start_time) >= '{start_date}'
            GROUP BY cost_date
            ORDER BY cost_date ASC
        """
        df = client.query(query).to_dataframe()
        df["cost_date"] = df["cost_date"].astype(str)
        return df.to_json(orient="records")
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_azure_cost_summary_by_service(days: int = DEFAULT_DAYS):
    """
    Resumen de costos de Azure por servicio en los últimos 'days' días.
    Devuelve service, total_cost y currency.
    """
    st.write(f"🔧 Ejecutando Azure: get_azure_cost_summary_by_service (días={days})...")
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": start_date,
                "to": end_date,
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {
                    "totalCost": {
                        "name": "Cost",
                        "function": "Sum",
                    }
                },
                "grouping": [
                    {
                        "type": "Dimension",
                        "name": "ServiceName",
                    }
                ],
                "sorting": [
                    {
                        "direction": "descending",
                        "name": "Cost",
                    }
                ],
            },
        }

        result = _run_azure_cost_query(payload)
        records = _azure_records(result)

        normalized = []
        for record in records:
            normalized.append(
                {
                    "service": record.get("ServiceName", "Unknown"),
                    "total_cost": _azure_cost_value(record),
                    "currency": record.get("Currency", "USD"),
                }
            )

        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_azure_cost_by_meter_for_service(service_name: str, days: int = DEFAULT_DAYS):
    """
    Desglose de costo de Azure para un servicio específico usando Meter como equivalente cercano a SKU.
    """
    st.write(f"🔧 Ejecutando Azure: get_azure_cost_by_meter_for_service (servicio='{service_name}', días={days})...")
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": start_date,
                "to": end_date,
            },
            "dataset": {
                "granularity": "None",
                "aggregation": {
                    "totalCost": {
                        "name": "Cost",
                        "function": "Sum",
                    }
                },
                "filter": {
                    "dimensions": {
                        "name": "ServiceName",
                        "operator": "In",
                        "values": [service_name],
                    }
                },
                "grouping": [
                    {
                        "type": "Dimension",
                        "name": "Meter",
                    }
                ],
                "sorting": [
                    {
                        "direction": "descending",
                        "name": "Cost",
                    }
                ],
            },
        }

        result = _run_azure_cost_query(payload)
        records = _azure_records(result)

        normalized = []
        for record in records:
            normalized.append(
                {
                    "meter": record.get("Meter", "Unknown"),
                    "total_cost": _azure_cost_value(record),
                    "currency": record.get("Currency", "USD"),
                }
            )

        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@st.cache_data(ttl=3600)
def get_azure_daily_trend_for_service(service_name: str, days: int = DEFAULT_DAYS):
    """
    Tendencia diaria de costo en Azure para un servicio específico.
    """
    st.write(f"🔧 Ejecutando Azure: get_azure_daily_trend_for_service (servicio='{service_name}', días={days})...")
    try:
        start_date, end_date = _azure_date_range(days)
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {
                "from": start_date,
                "to": end_date,
            },
            "dataset": {
                "granularity": "Daily",
                "aggregation": {
                    "totalCost": {
                        "name": "Cost",
                        "function": "Sum",
                    }
                },
                "filter": {
                    "dimensions": {
                        "name": "ServiceName",
                        "operator": "In",
                        "values": [service_name],
                    }
                },
                "sorting": [
                    {
                        "direction": "ascending",
                        "name": "UsageDate",
                    }
                ],
            },
        }

        result = _run_azure_cost_query(payload)
        records = _azure_records(result)

        normalized = []
        for record in records:
            normalized.append(
                {
                    "cost_date": _normalize_azure_date(record.get("UsageDate")),
                    "daily_cost": _azure_cost_value(record),
                    "currency": record.get("Currency", "USD"),
                }
            )

        return json.dumps(normalized, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ============ TAG QUERY FUNCTIONS ============

def get_azure_resources_without_tags(resource_group: str = None) -> str:
    """
    Consulta recursos de Azure que NO tienen tags asignados.
    Usa Azure Resource Graph para encontrar recursos sin etiquetas.

    Args:
        resource_group: Nombre del resource group a filtrar (opcional).

    Returns:
        JSON con lista de recursos sin tags: name, type, resourceGroup, id.
    """
    st.write(f"Consultando recursos Azure sin tags...")
    try:
        subscription_id = _get_setting("AZURE_SUBSCRIPTION_ID")
        token = _get_azure_access_token()
        if resource_group:
            query = f"Resources | where resourceGroup =~ '{resource_group}' | where isnull(tags) or array_length(bag_keys(tags)) == 0 | project id, name, type, resourceGroup | limit 200"
        else:
            query = f"Resources | where resourceGroup =~ '{resource_group}' | where isnull(tags) or array_length(bag_keys(tags)) == 0 | project id, name, type, resourceGroup | limit 200"
        url = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"query": query, "subscriptions": [subscription_id]},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("data", [])
        return json.dumps({"total_without_tags": len(rows), "resources": rows}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def get_azure_resource_tags(resource_id: str) -> str:
    """Consulta los tags de un recurso Azure usando Resource Graph (no requiere rol Reader)."""
    st.write("Consultando tags del recurso Azure...")
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
        return json.dumps({"resource_id": rid, "has_tags": len(tags) > 0, "tags": tags, "tags_count": len(tags)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "resource_id": resource_id}, ensure_ascii=False)


def _is_tag_query(prompt: str) -> str:
    p = prompt.lower()
    no_tags = ["sin tag", "sin etiqueta", "no tiene tag", "no tienen tag",
               "sin tags", "sin etiquetas", "recursos sin", "without tag",
               "no tienen etiqueta", "recursos que no tienen"]
    has_tags = ["tags del recurso", "etiquetas del recurso", "que tags tiene",
                "ver tags", "mostrar tags", "tags asignados a", "que etiquetas tiene"]
    if any(k in p for k in no_tags):
        return "without"
    if any(k in p for k in has_tags):
        return "get"
    return ""


def _format_tags_response(result_json: str, query_type: str) -> str:
    import json as _json
    try:
        data = _json.loads(result_json)
        if "error" in data:
            return f"Error al consultar Azure: {data.get('error', 'desconocido')}"
        if query_type == "without":
            total = data.get("total_without_tags", 0)
            resources = data.get("resources", [])
            if total == 0:
                return "Todos los recursos en Azure tienen tags asignados."
            out = [f"Se encontraron **{total} recursos sin tags** en Azure:\n"]
            for r in resources[:20]:
                cols = data.get("columns", [])
                if isinstance(r, dict):
                    out.append(f"- **{r.get('name','?')}** | {r.get('type','?')} | RG: {r.get('resourceGroup','?')}")
                elif isinstance(r, list) and len(r) >= 4:
                    out.append(f"- **{r[1]}** | {r[2]} | RG: {r[3]}")
            if total > 20:
                out.append(f"\n...y {total-20} recursos mas.")
            out.append("\n**Recomendacion FinOps:** Asigna businessOwner y env a cada recurso sin tags.")
            return "\n".join(out)
        if query_type == "get":
            tags = data.get("tags", {})
            if not data.get("has_tags", False):
                return "El recurso **no tiene tags asignados**. Asigna: businessOwner, env, businessUnit, ac."
            out = [f"**{data.get('tags_count',0)} tags encontrados:**\n"]
            for k, v in tags.items():
                out.append(f"- **{k}**: {v}")
            return "\n".join(out)
    except Exception as e:
        return f"Error: {e}"


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

    last_error = None
    for model_name in MODEL_NAMES:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                tools=TOOLS,
                system_instruction=SYSTEM_INSTRUCTION,
            )
            chat = model.start_chat(enable_automatic_function_calling=True)
            response = chat.send_message(prompt)
            return response.text, model_name
        except ResourceExhausted as e:
            last_error = e
            st.info(f"Cuota agotada en model. Intentando con el siguiente...")
            continue
    raise last_error

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
            st.info(f"Cuota agotada en '{model_name}'. Intentando con el siguiente modelo...")
            continue
    raise last_error


if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "¿Cómo puedo ayudarte a analizar y optimizar tus costos de GCP o Azure? También puedo ayudarte a gestionar tags en tus recursos."
        }
    ]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Your message"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analizando..."):
            try:
                final_response, used_model = ask_with_fallback(prompt)
                st.markdown(final_response)
                st.caption(f"_Respondido con: {used_model}_")
                st.session_state.messages.append({"role": "assistant", "content": final_response})
                log_conversation(prompt, final_response, used_model)
                log_conversation(prompt, final_response, used_model)
                log_conversation(prompt, final_response, used_model)
            except ResourceExhausted:
                msg = "⚠️ Se agotó la cuota diaria de todos los modelos gratuitos. Intenta de nuevo más tarde o activa el paid tier."
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
            except Exception as e:
                msg = f"❌ Ocurrió un error al procesar la consulta: {e}"
                st.error(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})



