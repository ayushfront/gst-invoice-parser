import os
import pytest

# Set env vars BEFORE any app module is imported so module-level reads pick them up
os.environ.setdefault("GEMINI_API_KEY", "test-key-offline")
os.environ.setdefault("RAPIDAPI_PROXY_SECRET", "")
os.environ.setdefault("INTERNAL_SECRET", "")
os.environ.setdefault("REVENUE_PER_CALL_USD", "0.08")
os.environ.setdefault("LOG_LEVEL", "ERROR")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Provide a fresh SQLite DB path for each test that needs it."""
    db = str(tmp_path / "tracker.db")
    monkeypatch.setenv("SQLITE_DB_PATH", db)
    return db


# Shared minimal invoice dict used across test modules
VALID_GSTIN_SELLER = "27AABCU9603R1ZX"
VALID_GSTIN_BUYER = "29AABCU9603R1ZY"

MINIMAL_INVOICE = {
    "invoice_number": "INV-2024-00123",
    "invoice_date": "2024-03-15",
    "invoice_type": "B2B",
    "seller": {
        "name": "Acme Pvt Ltd",
        "gstin": VALID_GSTIN_SELLER,
        "address": "Plot 12, MIDC, Pune, Maharashtra 411019",
        "state_code": "27",
    },
    "buyer": {
        "name": "Buyer Corp India",
        "gstin": VALID_GSTIN_BUYER,
        "address": "14 MG Road, Bengaluru, Karnataka 560001",
        "state_code": "29",
    },
    "line_items": [
        {
            "description": "Office Chair",
            "hsn_sac_code": "9401",
            "quantity": 10,
            "unit": "NOS",
            "unit_price": 2500.0,
            "taxable_amount": 25000.0,
            "cgst_rate": 9.0,
            "sgst_rate": 9.0,
            "igst_rate": 0.0,
            "cgst_amount": 2250.0,
            "sgst_amount": 2250.0,
            "igst_amount": 0.0,
            "total_amount": 29500.0,
        }
    ],
    "tax_summary": {
        "subtotal": 25000.0,
        "total_cgst": 2250.0,
        "total_sgst": 2250.0,
        "total_igst": 0.0,
        "total_cess": 0.0,
        "round_off": 0.0,
        "grand_total": 29500.0,
        "amount_in_words": "Twenty Nine Thousand Five Hundred Rupees Only",
    },
    "payment": {
        "bank_name": "HDFC Bank",
        "account_number": "XXXX1234",
        "ifsc_code": "HDFC0001234",
        "due_date": "2024-04-15",
    },
    "meta": {
        "confidence_score": 0.97,
        "extraction_time_ms": None,
        "pages_processed": None,
        "currency": "INR",
    },
}

# Minimal valid PDF bytes (1 blank page, text layer present)
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
