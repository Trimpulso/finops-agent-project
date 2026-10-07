"""Constructor de reportes Power BI a partir de una especificación (generada por LLM o manual).

El LLM solo elige entre un catálogo cerrado de dimensiones, medidas y tipos de visual; este módulo
valida la especificación y construye el dataset (con medidas DAX) y la definición PBIR.
"""

import re

from powerbi_report import _SCHEMA_BASE, _literal, _part

TABLE = "FinOpsCosts"
NOT_EVALUATED = "No evaluado"
ORPHAN = "Gasto Huérfano"
TAGGED = "Etiquetado"

CLOUDS = ("gcp", "azure", "aws")

DIMENSIONS = {
    "Date": "Fecha",
    "Cloud": "Nube",
    "Service": "Servicio",
    "Detail": "Detalle (SKU / grupo de recursos / tipo de uso)",
    "Scope": "Proyecto / suscripción / cuenta",
    "Environment": "Entorno (tag env)",
    "BusinessOwner": "Dueño de negocio (tag businessOwner)",
    "TaggingStatus": "Estado de etiquetado",
}

MEASURES = {
    "Costo Bruto": ("SUM({t}[Cost])", "#,##0.00"),
    "Costo Neto": ("SUM({t}[NetCost])", "#,##0.00"),
    "Costo Huerfano": ('CALCULATE(SUM({t}[Cost]), {t}[TaggingStatus] = "%s")' % ORPHAN, "#,##0.00"),
    "Costo Etiquetado": ('CALCULATE(SUM({t}[Cost]), {t}[TaggingStatus] = "%s")' % TAGGED, "#,##0.00"),
    "Pct Cumplimiento Tags": (
        'DIVIDE([Costo Etiquetado] + 0, CALCULATE(SUM({t}[Cost]), {t}[TaggingStatus] <> "%s"), 0)' % NOT_EVALUATED,
        "0.0%",
    ),
    "Costo Diario Promedio": ("DIVIDE(SUM({t}[Cost]), DISTINCTCOUNT({t}[Date]), 0)", "#,##0.00"),
    "Servicios Distintos": ("DISTINCTCOUNT({t}[Service])", "#,##0"),
}

VISUAL_TYPES = {
    "card": "card",
    "bar": "barChart",
    "column": "clusteredColumnChart",
    "line": "lineChart",
    "donut": "donutChart",
    "table": "tableEx",
    "slicer": "slicer",
}

MAX_PAGES = 5
MAX_VISUALS_PER_PAGE = 10

SPEC_INSTRUCTION = f"""Eres el diseñador de reportes de Power BI de un agente FinOps multicloud.
Convierte la petición del usuario en un JSON de especificación. Devuelve SOLO JSON válido, sin markdown.

Forma exacta:
{{"report_name": "string corto", "days": 30, "clouds": ["gcp","azure","aws"],
 "pages": [{{"title": "string", "visuals": [
   {{"type": "card", "measure": "<medida>", "title": "string"}},
   {{"type": "bar|column|donut", "dimension": "<dimension>", "measure": "<medida>", "title": "string"}},
   {{"type": "line", "dimension": "Date", "measure": "<medida>", "title": "string"}},
   {{"type": "table", "dimensions": ["<dimension>", ...], "measures": ["<medida>", ...], "title": "string"}},
   {{"type": "slicer", "dimension": "<dimension>", "title": "string"}}
 ]}}]}}

Dimensiones permitidas: {", ".join(DIMENSIONS)}.
Medidas permitidas: {", ".join(MEASURES)}.
Reglas:
- Usa solo esas dimensiones y medidas; no inventes otras.
- "clouds" solo con las nubes que pida el usuario (si no dice ninguna, usa las tres). "days" entre 7 y 365 (por defecto 30).
- Máximo {MAX_PAGES} páginas y {MAX_VISUALS_PER_PAGE} visuales por página. Empieza cada página con 2-4 tarjetas (card) de KPIs.
- "line" solo con la dimensión Date. "donut" solo con dimensiones de pocas categorías (Cloud, Environment, TaggingStatus).
- Los tags (Environment, BusinessOwner, TaggingStatus) existen para GCP y Azure. Los datos de AWS no traen tags: su TaggingStatus es "{NOT_EVALUATED}".
- Títulos en español, claros y orientados a negocio.
"""

_NAME_RE = re.compile(r"[^\w\s\-().áéíóúüñÁÉÍÓÚÜÑ]")


class SpecError(ValueError):
    pass


def merge_azure_tag_records(records: list[dict]) -> list[dict]:
    """Recombina las filas de Cost Management agrupadas por TagKey (una fila por clave, costo duplicado)
    en filas únicas con Environment y BusinessOwner.

    Es exacto cuando, para un mismo día/servicio/grupo de recursos, al menos una de las dos claves
    tiene un único valor (caso habitual). Si ambas tienen varios valores, el dueño se marca "Múltiples".
    """
    groups: dict[tuple, dict[str, dict]] = {}
    for r in records:
        key_name = str(r.get("TagKey") or "").lower()
        if key_name not in ("env", "businessowner"):
            continue
        gkey = (r.get("UsageDate"), r.get("ServiceName"), r.get("ResourceGroupName"))
        values = groups.setdefault(gkey, {"env": {}, "businessowner": {}})[key_name]
        tag_value = r.get("TagValue") or None
        values[tag_value] = values.get(tag_value, 0.0) + float(r.get("Cost") or 0)

    out = []
    for (usage_date, service, rg), tags in groups.items():
        env_items = list(tags["env"].items()) or [(None, 0.0)]
        owner_items = list(tags["businessowner"].items()) or [(None, 0.0)]
        if len(owner_items) == 1:
            combos = [(ev, owner_items[0][0], c) for ev, c in env_items]
        elif len(env_items) == 1:
            combos = [(env_items[0][0], ov, c) for ov, c in owner_items]
        else:
            owners_ok = all(ov is not None for ov, _ in owner_items)
            combos = [(ev, "Múltiples" if owners_ok else None, c) for ev, c in env_items]
        for env, owner, cost in combos:
            out.append({
                "UsageDate": usage_date, "ServiceName": service, "ResourceGroupName": rg,
                "Environment": env or "Sin Asignar", "BusinessOwner": owner or "Sin Asignar",
                "TaggingStatus": TAGGED if (env and owner) else ORPHAN, "Cost": cost,
            })
    return out


def default_spec(clouds=CLOUDS, days: int = 30) -> dict:
    return {
        "report_name": "FinOps Multicloud - Resumen",
        "days": days,
        "clouds": list(clouds),
        "pages": [
            {
                "title": "Resumen multicloud",
                "visuals": [
                    {"type": "card", "measure": "Costo Bruto", "title": "Costo bruto"},
                    {"type": "card", "measure": "Costo Diario Promedio", "title": "Costo diario promedio"},
                    {"type": "card", "measure": "Servicios Distintos", "title": "Servicios con consumo"},
                    {"type": "donut", "dimension": "Cloud", "measure": "Costo Bruto", "title": "Costo por nube"},
                    {"type": "bar", "dimension": "Service", "measure": "Costo Bruto", "title": "Top servicios por costo"},
                    {"type": "line", "dimension": "Date", "measure": "Costo Bruto", "title": "Tendencia diaria"},
                    {"type": "table", "dimensions": ["Cloud", "Service", "Scope"], "measures": ["Costo Bruto"],
                     "title": "Detalle por servicio"},
                ],
            }
        ],
    }


def _clean_title(value, fallback: str) -> str:
    text = _NAME_RE.sub("", str(value or "")).strip()
    return (text or fallback)[:80]


def validate_spec(raw: dict) -> dict:
    """Valida y normaliza la especificación; descarta visuales inválidos y falla si no queda ninguno."""
    if not isinstance(raw, dict):
        raise SpecError("La especificación debe ser un objeto JSON.")

    clouds = [c for c in (raw.get("clouds") or []) if c in CLOUDS] or list(CLOUDS)
    try:
        days = max(7, min(int(raw.get("days", 30)), 365))
    except (TypeError, ValueError):
        days = 30

    pages = []
    for page in (raw.get("pages") or [])[:MAX_PAGES]:
        visuals = []
        for v in (page.get("visuals") or [])[:MAX_VISUALS_PER_PAGE]:
            clean = _validate_visual(v)
            if clean:
                visuals.append(clean)
        if visuals:
            pages.append({"title": _clean_title(page.get("title"), f"Página {len(pages) + 1}"), "visuals": visuals})

    if not pages:
        raise SpecError("La especificación no contiene visuales válidos.")

    return {
        "report_name": _clean_title(raw.get("report_name"), "FinOps Reporte Personalizado"),
        "days": days,
        "clouds": clouds,
        "pages": pages,
    }


def _validate_visual(v) -> dict | None:
    if not isinstance(v, dict) or v.get("type") not in VISUAL_TYPES:
        return None
    vtype = v["type"]
    title = _clean_title(v.get("title"), "")
    measure = v.get("measure")
    dim = v.get("dimension")

    if vtype == "card":
        return {"type": vtype, "measure": measure, "title": title or measure} if measure in MEASURES else None
    if vtype == "slicer":
        return {"type": vtype, "dimension": dim, "title": title or dim} if dim in DIMENSIONS else None
    if vtype == "table":
        dims = [d for d in (v.get("dimensions") or []) if d in DIMENSIONS]
        meas = [m for m in (v.get("measures") or []) if m in MEASURES]
        if not dims and not meas:
            return None
        return {"type": vtype, "dimensions": dims, "measures": meas, "title": title or "Detalle"}
    if dim not in DIMENSIONS or measure not in MEASURES:
        return None
    if vtype == "line" and dim != "Date":
        vtype = "column"
    return {"type": vtype, "dimension": dim, "measure": measure, "title": title or f"{measure} por {dim}"}


def build_dataset_schema(dataset_name: str) -> dict:
    columns = [{"name": "Date", "dataType": "DateTime"}] + [
        {"name": n, "dataType": "string"}
        for n in ("Cloud", "Service", "Detail", "Scope", "Environment", "BusinessOwner", "TaggingStatus")
    ]
    columns += [{"name": "Cost", "dataType": "Double"}, {"name": "NetCost", "dataType": "Double"}]
    measures = [
        {"name": name, "expression": expr.format(t=TABLE), "formatString": fmt}
        for name, (expr, fmt) in MEASURES.items()
    ]
    return {
        "name": dataset_name,
        "defaultMode": "Push",
        "tables": [{"name": TABLE, "columns": columns, "measures": measures}],
    }


def _field(kind: str, prop: str) -> dict:
    return {kind: {"Expression": {"SourceRef": {"Entity": TABLE}}, "Property": prop}}


def _proj(kind: str, prop: str) -> dict:
    return {"field": _field(kind, prop), "queryRef": f"{TABLE}.{prop}"}


def _visual_json(name: str, spec: dict, pos: tuple) -> dict:
    vtype = spec["type"]
    if vtype == "card":
        roles = {"Values": [("Measure", spec["measure"])]}
        sort = None
    elif vtype == "slicer":
        roles = {"Values": [("Column", spec["dimension"])]}
        sort = None
    elif vtype == "table":
        roles = {"Values": [("Column", d) for d in spec["dimensions"]] + [("Measure", m) for m in spec["measures"]]}
        sort = ("Measure", spec["measures"][0]) if spec["measures"] else None
    else:
        roles = {"Category": [("Column", spec["dimension"])], "Y": [("Measure", spec["measure"])]}
        sort = ("Measure", spec["measure"]) if vtype in ("bar", "column", "donut") else None

    query = {"queryState": {r: {"projections": [_proj(k, p) for k, p in f]} for r, f in roles.items()}}
    if sort:
        query["sortDefinition"] = {"sort": [{"field": _field(*sort), "direction": "Descending"}]}

    x, y, w, h = pos
    return {
        "$schema": _SCHEMA_BASE + "visualContainer/1.0.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "width": w, "height": h, "z": 0},
        "visual": {
            "visualType": VISUAL_TYPES[vtype],
            "query": query,
            "visualContainerObjects": {
                "title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}, "text": _literal(spec["title"])}}]
            },
            "drillFilterOtherVisuals": True,
        },
    }


def layout_page(visuals: list[dict]) -> tuple[list[tuple[dict, tuple]], int]:
    """Posiciona visuales en una cuadrícula de 1280 px: tarjetas arriba, el resto en 2 columnas (tablas a todo el ancho)."""
    margin, full_w = 20, 1240
    placed, y = [], margin

    cards = [v for v in visuals if v["type"] == "card"]
    for i in range(0, len(cards), 4):
        row = cards[i:i + 4]
        w = (full_w - margin * (len(row) - 1)) // len(row)
        for j, v in enumerate(row):
            placed.append((v, (margin + j * (w + margin), y, w, 110)))
        y += 110 + margin

    pending_half = None
    for v in [v for v in visuals if v["type"] != "card"]:
        if v["type"] == "table":
            if pending_half:
                placed.append((pending_half[0], (pending_half[1], y, 610, 270)))
                y += 270 + margin
                pending_half = None
            placed.append((v, (margin, y, full_w, 280)))
            y += 280 + margin
        elif pending_half is None:
            pending_half = (v, margin)
        else:
            placed.append((pending_half[0], (margin, y, 610, 270)))
            placed.append((v, (margin + 630, y, 610, 270)))
            y += 270 + margin
            pending_half = None
    if pending_half:
        placed.append((pending_half[0], (margin, y, 610, 270)))
        y += 270 + margin
    return placed, max(720, y)


def build_report_parts(dataset_id: str, spec: dict) -> list[dict]:
    page_ids = [f"p{i + 1}" for i in range(len(spec["pages"]))]
    parts = [
        _part(
            "definition.pbir",
            {
                "version": "4.0",
                "datasetReference": {
                    "byConnection": {
                        "connectionString": None,
                        "pbiServiceModelId": None,
                        "pbiModelVirtualServerName": "sobe_wowvirtualserver",
                        "pbiModelDatabaseName": dataset_id,
                        "name": "EntityDataSource",
                        "connectionType": "pbiServiceXmlaStyleLive",
                    }
                },
            },
        ),
        _part("definition/version.json", {"$schema": _SCHEMA_BASE + "versionMetadata/1.0.0/schema.json", "version": "2.0.0"}),
        _part(
            "definition/report.json",
            {
                "$schema": _SCHEMA_BASE + "report/1.0.0/schema.json",
                "themeCollection": {
                    "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.59", "type": "SharedResources"}
                },
                "layoutOptimization": "None",
            },
        ),
        _part(
            "definition/pages/pages.json",
            {
                "$schema": _SCHEMA_BASE + "pagesMetadata/1.0.0/schema.json",
                "pageOrder": page_ids,
                "activePageName": page_ids[0],
            },
        ),
    ]
    for pid, page in zip(page_ids, spec["pages"]):
        placed, height = layout_page(page["visuals"])
        parts.append(
            _part(
                f"definition/pages/{pid}/page.json",
                {
                    "$schema": _SCHEMA_BASE + "page/1.0.0/schema.json",
                    "name": pid,
                    "displayName": page["title"],
                    "displayOption": "FitToPage",
                    "height": height,
                    "width": 1280,
                },
            )
        )
        for i, (vspec, pos) in enumerate(placed, start=1):
            vname = f"v{i}"
            parts.append(_part(f"definition/pages/{pid}/visuals/{vname}/visual.json", _visual_json(vname, vspec, pos)))
    return parts
