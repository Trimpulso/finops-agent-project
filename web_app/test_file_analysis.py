import io
import json
import unittest
from unittest.mock import patch

from file_analysis import analyze_files, prepare_file_parts


class Uploaded:
    def __init__(self, name, data):
        self.name, self.data = name, data

    def getvalue(self):
        return self.data


class FileAnalysisTests(unittest.TestCase):
    def test_csv_sample_and_stats(self):
        parts = prepare_file_parts([Uploaded("costos.csv", b"servicio,costo\nVM,12\nDB,30\n")])
        text = parts[0]["text"]
        self.assertIn("2 filas", text)
        self.assertIn("VM,12", text)
        self.assertIn("No infieras totales exactos", text)

    def test_image_encoding(self):
        parts = prepare_file_parts([Uploaded("captura.png", b"\x89PNG")])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(parts[1]["inline_data"]["data"], "iVBORw==")

    def test_json_validation_and_size_limit(self):
        with self.assertRaisesRegex(ValueError, "JSON válido"):
            prepare_file_parts([Uploaded("mal.json", b"{")])
        with self.assertRaisesRegex(ValueError, "10 MB"):
            prepare_file_parts([Uploaded("grande.pdf", b"x" * (10 * 1024 * 1024 + 1))])

    def test_model_fallback_and_no_key_in_payload(self):
        class Response:
            def __init__(self, status, body=None):
                self.status_code = status
                self.body = body

            def raise_for_status(self):
                pass

            def json(self):
                return self.body

        fake = [
            Response(404),
            Response(200, {"candidates": [{"content": {"parts": [{"text": "Hallazgo FinOps"}]}}]}),
        ]
        with patch("file_analysis.requests.post", side_effect=fake) as post:
            answer, model = analyze_files("Revisa", [Uploaded("costos.csv", b"costo\n12\n")],
                                          "private-key", ["fallback", "working"], "Eres FinOps.")
        self.assertEqual(answer, "Hallazgo FinOps")
        self.assertEqual(model, "working (archivo)")
        self.assertEqual(post.call_count, 2)
        self.assertNotIn("private-key", json.dumps(post.call_args.kwargs["json"]))

    def test_excel(self):
        import pandas as pd
        output = io.BytesIO()
        pd.DataFrame({"servicio": ["EC2"], "costo": [5]}).to_excel(output, index=False)
        self.assertIn("EC2", prepare_file_parts([Uploaded("costos.xlsx", output.getvalue())])[0]["text"])


if __name__ == "__main__":
    unittest.main()
