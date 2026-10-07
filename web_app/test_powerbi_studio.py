import base64
import json
import unittest

import powerbi_studio as ps


def decode(parts):
    return {p["path"]: json.loads(base64.b64decode(p["payload"])) for p in parts}


class StudioTests(unittest.TestCase):
    def test_validate_drops_invalid_and_clamps(self):
        spec = ps.validate_spec({
            "report_name": "Mi <reporte>!", "days": 9999, "clouds": ["gcp", "oracle"],
            "pages": [{"title": "P", "visuals": [
                {"type": "card", "measure": "Costo Bruto"},
                {"type": "card", "measure": "Inventada"},
                {"type": "bar", "dimension": "Service", "measure": "Costo Bruto"},
                {"type": "pie3d", "dimension": "Service", "measure": "Costo Bruto"},
                {"type": "line", "dimension": "Service", "measure": "Costo Bruto"},
            ]}],
        })
        self.assertEqual(spec["days"], 365)
        self.assertEqual(spec["clouds"], ["gcp"])
        self.assertNotIn("<", spec["report_name"])
        types = [v["type"] for v in spec["pages"][0]["visuals"]]
        self.assertEqual(types, ["card", "bar", "column"])

    def test_validate_rejects_empty(self):
        with self.assertRaises(ps.SpecError):
            ps.validate_spec({"pages": [{"title": "x", "visuals": [{"type": "card", "measure": "nope"}]}]})
        with self.assertRaises(ps.SpecError):
            ps.validate_spec("texto")

    def test_default_spec_is_valid_and_fields_exist(self):
        spec = ps.validate_spec(ps.default_spec())
        schema = ps.build_dataset_schema("D")
        cols = {c["name"] for c in schema["tables"][0]["columns"]}
        measures = {m["name"] for m in schema["tables"][0]["measures"]}
        files = decode(ps.build_report_parts("ds-1", spec))
        n = 0
        for path, obj in files.items():
            if path.endswith("visual.json"):
                n += 1
                for state in obj["visual"]["query"]["queryState"].values():
                    for proj in state["projections"]:
                        kind, body = next(iter(proj["field"].items()))
                        self.assertIn(body["Property"], measures if kind == "Measure" else cols)
        self.assertEqual(n, 7)

    def test_layout_fits_and_no_overlap_of_page_height(self):
        visuals = [{"type": "card"}] * 5 + [{"type": "bar"}] * 3 + [{"type": "table"}]
        placed, height = ps.layout_page(visuals)
        self.assertEqual(len(placed), len(visuals))
        for _, (x, y, w, h) in placed:
            self.assertLessEqual(x + w, 1280)
            self.assertLessEqual(y + h, height)

    def test_measures_reference_existing_columns(self):
        import re
        schema = ps.build_dataset_schema("D")
        cols = {c["name"] for c in schema["tables"][0]["columns"]}
        for m in schema["tables"][0]["measures"]:
            for col in re.findall(rf"{ps.TABLE}\[(\w+)\]", m["expression"]):
                self.assertIn(col, cols)

    def _rec(self, key, value, cost, rg="rg1"):
        return {"UsageDate": 20260901, "ServiceName": "VM", "ResourceGroupName": rg,
                "TagKey": key, "TagValue": value, "Cost": cost}

    def test_azure_tags_single_values(self):
        rows = ps.merge_azure_tag_records([self._rec("env", "prod", 10), self._rec("businessowner", "ana", 10)])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["Environment"], rows[0]["BusinessOwner"], rows[0]["TaggingStatus"]), ("prod", "ana", ps.TAGGED))
        self.assertEqual(rows[0]["Cost"], 10)

    def test_azure_tags_untagged_and_cost_preserved(self):
        rows = ps.merge_azure_tag_records([
            self._rec("env", "prod", 6), self._rec("env", None, 4), self._rec("businessowner", "ana", 10),
        ])
        self.assertAlmostEqual(sum(r["Cost"] for r in rows), 10)
        by_env = {r["Environment"]: r for r in rows}
        self.assertEqual(by_env["prod"]["TaggingStatus"], ps.TAGGED)
        self.assertEqual(by_env["Sin Asignar"]["TaggingStatus"], ps.ORPHAN)

    def test_azure_tags_both_missing_and_multiple(self):
        rows = ps.merge_azure_tag_records([self._rec("env", None, 3), self._rec("businessowner", None, 3)])
        self.assertEqual((rows[0]["Environment"], rows[0]["BusinessOwner"], rows[0]["TaggingStatus"]), ("Sin Asignar", "Sin Asignar", ps.ORPHAN))
        rows = ps.merge_azure_tag_records([
            self._rec("env", "prod", 5), self._rec("env", "dev", 5),
            self._rec("businessowner", "a", 6), self._rec("businessowner", "b", 4),
        ])
        self.assertAlmostEqual(sum(r["Cost"] for r in rows), 10)
        self.assertTrue(all(r["BusinessOwner"] == "Múltiples" for r in rows))


if __name__ == "__main__":
    unittest.main()
