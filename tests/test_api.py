"""
Integration tests for FastAPI endpoints.
All Claude calls are mocked so no real API key is required.
"""

import io
import json
import os
import time

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

# Prevent RapidAPI check in tests
os.environ.setdefault("RAPIDAPI_PROXY_SECRET", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.main import app  # noqa: E402

VALID_GSTIN_SELLER = "27AABCU9603R1ZX"
VALID_GSTIN_BUYER = "29AABCU9603R1ZY"

MINIMAL_INVOICE_DICT = {
    "invoice_number": "INV-001",
    "invoice_date": "2024-03-15",
    "invoice_type": "B2B",
    "seller": {"name": "Acme Pvt Ltd", "gstin": VALID_GSTIN_SELLER, "address": "Pune", "state_code": "27"},
    "buyer": {"name": "Buyer Corp", "gstin": VALID_GSTIN_BUYER, "address": "Bengaluru", "state_code": "29"},
    "line_items": [
        {
            "description": "Office Chair",
            "hsn_sac_code": "9401",
            "quantity": 10,
            "unit": "NOS",
            "unit_price": 2500.0,
            "taxable_amount": 25000.0,
            "cgst_rate": 9,
            "sgst_rate": 9,
            "igst_rate": 0,
            "cgst_amount": 2250.0,
            "sgst_amount": 2250.0,
            "igst_amount": 0,
            "total_amount": 29500.0,
        }
    ],
    "tax_summary": {
        "subtotal": 25000.0,
        "total_cgst": 2250.0,
        "total_sgst": 2250.0,
        "total_igst": 0,
        "total_cess": 0,
        "round_off": 0,
        "grand_total": 29500.0,
        "amount_in_words": "Twenty Nine Thousand Five Hundred Rupees Only",
    },
    "payment": {"bank_name": "HDFC Bank", "account_number": "XXXX1234", "ifsc_code": "HDFC0001234", "due_date": "2024-04-15"},
    "meta": {"confidence_score": 0.97, "extraction_time_ms": None, "pages_processed": None, "currency": "INR"},
}

MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type /Pages /Kids [3 0 R] /Count 1>>endobj\n"
    b"3 0 obj<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"trailer<</Size 4 /Root 1 0 R>>\nstartxref\n190\n%%EOF"
)


@pytest.fixture
def client():
    return TestClient(app)


def _mock_parse_ok(monkeypatch, invoice_override: dict | None = None):
    invoice = {**MINIMAL_INVOICE_DICT, **(invoice_override or {})}

    def mock_parse(filename, content_type, file_bytes, language="en"):
        from app.models import InvoiceData
        meta = dict(invoice.get("meta") or {})
        meta["extraction_time_ms"] = 300
        meta["pages_processed"] = 1
        data = {**invoice, "meta": meta}
        return InvoiceData(**data).model_dump()

    monkeypatch.setattr("app.main.parse_invoice", mock_parse)


# ---------------------------------------------------------------------------
# TC-015 — Health check
# ---------------------------------------------------------------------------

def test_TC_015_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# TC-001 — Standard B2B invoice
# ---------------------------------------------------------------------------

def test_TC_001_standard_b2b(client, monkeypatch):
    _mock_parse_ok(monkeypatch)
    response = client.post(
        "/parse",
        files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")},
        data={"language": "en"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert len(data["seller"]["gstin"]) == 15
    assert len(data["buyer"]["gstin"]) == 15
    assert len(data["line_items"]) >= 1
    assert data["tax_summary"]["grand_total"] > 0
    assert data["meta"]["confidence_score"] >= 0.85


# ---------------------------------------------------------------------------
# TC-002 — B2C invoice (buyer has no GSTIN)
# ---------------------------------------------------------------------------

def test_TC_002_b2c_no_buyer_gstin(client, monkeypatch):
    _mock_parse_ok(
        monkeypatch,
        {
            "invoice_type": "B2C",
            "buyer": {"name": "Consumer", "gstin": None, "address": "Delhi", "state_code": "07"},
        },
    )
    response = client.post(
        "/parse",
        files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["invoice_type"] == "B2C"
    assert body["data"]["buyer"]["gstin"] is None
    assert body["data"]["seller"]["gstin"] is not None


# ---------------------------------------------------------------------------
# TC-003 — Multi-page invoice
# ---------------------------------------------------------------------------

def test_TC_003_multipage(client, monkeypatch):
    invoice = dict(MINIMAL_INVOICE_DICT)
    # Add 15 line items
    invoice["line_items"] = [
        {**MINIMAL_INVOICE_DICT["line_items"][0], "description": f"Item {i}"}
        for i in range(15)
    ]

    def mock_parse(filename, content_type, file_bytes, language="en"):
        from app.models import InvoiceData
        meta = {"confidence_score": 0.95, "extraction_time_ms": 300, "pages_processed": 3, "currency": "INR"}
        data = {**invoice, "meta": meta}
        return InvoiceData(**data).model_dump()

    monkeypatch.setattr("app.main.parse_invoice", mock_parse)

    response = client.post(
        "/parse",
        files={"file": ("multipage.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["data"]["line_items"]) >= 10
    assert body["data"]["meta"]["pages_processed"] == 3


# ---------------------------------------------------------------------------
# TC-005 — IGST-only invoice
# ---------------------------------------------------------------------------

def test_TC_005_igst_interstate(client, monkeypatch):
    igst_item = {
        "description": "Laptop",
        "hsn_sac_code": "8471",
        "quantity": 1,
        "unit": "NOS",
        "unit_price": 50000.0,
        "taxable_amount": 50000.0,
        "cgst_rate": 0,
        "sgst_rate": 0,
        "igst_rate": 18,
        "cgst_amount": 0,
        "sgst_amount": 0,
        "igst_amount": 9000.0,
        "total_amount": 59000.0,
    }
    _mock_parse_ok(
        monkeypatch,
        {
            "line_items": [igst_item],
            "tax_summary": {**MINIMAL_INVOICE_DICT["tax_summary"], "total_cgst": 0, "total_sgst": 0, "total_igst": 9000.0},
        },
    )
    response = client.post(
        "/parse",
        files={"file": ("igst.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["line_items"][0]["igst_rate"] > 0
    assert data["line_items"][0]["cgst_rate"] == 0
    assert data["tax_summary"]["total_igst"] > 0
    assert data["tax_summary"]["total_cgst"] == 0


# ---------------------------------------------------------------------------
# TC-007 — Service invoice (SAC code starts with "99")
# ---------------------------------------------------------------------------

def test_TC_007_service_sac_code(client, monkeypatch):
    sac_item = {**MINIMAL_INVOICE_DICT["line_items"][0], "hsn_sac_code": "998314", "unit": "NOS"}
    _mock_parse_ok(monkeypatch, {"line_items": [sac_item]})
    response = client.post(
        "/parse",
        files={"file": ("service.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 200
    item = response.json()["data"]["line_items"][0]
    assert item["hsn_sac_code"].startswith("99")


# ---------------------------------------------------------------------------
# TC-008 — Not an invoice
# ---------------------------------------------------------------------------

def test_TC_008_not_an_invoice(client, monkeypatch):
    def mock_parse(filename, content_type, file_bytes, language="en"):
        raise ValueError("INVALID_FORMAT|File does not appear to be a GST invoice|Claude could not identify fields.")

    monkeypatch.setattr("app.main.parse_invoice", mock_parse)

    response = client.post(
        "/parse",
        files={"file": ("photo.png", b"fake png bytes", "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_FORMAT"


# ---------------------------------------------------------------------------
# TC-009 — File too large (PDF > 10MB)
# ---------------------------------------------------------------------------

def test_TC_009_pdf_too_large(client):
    big_pdf = b"x" * (11 * 1024 * 1024)
    response = client.post(
        "/parse",
        files={"file": ("big.pdf", big_pdf, "application/pdf")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FILE_TOO_LARGE"


# ---------------------------------------------------------------------------
# TC-010 — Password-protected PDF
# ---------------------------------------------------------------------------

def test_TC_010_password_protected_pdf(client, monkeypatch):
    def mock_parse(filename, content_type, file_bytes, language="en"):
        raise ValueError("INVALID_FORMAT|PDF is password protected|Please provide an unprotected PDF.")

    monkeypatch.setattr("app.main.parse_invoice", mock_parse)

    response = client.post(
        "/parse",
        files={"file": ("protected.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_FORMAT"
    assert "password" in body["error"]["message"].lower()


# ---------------------------------------------------------------------------
# TC-011 — Empty file upload
# ---------------------------------------------------------------------------

def test_TC_011_empty_file(client):
    response = client.post(
        "/parse",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_FORMAT"


# ---------------------------------------------------------------------------
# TC-012 — Unsupported file type (Excel)
# ---------------------------------------------------------------------------

def test_TC_012_unsupported_file_type(client):
    response = client.post(
        "/parse",
        files={"file": ("invoice.xlsx", b"PK fake xlsx", "application/vnd.ms-excel")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNSUPPORTED_TYPE"


# ---------------------------------------------------------------------------
# TC-013 — Invoice with no GSTIN
# ---------------------------------------------------------------------------

def test_TC_013_missing_gstin(client, monkeypatch):
    def mock_parse(filename, content_type, file_bytes, language="en"):
        raise ValueError("MISSING_GSTIN|No GSTIN found — not a valid GST invoice|A valid GST invoice must contain at least a seller GSTIN.")

    monkeypatch.setattr("app.main.parse_invoice", mock_parse)

    response = client.post(
        "/parse",
        files={"file": ("no_gstin.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "MISSING_GSTIN"


# ---------------------------------------------------------------------------
# TC-014 — Wrong / missing RapidAPI proxy secret
# ---------------------------------------------------------------------------

def test_TC_014_missing_rapidapi_secret(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "correct-secret")
    # Reload main so it picks up the new env var
    import importlib
    import app.main as main_module
    importlib.reload(main_module)

    test_client = TestClient(main_module.app)
    response = test_client.post(
        "/parse",
        files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")},
        # No X-RapidAPI-Proxy-Secret header
    )
    assert response.status_code == 403

    # Restore
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "")
    importlib.reload(main_module)


def test_TC_014_wrong_rapidapi_secret(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "correct-secret")
    import importlib
    import app.main as main_module
    importlib.reload(main_module)

    test_client = TestClient(main_module.app)
    response = test_client.post(
        "/parse",
        files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"X-RapidAPI-Proxy-Secret": "wrong-secret"},
    )
    assert response.status_code == 403

    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "")
    importlib.reload(main_module)


# ---------------------------------------------------------------------------
# TC-016 — Concurrent requests (performance smoke test)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_TC_016_concurrent_requests(monkeypatch):
    """TC-016: 10 concurrent requests should all succeed within 15s."""
    import asyncio

    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "")

    async def mock_parse_async(filename, content_type, file_bytes, language="en"):
        from app.models import InvoiceData
        meta = {"confidence_score": 0.97, "extraction_time_ms": 300, "pages_processed": 1, "currency": "INR"}
        data = {**MINIMAL_INVOICE_DICT, "meta": meta}
        return InvoiceData(**data).model_dump()

    # Patch synchronously in parse_invoice but the endpoint is sync too
    import app.main as main_module

    def mock_parse_sync(filename, content_type, file_bytes, language="en"):
        from app.models import InvoiceData
        meta = {"confidence_score": 0.97, "extraction_time_ms": 300, "pages_processed": 1, "currency": "INR"}
        data = {**MINIMAL_INVOICE_DICT, "meta": meta}
        return InvoiceData(**data).model_dump()

    monkeypatch.setattr("app.main.parse_invoice", mock_parse_sync)

    start = time.time()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        tasks = [
            ac.post(
                "/parse",
                files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")},
            )
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)

    elapsed = time.time() - start
    assert elapsed < 15, f"10 concurrent requests took {elapsed:.1f}s, expected < 15s"
    for r in responses:
        assert r.status_code == 200, f"Got status {r.status_code}"
        assert r.json()["success"] is True
