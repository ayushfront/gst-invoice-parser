"""
Integration tests for FastAPI endpoints.
All Gemini API calls are mocked — tests run completely offline.
Covers: TC-001 to TC-016 (all 17 test cases including TC-017 in test_parser.py).
"""
import asyncio
import copy
import time
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from tests.conftest import MINIMAL_INVOICE, MINIMAL_PDF, VALID_GSTIN_BUYER, VALID_GSTIN_SELLER


# ---------------------------------------------------------------------------
# Import app AFTER conftest has set env vars
# ---------------------------------------------------------------------------
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def _make_success_parser(invoice_override=None, page_count=1):
    """Return a mock parse_invoice function that yields a valid invoice."""
    import copy
    from app.models import InvoiceData

    def _parse(filename, content_type, file_bytes, language="en"):
        inv = copy.deepcopy(MINIMAL_INVOICE)
        if invoice_override:
            for k, v in invoice_override.items():
                if isinstance(v, dict) and isinstance(inv.get(k), dict):
                    inv[k] = {**inv[k], **v}
                else:
                    inv[k] = v
        inv["meta"]["extraction_time_ms"] = 300
        inv["meta"]["pages_processed"] = page_count
        return InvoiceData(**inv).model_dump()

    return _parse


# ===========================================================================
# TC-015 — Health check
# ===========================================================================

def test_TC015_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"


# ===========================================================================
# TC-001 — Standard B2B invoice PDF
# ===========================================================================

def test_TC001_standard_b2b(client, monkeypatch):
    monkeypatch.setattr("app.main.parse_invoice", _make_success_parser())
    resp = client.post(
        "/parse",
        files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")},
        data={"language": "en"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    d = data["data"]
    assert len(d["seller"]["gstin"]) == 15
    assert len(d["buyer"]["gstin"]) == 15
    assert len(d["line_items"]) >= 1
    assert d["tax_summary"]["grand_total"] > 0
    assert d["meta"]["confidence_score"] >= 0.85


# ===========================================================================
# TC-002 — B2C invoice (buyer has no GSTIN)
# ===========================================================================

def test_TC002_b2c_no_buyer_gstin(client, monkeypatch):
    monkeypatch.setattr("app.main.parse_invoice", _make_success_parser(
        invoice_override={
            "invoice_type": "B2C",
            "buyer": {"name": "Consumer", "gstin": None, "address": "Delhi", "state_code": "07"},
        }
    ))
    resp = client.post("/parse", files={"file": ("b2c.pdf", MINIMAL_PDF, "application/pdf")})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["invoice_type"] == "B2C"
    assert d["buyer"]["gstin"] is None
    assert d["seller"]["gstin"] is not None


# ===========================================================================
# TC-003 — Multi-page invoice PDF
# ===========================================================================

def test_TC003_multipage(client, monkeypatch):
    items = [copy.deepcopy(MINIMAL_INVOICE["line_items"][0]) for _ in range(15)]
    for i, it in enumerate(items):
        it["description"] = f"Item {i}"
    monkeypatch.setattr("app.main.parse_invoice", _make_success_parser(
        invoice_override={"line_items": items},
        page_count=3,
    ))
    resp = client.post("/parse", files={"file": ("multi.pdf", MINIMAL_PDF, "application/pdf")})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert len(d["line_items"]) >= 10
    assert d["meta"]["pages_processed"] == 3


# ===========================================================================
# TC-004 — Invoice as JPG image
# ===========================================================================

def test_TC004_jpg_image_invoice(client, monkeypatch):
    monkeypatch.setattr("app.main.parse_invoice", _make_success_parser())
    resp = client.post("/parse", files={"file": ("scan.jpg", b"fake-jpg-bytes", "image/jpeg")})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["meta"]["confidence_score"] >= 0.75


# ===========================================================================
# TC-005 — IGST-only invoice (interstate)
# ===========================================================================

def test_TC005_igst_interstate(client, monkeypatch):
    igst_item = {**copy.deepcopy(MINIMAL_INVOICE["line_items"][0]),
                 "cgst_rate": 0.0, "sgst_rate": 0.0, "igst_rate": 18.0,
                 "cgst_amount": 0.0, "sgst_amount": 0.0, "igst_amount": 9000.0}
    tax = {**MINIMAL_INVOICE["tax_summary"],
           "total_cgst": 0.0, "total_sgst": 0.0, "total_igst": 9000.0}
    monkeypatch.setattr("app.main.parse_invoice", _make_success_parser(
        invoice_override={"line_items": [igst_item], "tax_summary": tax}
    ))
    resp = client.post("/parse", files={"file": ("igst.pdf", MINIMAL_PDF, "application/pdf")})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["line_items"][0]["igst_rate"] > 0
    assert d["line_items"][0]["cgst_rate"] == 0
    assert d["line_items"][0]["sgst_rate"] == 0
    assert d["tax_summary"]["total_igst"] > 0
    assert d["tax_summary"]["total_cgst"] == 0


# ===========================================================================
# TC-006 — Hindi invoice
# ===========================================================================

def test_TC006_hindi_invoice(client, monkeypatch):
    lang_received = []

    def _parse(filename, content_type, file_bytes, language="en"):
        lang_received.append(language)
        return _make_success_parser()(filename, content_type, file_bytes, language)

    monkeypatch.setattr("app.main.parse_invoice", _parse)
    resp = client.post(
        "/parse",
        files={"file": ("hindi.pdf", MINIMAL_PDF, "application/pdf")},
        data={"language": "hi"},
    )
    assert resp.status_code == 200
    assert lang_received == ["hi"]
    d = resp.json()["data"]
    assert d["meta"]["confidence_score"] >= 0.70


# ===========================================================================
# TC-007 — Service invoice (SAC code starts with "99")
# ===========================================================================

def test_TC007_service_sac_code(client, monkeypatch):
    sac_item = {**copy.deepcopy(MINIMAL_INVOICE["line_items"][0]), "hsn_sac_code": "998314"}
    monkeypatch.setattr("app.main.parse_invoice", _make_success_parser(
        invoice_override={"line_items": [sac_item]}
    ))
    resp = client.post("/parse", files={"file": ("service.pdf", MINIMAL_PDF, "application/pdf")})
    assert resp.status_code == 200
    item = resp.json()["data"]["line_items"][0]
    assert item["hsn_sac_code"].startswith("99")


# ===========================================================================
# TC-008 — Not an invoice (PNG photo of food)
# ===========================================================================

def test_TC008_not_an_invoice(client, monkeypatch):
    def _parse(*a, **kw):
        raise ValueError("INVALID_FORMAT|File does not appear to be a GST invoice|")

    monkeypatch.setattr("app.main.parse_invoice", _parse)
    resp = client.post("/parse", files={"file": ("food.png", b"fake-png", "image/png")})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_FORMAT"


# ===========================================================================
# TC-009 — PDF exceeds 10MB size limit
# ===========================================================================

def test_TC009_pdf_too_large(client):
    # Don't mock parse_invoice — let real file validation run
    big = b"x" * (11 * 1024 * 1024)
    resp = client.post("/parse", files={"file": ("big.pdf", big, "application/pdf")})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FILE_TOO_LARGE"


# ===========================================================================
# TC-010 — Password-protected PDF
# ===========================================================================

def test_TC010_password_protected_pdf(client, monkeypatch):
    def _parse(*a, **kw):
        raise ValueError("INVALID_FORMAT|PDF is password protected|Remove the password and upload again.")

    monkeypatch.setattr("app.main.parse_invoice", _parse)
    resp = client.post("/parse", files={"file": ("protected.pdf", MINIMAL_PDF, "application/pdf")})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_FORMAT"
    assert "password" in body["error"]["message"].lower()


# ===========================================================================
# TC-011 — Empty file upload
# ===========================================================================

def test_TC011_empty_file(client):
    resp = client.post("/parse", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_FORMAT"


# ===========================================================================
# TC-012 — Unsupported file type (Excel)
# ===========================================================================

def test_TC012_unsupported_file_type(client):
    resp = client.post(
        "/parse",
        files={"file": ("invoice.xlsx", b"PK fake xlsx content", "application/vnd.ms-excel")},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNSUPPORTED_TYPE"


# ===========================================================================
# TC-013 — Invoice missing GSTIN
# ===========================================================================

def test_TC013_missing_gstin(client, monkeypatch):
    def _parse(*a, **kw):
        raise ValueError("MISSING_GSTIN|No GSTIN found — not a valid GST invoice|")

    monkeypatch.setattr("app.main.parse_invoice", _parse)
    resp = client.post("/parse", files={"file": ("bill.pdf", MINIMAL_PDF, "application/pdf")})
    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "MISSING_GSTIN"


# ===========================================================================
# TC-014 — Wrong / missing RapidAPI proxy secret
# ===========================================================================

def test_TC014_missing_rapidapi_secret(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "correct-secret-abc")
    client = TestClient(app)
    # No header at all
    resp = client.post("/parse", files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")})
    assert resp.status_code == 403


def test_TC014_wrong_rapidapi_secret(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "correct-secret-abc")
    client = TestClient(app)
    resp = client.post(
        "/parse",
        files={"file": ("invoice.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"X-RapidAPI-Proxy-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403


# ===========================================================================
# TC-016 — Concurrent requests (10 simultaneous, all within 15s)
# ===========================================================================

@pytest.mark.asyncio
async def test_TC016_concurrent_requests(monkeypatch):
    monkeypatch.setattr("app.main.parse_invoice", _make_success_parser())

    transport = ASGITransport(app=app)
    start = time.time()

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
    assert elapsed < 15, f"10 concurrent requests took {elapsed:.1f}s"
    for r in responses:
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        assert r.json()["success"] is True


# ===========================================================================
# Dashboard endpoint
# ===========================================================================

def test_dashboard_returns_summary(client, tmp_db, monkeypatch):
    import importlib
    import app.cost_tracker as ct
    importlib.reload(ct)
    ct.log_call(success=True, input_tokens=1000, output_tokens=600, file_type="pdf")

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_calls" in body
    assert "total_profit_usd" in body


def test_dashboard_protected_when_secret_set(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_SECRET", "my-internal-secret")
    resp = client.get("/dashboard")
    assert resp.status_code == 403
