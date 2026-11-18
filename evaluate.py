"""Measure exact field extraction on the two included synthetic scans."""
import json
import time

from app import ROOT, ocr, parse_invoice

for language, invoice in (("en", "INV-204"), ("ar", "205")):
    start = time.perf_counter()
    sample = (ROOT / "samples" / f"invoice-{language}.png").read_bytes()
    actual = parse_invoice(ocr(sample, language))
    expected = {"invoice_number": invoice, "subtotal": "120.00", "tax": "6.00", "total": "126.00"}
    print(json.dumps({"language": language, "correct_fields": sum(actual[k] == v for k, v in expected.items()),
                      "field_count": len(expected), "ocr_and_parse_ms": round((time.perf_counter()-start)*1000, 1),
                      "actual": actual}))
