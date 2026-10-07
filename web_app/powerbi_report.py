"""Modelo (tabla + medidas DAX) y definición PBIR del reporte FinOps para Power BI."""

import base64
import json

TABLE = "FinOpsBilling"
ORPHAN = "Gasto Huérfano"
ZOMBIE_CATEGORIES = ("Discos y Snapshots", "IPs reservadas")
RIGHTSIZING_RATE = 0.35

_SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"


def classify_resource(service: str, sku: str) -> str:
    s, k = (service or "").lower(), (sku or "").lower()
    if "static ip" in k or "reserved ip" in k or ("unused" in k and "ip" in k):
        return "IPs reservadas"
    if "pd capacity" in k or "persistent disk" in k or "snapshot" in k or "disk" in k:
        return "Discos y Snapshots"
    if "cloud storage" in s or "bucket" in k:
        return "Almacenamiento"
    if "compute engine" in s or "kubernetes" in s or "cloud run" in s or "functions" in s:
        return "Cómputo"
    if "network" in s or "egress" in k or "load balanc" in k or "nat" in k.split():
        return "Red"
    if any(x in s for x in ("bigquery", "sql", "spanner", "firestore", "bigtable", "memorystore")):
        return "Datos y BD"
    return "Otros"


def _col(name, data_type):
    return {"name": name, "dataType": data_type}


def _measure(name, expression, fmt=None):
    m = {"name": name, "expression": expression}
    if fmt:
        m["formatString"] = fmt
    return m


def build_dataset_schema(dataset_name: str) -> dict:
    t = TABLE
    zombie_list = ", ".join(f'"{c}"' for c in ZOMBIE_CATEGORIES)
    return {
        "name": dataset_name,
        "defaultMode": "Push",
        "tables": [
            {
                "name": t,
                "columns": [
                    _col("Date", "DateTime"),
                    _col("CloudProvider", "string"),
                    _col("Service", "string"),
                    _col("SKU", "string"),
                    _col("Project", "string"),
                    _col("ResourceCategory", "string"),
                    _col("GrossCost", "Double"),
                    _col("Credits", "Double"),
                    _col("NetCost", "Double"),
                    _col("TaggingStatus", "string"),
                    _col("BusinessOwner", "string"),
                    _col("Environment", "string"),
                ],
                "measures": [
                    _measure("Costo Bruto", f"SUM({t}[GrossCost])", "#,##0.00"),
                    _measure("Costo Neto", f"SUM({t}[NetCost])", "#,##0.00"),
                    _measure("Creditos", f"SUM({t}[Credits])", "#,##0.00"),
                    _measure(
                        "Costo Huerfano",
                        f'CALCULATE(SUM({t}[GrossCost]), {t}[TaggingStatus] = "{ORPHAN}")',
                        "#,##0.00",
                    ),
                    _measure(
                        "Costo Etiquetado",
                        f'CALCULATE(SUM({t}[GrossCost]), {t}[TaggingStatus] = "Etiquetado")',
                        "#,##0.00",
                    ),
                    _measure(
                        "Pct Cumplimiento Tags",
                        "DIVIDE([Costo Etiquetado] + 0, [Costo Bruto], 0)",
                        "0.0%",
                    ),
                    _measure(
                        "Costo Candidatos Zombie",
                        f'CALCULATE(SUM({t}[GrossCost]), {t}[ResourceCategory] IN {{{zombie_list}}}, '
                        f'{t}[TaggingStatus] = "{ORPHAN}")',
                        "#,##0.00",
                    ),
                    _measure(
                        "Costo Discos e IPs",
                        f"CALCULATE(SUM({t}[GrossCost]), {t}[ResourceCategory] IN {{{zombie_list}}})",
                        "#,##0.00",
                    ),
                    _measure(
                        "Costo Computo",
                        f'CALCULATE(SUM({t}[GrossCost]), {t}[ResourceCategory] = "Cómputo")',
                        "#,##0.00",
                    ),
                    _measure("Pct Computo", "DIVIDE([Costo Computo] + 0, [Costo Bruto], 0)", "0.0%"),
                    _measure(
                        "Ahorro Rightsizing Est",
                        f"[Costo Computo] * {RIGHTSIZING_RATE}",
                        "#,##0.00",
                    ),
                ],
            }
        ],
    }


def _field(kind: str, prop: str) -> dict:
    return {kind: {"Expression": {"SourceRef": {"Entity": TABLE}}, "Property": prop}}


def _proj(kind: str, prop: str) -> dict:
    return {"field": _field(kind, prop), "queryRef": f"{TABLE}.{prop}"}


def _literal(text: str) -> dict:
    return {"expr": {"Literal": {"Value": "'" + text.replace("'", "''") + "'"}}}


def _visual(name, vtype, pos, title, roles, sort=None):
    x, y, w, h = pos
    query = {
        "queryState": {
            role: {"projections": [_proj(kind, prop) for kind, prop in fields]}
            for role, fields in roles.items()
        }
    }
    if sort:
        query["sortDefinition"] = {
            "sort": [{"field": _field(*sort), "direction": "Descending"}]
        }
    return {
        "$schema": _SCHEMA_BASE + "visualContainer/1.0.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "width": w, "height": h, "z": 0},
        "visual": {
            "visualType": vtype,
            "query": query,
            "visualContainerObjects": {
                "title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}, "text": _literal(title)}}]
            },
            "drillFilterOtherVisuals": True,
        },
    }


def _card(name, x, w, measure, title):
    return _visual(name, "card", (x, 20, w, 110), title, {"Values": [("Measure", measure)]})


def _pages() -> list[dict]:
    M, C = "Measure", "Column"
    resumen = {
        "name": "resumen",
        "displayName": "Resumen y Tagging",
        "visuals": [
            _card("kpi_bruto", 20, 300, "Costo Bruto", "Costo bruto"),
            _card("kpi_neto", 330, 300, "Costo Neto", "Costo neto (tras créditos)"),
            _card("kpi_huerfano", 640, 300, "Costo Huerfano", "Costo sin atribuir (tags faltantes)"),
            _card("kpi_pct", 950, 310, "Pct Cumplimiento Tags", "% cumplimiento de tagging"),
            _visual("donut_tags", "donutChart", (20, 150, 400, 270), "Estado de etiquetado",
                    {"Category": [(C, "TaggingStatus")], "Y": [(M, "Costo Bruto")]}),
            _visual("bar_env", "barChart", (440, 150, 400, 270), "Costo sin etiquetar por entorno",
                    {"Category": [(C, "Environment")], "Y": [(M, "Costo Huerfano")]}, sort=(M, "Costo Huerfano")),
            _visual("slicer_proj", "slicer", (860, 150, 400, 270), "Proyecto",
                    {"Values": [(C, "Project")]}),
            _visual("line_trend", "lineChart", (20, 440, 620, 260), "Tendencia diaria de costo bruto",
                    {"Category": [(C, "Date")], "Y": [(M, "Costo Bruto")]}),
            _visual("tbl_remediacion", "tableEx", (660, 440, 600, 260), "Remediación de tags (detalle)",
                    {"Values": [(C, "Project"), (C, "BusinessOwner"), (C, "Environment"), (M, "Costo Huerfano")]},
                    sort=(M, "Costo Huerfano")),
        ],
    }
    zombies = {
        "name": "zombies",
        "displayName": "Operación Limpieza (Zombies)",
        "visuals": [
            _card("kpi_zombie", 20, 400, "Costo Candidatos Zombie",
                  "Ahorro potencial: discos/IPs sin dueño"),
            _card("kpi_discos", 440, 400, "Costo Discos e IPs", "Costo total discos, snapshots e IPs"),
            _card("kpi_pct_huerfano", 860, 400, "Costo Huerfano", "Costo sin atribuir"),
            _visual("bar_cat", "barChart", (20, 150, 620, 270), "Costo bruto por categoría de recurso",
                    {"Category": [(C, "ResourceCategory")], "Y": [(M, "Costo Bruto")]}, sort=(M, "Costo Bruto")),
            _visual("bar_zombie_env", "barChart", (660, 150, 600, 270), "Candidatos zombie por entorno",
                    {"Category": [(C, "Environment")], "Y": [(M, "Costo Candidatos Zombie")]},
                    sort=(M, "Costo Candidatos Zombie")),
            _visual("tbl_zombie", "tableEx", (20, 440, 1240, 260), "Recursos candidatos a eliminar (sin owner/tags)",
                    {"Values": [(C, "Project"), (C, "SKU"), (C, "Environment"), (M, "Costo Candidatos Zombie")]},
                    sort=(M, "Costo Candidatos Zombie")),
        ],
    }
    rightsizing = {
        "name": "rightsizing",
        "displayName": "Rightsizing",
        "visuals": [
            _card("kpi_computo", 20, 400, "Costo Computo", "Costo de cómputo"),
            _card("kpi_ahorro", 440, 400, "Ahorro Rightsizing Est",
                  f"Ahorro estimado ({int(RIGHTSIZING_RATE * 100)}% del cómputo)"),
            _card("kpi_pct_computo", 860, 400, "Pct Computo", "% del gasto en cómputo"),
            _visual("bar_sku", "barChart", (20, 150, 620, 270), "Top SKUs de cómputo por costo",
                    {"Category": [(C, "SKU")], "Y": [(M, "Costo Computo")]}, sort=(M, "Costo Computo")),
            _visual("bar_ahorro_env", "barChart", (660, 150, 600, 270), "Ahorro estimado por entorno",
                    {"Category": [(C, "Environment")], "Y": [(M, "Ahorro Rightsizing Est")]},
                    sort=(M, "Ahorro Rightsizing Est")),
            _visual("tbl_rightsizing", "tableEx", (20, 440, 1240, 260), "Candidatos a rightsizing (validar con CPU/RAM)",
                    {"Values": [(C, "Project"), (C, "SKU"), (C, "Environment"),
                                (M, "Costo Computo"), (M, "Ahorro Rightsizing Est")]},
                    sort=(M, "Costo Computo")),
        ],
    }
    return [resumen, zombies, rightsizing]


def _part(path: str, obj: dict) -> dict:
    payload = base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return {"path": path, "payload": payload, "payloadType": "InlineBase64"}


def build_report_parts(dataset_id: str) -> list[dict]:
    pages = _pages()
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
                "pageOrder": [p["name"] for p in pages],
                "activePageName": pages[0]["name"],
            },
        ),
    ]
    for p in pages:
        parts.append(
            _part(
                f"definition/pages/{p['name']}/page.json",
                {
                    "$schema": _SCHEMA_BASE + "page/1.0.0/schema.json",
                    "name": p["name"],
                    "displayName": p["displayName"],
                    "displayOption": "FitToPage",
                    "height": 720,
                    "width": 1280,
                },
            )
        )
        for v in p["visuals"]:
            parts.append(_part(f"definition/pages/{p['name']}/visuals/{v['name']}/visual.json", v))
    return parts
