import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app, check_fields, parse_invoice


class IntakeTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"INTAKE_DB": folder + "/test.sqlite3"}))
        self.client = self.enterContext(TestClient(app))

    def test_arabic_indic_digits_and_decimal_validation(self):
        fields = parse_invoice("رقم الفاتورة: ٢٠٥\nالمجموع الفرعي: ١٢٠٫٠٠\nالضريبة: ٦٫٠٠\nالإجمالي: ١٢٦٫٠٠")
        self.assertEqual(fields["invoice_number"], "205")
        self.assertIsNone(check_fields(fields))
        self.assertEqual(check_fields({**fields, "total": "125"}), "Subtotal plus tax must equal total.")

    def test_conflicting_labels_leave_field_for_review(self):
        self.assertIsNone(parse_invoice("Total: 100\nTotal: 200")["total"])

    def test_image_intake_ocr_review_and_retry(self):
        image = self.client.get("/samples/en").content
        response = self.client.post("/api/documents", files={"image": ("example.png", image)})
        self.assertEqual(response.status_code, 202)
        job = response.json()["id"]
        path = f"/api/documents/{job}"
        for _ in range(100):
            result = self.client.get(path).json()
            if result["status"] in ("review", "failed"):
                break
            time.sleep(.1)
        self.assertEqual(result["status"], "review")
        self.assertIsNone(result["error"])
        fields = result["fields"]
        self.assertEqual(fields["invoice_number"], "INV-204")
        self.assertEqual(self.client.get("/api/documents").json()[0]["id"], job)
        self.assertEqual(self.client.put(path+"/review", json={**fields, "total":"125"}).status_code, 422)
        for _ in range(2):
            self.assertEqual(self.client.put(path+"/review", json=fields).json(), {"status": "approved"})
        self.assertEqual(self.client.get("/api/documents").json(), [])

    def test_reject_non_image(self):
        result = self.client.post("/api/documents", files={"image": ("fake.png", b"fake")})
        self.assertEqual(result.status_code, 422)
