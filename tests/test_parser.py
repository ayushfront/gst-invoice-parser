"""
Unit tests for file_handler.py and claude_client.py helpers.
These tests do NOT call real Anthropic or real PDF files (except where noted).
"""

import io
import json
import os
import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# file_handler tests
# ---------------------------------------------------------------------------

class TC_FileHandler:

    def test_TC_empty_file_raises(self):
        from app.file_handler import validate_file
        with pytest.raises(ValueError, match="INVALID_FORMAT"):
            validate_file("invoice.pdf", "application/pdf", b"")

    def test_TC_unsupported_extension_raises(self):
        from app.file_handler import validate_file
        with pytest.raises(ValueError, match="UNSUPPORTED_TYPE"):
            validate_file("invoice.xlsx", "application/vnd.ms-excel", b"x" * 100)

    def test_TC_pdf_too_large_raises(self):
        from app.file_handler import validate_file
        big = b"x" * (11 * 1024 * 1024)
        with pytest.raises(ValueError, match="FILE_TOO_LARGE"):
            validate_file("invoice.pdf", "application/pdf", big)

    def test_TC_image_too_large_raises(self):
        from app.file_handler import validate_file
        big = b"x" * (6 * 1024 * 1024)
        with pytest.raises(ValueError, match="FILE_TOO_LARGE"):
            validate_file("invoice.jpg", "image/jpeg", big)

    def test_TC_valid_pdf_returns_pdf_category(self):
        from app.file_handler import validate_file
        result = validate_file("invoice.pdf", "application/pdf", b"x" * 100)
        assert result == "pdf"

    def test_TC_valid_png_returns_image_category(self):
        from app.file_handler import validate_file
        result = validate_file("invoice.png", "image/png", b"x" * 100)
        assert result == "image"

    def test_TC_pdf_extension_but_image_mime_returns_image(self):
        from app.file_handler import validate_file
        result = validate_file("invoice.pdf", "image/jpeg", b"x" * 100)
        assert result == "image"

    def test_TC_unsupported_mime_no_ext_raises(self):
        from app.file_handler import validate_file
        with pytest.raises(ValueError, match="UNSUPPORTED_TYPE"):
            validate_file("invoice", "text/plain", b"x" * 100)


# ---------------------------------------------------------------------------
# claude_client safe_parse tests
# ---------------------------------------------------------------------------

class TC_SafeParse:

    def test_TC_clean_json_parses(self):
        from app.claude_client import safe_parse_claude_response
        raw = json.dumps({"invoice_number": "INV-001"})
        result = safe_parse_claude_response(raw)
        assert result["invoice_number"] == "INV-001"

    def test_TC_markdown_fenced_json_stripped(self):
        from app.claude_client import safe_parse_claude_response
        raw = "```json\n{\"invoice_number\": \"INV-002\"}\n```"
        result = safe_parse_claude_response(raw)
        assert result["invoice_number"] == "INV-002"

    def test_TC_json_embedded_in_prose_extracted(self):
        from app.claude_client import safe_parse_claude_response
        raw = 'Sure! Here is the result: {"invoice_number": "INV-003"} Hope that helps.'
        result = safe_parse_claude_response(raw)
        assert result["invoice_number"] == "INV-003"

    def test_TC_totally_invalid_raises(self):
        from app.claude_client import safe_parse_claude_response
        with pytest.raises((ValueError, json.JSONDecodeError)):
            safe_parse_claude_response("this is not json at all")


# ---------------------------------------------------------------------------
# parser logic tests (mocked Claude)
# ---------------------------------------------------------------------------

class TC_Parser:

    def _make_pdf_bytes(self) -> bytes:
        """Minimal valid PDF bytes (1-page blank)."""
        return (
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

    def test_TC_001_happy_path_b2b(self, monkeypatch):
        """TC-001: Standard B2B invoice returns correct structure."""
        invoice = dict(MINIMAL_INVOICE_DICT)

        def mock_call_claude(text, language="en"):
            return dict(invoice), 1200, 800

        monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "")
        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)

        def mock_extract_text(category, file_bytes):
            return "Invoice text", 1

        monkeypatch.setattr("app.parser.extract_text", mock_extract_text)
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", self._make_pdf_bytes())

        assert result["seller"]["gstin"] is not None
        assert len(result["seller"]["gstin"]) == 15
        assert result["buyer"]["gstin"] is not None
        assert len(result["line_items"]) == 1
        assert result["tax_summary"]["grand_total"] > 0
        assert result["meta"]["confidence_score"] >= 0.85

    def test_TC_002_b2c_buyer_no_gstin(self, monkeypatch):
        """TC-002: B2C invoice where buyer has no GSTIN."""
        invoice = dict(MINIMAL_INVOICE_DICT)
        invoice["invoice_type"] = "B2C"
        invoice["buyer"] = {"name": "Consumer", "gstin": None, "address": "Delhi", "state_code": "07"}

        def mock_call_claude(text, language="en"):
            return dict(invoice), 1000, 700

        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("text", 1))
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", b"x")

        assert result["invoice_type"] == "B2C"
        assert result["buyer"]["gstin"] is None
        assert result["seller"]["gstin"] is not None

    def test_TC_005_igst_interstate(self, monkeypatch):
        """TC-005: Interstate invoice uses IGST only."""
        invoice = dict(MINIMAL_INVOICE_DICT)
        invoice["line_items"] = [
            {
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
        ]
        invoice["tax_summary"] = {**invoice["tax_summary"], "total_cgst": 0, "total_sgst": 0, "total_igst": 9000.0}

        def mock_call_claude(text, language="en"):
            return dict(invoice), 1000, 700

        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("text", 1))
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", b"x")

        assert result["line_items"][0]["igst_rate"] > 0
        assert result["line_items"][0]["cgst_rate"] == 0
        assert result["line_items"][0]["sgst_rate"] == 0
        assert result["tax_summary"]["total_igst"] > 0
        assert result["tax_summary"]["total_cgst"] == 0

    def test_TC_not_an_invoice_raises(self, monkeypatch):
        """Claude signals NOT_AN_INVOICE → ValueError INVALID_FORMAT."""
        def mock_call_claude(text, language="en"):
            return {"error": "NOT_AN_INVOICE"}, 500, 50

        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("random text", 1))
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")

        from app.parser import parse_invoice
        with pytest.raises(ValueError, match="INVALID_FORMAT"):
            parse_invoice("photo.pdf", "application/pdf", b"x")

    def test_TC_missing_gstin_raises(self, monkeypatch):
        """No GSTIN in seller or buyer → MISSING_GSTIN."""
        invoice = dict(MINIMAL_INVOICE_DICT)
        invoice["seller"] = {"name": "Shop", "gstin": None, "address": "Delhi", "state_code": "07"}
        invoice["buyer"] = {"name": "Person", "gstin": None, "address": "Delhi", "state_code": "07"}

        def mock_call_claude(text, language="en"):
            return dict(invoice), 800, 500

        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("text", 1))
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")

        from app.parser import parse_invoice
        with pytest.raises(ValueError, match="MISSING_GSTIN"):
            parse_invoice("invoice.pdf", "application/pdf", b"x")

    def test_TC_low_confidence_adds_warning(self, monkeypatch):
        """Confidence < 0.7 results in a warning field in meta."""
        invoice = dict(MINIMAL_INVOICE_DICT)
        invoice["meta"] = {**invoice["meta"], "confidence_score": 0.55}

        def mock_call_claude(text, language="en"):
            return dict(invoice), 800, 500

        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("text", 1))
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", b"x")
        assert "warning" in result["meta"]
        assert "Low confidence" in result["meta"]["warning"]

    def test_TC_text_truncation_sets_flag(self, monkeypatch, monkeypatch_env):
        """If extracted text > MAX_TEXT_CHARS, truncated flag is set in meta."""
        invoice = dict(MINIMAL_INVOICE_DICT)

        def mock_call_claude(text, language="en"):
            return dict(invoice), 1200, 800

        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")

        long_text = "A" * 13000  # > 12000 chars
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: (long_text, 1))

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", b"x")
        assert result["meta"].get("truncated") is True

    def test_TC_multipage_sets_pages_processed(self, monkeypatch):
        """page_count from extractor is set in meta.pages_processed."""
        invoice = dict(MINIMAL_INVOICE_DICT)

        def mock_call_claude(text, language="en"):
            return dict(invoice), 1200, 800

        monkeypatch.setattr("app.parser.call_claude", mock_call_claude)
        monkeypatch.setattr("app.parser.log_call", lambda **kwargs: None)
        monkeypatch.setattr("app.parser.validate_file", lambda *a, **kw: "pdf")
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("text", 3))

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", b"x")
        assert result["meta"]["pages_processed"] == 3


@pytest.fixture
def monkeypatch_env(monkeypatch):
    monkeypatch.setenv("MAX_TEXT_CHARS", "12000")
    return monkeypatch


# ---------------------------------------------------------------------------
# cost_tracker tests
# ---------------------------------------------------------------------------

class TC_CostTracker:

    def test_TC_017_log_creates_row(self, tmp_path, monkeypatch):
        """TC-017: log_call writes a row with correct financial fields."""
        db_path = str(tmp_path / "tracker.db")
        monkeypatch.setenv("SQLITE_DB_PATH", db_path)
        monkeypatch.setenv("COST_PER_CALL_USD", "0.003")
        monkeypatch.setenv("REVENUE_PER_CALL_USD", "0.08")

        # Reload module to pick up env vars
        import importlib
        import app.cost_tracker as ct
        importlib.reload(ct)

        record = ct.log_call(success=True, input_tokens=1200, output_tokens=800)
        assert record is not None
        assert 0.001 <= record["estimated_cost_usd"] <= 0.010
        assert record["revenue_usd"] == pytest.approx(0.08)
        assert record["profit_usd"] > 0

    def test_TC_circuit_breaker_negative_margin(self, tmp_path, monkeypatch):
        """Cost > revenue triggers circuit breaker RuntimeError."""
        db_path = str(tmp_path / "tracker.db")
        monkeypatch.setenv("SQLITE_DB_PATH", db_path)
        monkeypatch.setenv("COST_PER_CALL_USD", "0.10")
        monkeypatch.setenv("REVENUE_PER_CALL_USD", "0.08")

        import importlib
        import app.cost_tracker as ct
        importlib.reload(ct)

        with pytest.raises(RuntimeError, match="INTERNAL_ERROR"):
            ct.log_call(success=True, estimated_cost_usd=0.10)
