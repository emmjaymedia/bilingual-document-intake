# Bilingual Document Intake

An English and Arabic invoice review workspace. Upload a synthetic PNG or JPEG, inspect the OCR output, correct the extracted fields, and approve a record when the totals balance.

## Run

Install Tesseract 5 with both English and Arabic language packs. On Debian or Ubuntu these packages are `tesseract-ocr`, `tesseract-ocr-eng` and `tesseract-ocr-ara`. Confirm `tesseract --list-langs` includes `eng` and `ara`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --port 8002
```

Open http://127.0.0.1:8002. Choose a language and click **Try sample** to run OCR against one of the included images. **Review queue** shows pending jobs that can be reopened. Swagger UI is at `/docs`.

## Processing and safeguards

- Intake validates file size and format, re-encodes the image to strip metadata, and queues the normalized image in SQLite. Set `INTAKE_DB` for a persistent storage path.
- The worker claims a job under a transaction, processes it outside the transaction, and records results against its lease. An expired lease can be reclaimed after a restart, with a bounded number of attempts.
- Tesseract runs locally. Parsing recognizes bilingual labels and normalizes Arabic-Indic digits. Missing or conflicting values remain editable. Approval always requires human review and verifies subtotal plus tax using decimal arithmetic.
- The worker discards image data after OCR completes or permanently fails. Parsed fields and OCR text stay in SQLite. This local demo uses synthetic documents and a shared queue. A deployment with real invoices would require authenticated access, per-tenant queues, and a retention policy.

## Evaluation

```bash
python -m unittest -v
python evaluate.py
```

The recorded run extracted all four expected fields from each of the included scans. See [evaluation.jsonl](evaluation.jsonl) for measured OCR and parsing latency and each extracted value. Two clean synthetic scans establish that both language paths run. Production accuracy, review correction rate, and processing costs require a larger representative sample. The current flow uses local OCR with zero per-request provider charges.
