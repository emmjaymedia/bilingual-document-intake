"""Durable image intake with local OCR and explicit invoice review."""
import asyncio
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager, closing, suppress
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, StringConstraints

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("uvicorn.error")
MAX_BYTES = 5 * 1024 * 1024
LANGUAGES = {"en": "eng", "ar": "ara+eng"}
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
Id = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9-]{1,40}$")]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class Invoice(BaseModel):
    invoice_number: Id
    subtotal: Money
    tax: Money
    total: Money


def database():
    conn = sqlite3.connect(os.getenv("INTAKE_DB", str(ROOT / "intake.sqlite3")), timeout=8)
    conn.row_factory = sqlite3.Row
    return closing(conn)


def parse_invoice(text):
    normalized = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text.translate(DIGITS))
    labels = {
        "invoice_number": r"invoice\s*(?:no\.?|number)|رقم\s*الفاتورة",
        "subtotal": r"subtotal|المجموع\s*الفرعي|المبلغ\s*قبل\s*الضريبة",
        "tax": r"tax|vat|الضريبة",
        "total": r"total|الإجمالي|الاجمالي",
    }
    fields = {}
    for field, label in labels.items():
        matches = []
        value = r"[A-Za-z0-9][A-Za-z0-9-]*" if field == "invoice_number" else r"[0-9][0-9,]*(?:\.[0-9]{1,2})?"
        for line in normalized.splitlines():
            forward = re.fullmatch(rf"\s*(?:{label})\s*[:#-]?\s*({value})\s*", line, re.I)
            reverse = re.fullmatch(rf"\s*({value})\s*[:#-]?\s*(?:{label})\s*", line, re.I)
            match = forward or reverse
            if match:
                matches.append(match[1].replace(",", ""))
        fields[field] = matches[0] if len(set(matches)) == 1 else None
    return fields


def check_fields(fields):
    try:
        invoice = Invoice.model_validate(fields)
    except ValueError:
        return "Check missing or invalid fields."
    if invoice.subtotal + invoice.tax != invoice.total:
        return "Subtotal plus tax must equal total."
    return None


def normalize_image(data):
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ("PNG", "JPEG") or source.width * source.height > 8_000_000:
                raise ValueError("Use a PNG or JPEG with at most eight megapixels.")
            with io.BytesIO() as cleaned:
                source.convert("RGB").save(cleaned, format="PNG")
                return cleaned.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError("Could not read this image.") from None


def ocr(data, language):
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", LANGUAGES[language], "--psm", "6"],
        input=data, capture_output=True, timeout=20, check=True,
    )
    return result.stdout.decode("utf-8")


def process_one():
    now = time.time()
    with database() as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""UPDATE jobs SET status='failed', error='OCR retry limit reached.', image=NULL
                        WHERE status='processing' AND lease < ? AND attempts >= 2""", (now,))
        job = conn.execute("""SELECT * FROM jobs WHERE status='queued' OR
            (status='processing' AND lease < ? AND attempts < 2) ORDER BY rowid LIMIT 1""", (now,)).fetchone()
        if not job:
            return False
        lease = now + 45
        conn.execute("UPDATE jobs SET status='processing',lease=?,attempts=attempts+1 WHERE id=?",
                     (lease, job["id"]))
    start = time.perf_counter()
    try:
        extracted = ocr(job["image"], job["language"])
        fields = parse_invoice(extracted)
        state, error = "review", check_fields(fields)
        fields_json = json.dumps(fields, ensure_ascii=False)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        state = "queued" if isinstance(exc, subprocess.TimeoutExpired) and job["attempts"] == 0 else "failed"
        error = "OCR could not process the image. Check that the selected language is installed."
        fields_json = extracted = None
    with database() as conn, conn:
        conn.execute("""UPDATE jobs SET status=?,error=?,fields=?,ocr_text=?,
            image=CASE WHEN ?='queued' THEN image ELSE NULL END WHERE id=? AND lease=?""",
            (state, error, fields_json, extracted, state, job["id"], lease))
    LOG.info("intake language=%s state=%s ocr_ms=%.1f", job["language"], state,
             (time.perf_counter()-start)*1000)
    return True


async def worker():
    while True:
        try:
            if not await asyncio.to_thread(process_one):
                await asyncio.sleep(.5)
        except sqlite3.Error:
            LOG.exception("intake_queue_unavailable")
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    with database() as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, language TEXT NOT NULL, image BLOB,
          status TEXT NOT NULL, lease REAL, attempts INTEGER NOT NULL DEFAULT 0,
          error TEXT, fields TEXT, ocr_text TEXT
        )""")
    task = asyncio.create_task(worker())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Bilingual Document Intake", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(ROOT / "static/index.html")


@app.get("/samples/{language}")
def sample(language: Literal["en", "ar"]):
    return FileResponse(ROOT / "samples" / f"invoice-{language}.png", media_type="image/png")


@app.post("/api/documents", status_code=202)
def upload(image: Annotated[UploadFile, File()], language: Literal["en", "ar"] = "en"):
    raw = image.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "Images must be 5 MB or smaller.")
    try:
        data = normalize_image(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    job_id = str(uuid4())
    with database() as conn, conn:
        conn.execute("INSERT INTO jobs (id,language,image,status) VALUES (?,?,?,'queued')",
                     (job_id, language, data))
    return {"id": job_id, "status": "queued"}


@app.get("/api/documents/{job_id}")
def status(job_id: UUID):
    with database() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (str(job_id),)).fetchone()
    if not job:
        raise HTTPException(404, "Document not found.")
    return {"id": job["id"], "status": job["status"], "error": job["error"],
            "fields": json.loads(job["fields"]) if job["fields"] else None, "ocr_text": job["ocr_text"]}


@app.put("/api/documents/{job_id}/review")
def review(job_id: UUID, invoice: Invoice):
    message = check_fields(invoice.model_dump())
    if message:
        raise HTTPException(422, message)
    with database() as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT status,fields FROM jobs WHERE id=?", (str(job_id),)).fetchone()
        if not job:
            raise HTTPException(404, "Document not found.")
        if job["status"] == "approved" and Invoice.model_validate_json(job["fields"]) == invoice:
            return {"status": "approved"}
        if job["status"] != "review":
            raise HTTPException(409, "This document is unavailable for review.")
        conn.execute("UPDATE jobs SET status='approved',fields=?,error=NULL WHERE id=?",
                     (invoice.model_dump_json(), str(job_id)))
    return {"status": "approved"}


@app.get("/api/documents")
def queue():
    with database() as conn:
        jobs = conn.execute("""SELECT id,language,status FROM jobs
            WHERE status IN ('queued','processing','review') ORDER BY rowid DESC LIMIT 50""").fetchall()
    return [dict(job) for job in jobs]
