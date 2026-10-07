import base64
import json
import re
import unittest

import powerbi_report as pr


class PowerBIReportTests(unittest.TestCase):
    def setUp(self):
        self.schema = pr.build_dataset_schema("X")
        self.table = self.schema["tables"][0]
        self.columns = {c["name"] for c in self.table["columns"]}
        self.measures = {m["name"] for m in self.table["measures"]}

    def test_classify_resource(self):
        self.assertEqual(pr.classify_resource("Compute Engine", "N2 Instance Core running"), "Cómputo")
        self.assertEqual(pr.classify_resource("Compute Engine", "Storage PD Capacity"), "Discos y Snapshots")
        self.assertEqual(pr.classify_resource("Compute Engine", "Static IP Charge"), "IPs reservadas")
        self.assertEqual(pr.classify_resource("Cloud Storage", "Standard Storage"), "Almacenamiento")
        self.assertEqual(pr.classify_resource("BigQuery", "Analysis"), "Datos y BD")

    def test_measures_reference_existing_columns(self):
        for m in self.table["measures"]:
            for col in re.findall(r"FinOpsBilling\[(\w+)\]", m["expression"]):
                self.assertIn(col, self.columns, m["name"])

    def test_visuals_reference_existing_fields(self):
        parts = pr.build_report_parts("ds-1")
        visuals = [json.loads(base64.b64decode(p["payload"])) for p in parts if p["path"].endswith("visual.json")]
        self.assertGreater(len(visuals), 10)
        for v in visuals:
            for state in v["visual"]["query"]["queryState"].values():
                for proj in state["projections"]:
                    f = proj["field"]
                    kind, body = next(iter(f.items()))
                    allowed = self.measures if kind == "Measure" else self.columns
                    self.assertIn(body["Property"], allowed, v["name"])

    def test_report_binds_dataset_and_pages(self):
        parts = {p["path"]: p for p in pr.build_report_parts("ds-1")}
        pbir = json.loads(base64.b64decode(parts["definition.pbir"]["payload"]))
        self.assertEqual(pbir["datasetReference"]["byConnection"]["pbiModelDatabaseName"], "ds-1")
        pages = json.loads(base64.b64decode(parts["definition/pages/pages.json"]["payload"]))
        self.assertEqual(len(pages["pageOrder"]), 3)


if __name__ == "__main__":
    unittest.main()
