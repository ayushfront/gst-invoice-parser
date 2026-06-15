"""
Unit tests — file_handler, claude_client helpers, parser pipeline.
All AI calls are mocked; no real Gemini API key required.
Covers: TC-001 through TC-007, TC-010, TC-013, TC-017 + helper tests.
"""
import copy
import json
import pytest

from tests.conftest import MINIMAL_INVOICE, MINIMAL_PDF, VALID_GSTIN_BUYER, VALID_GSTIN_SELLER


# ===========================================================================
# file_handler — validate_file
# ===========================================================================

class TestFileHandler:

    def test_empty_file_raises_invalid_format(self):
        from app.file_handler import FileValidationError, validate_file
        with pytest.raises(FileValidationError, match="INVALID_FORMAT"):
            validate_file("invoice.pdf", "application/pdf", b"")

    def test_unsupported_extension_raises(self):
        from app.file_handler import FileValidationError, validate_file
        with pytest.raises(FileValidationError, match="UNSUPPORTED_TYPE"):
            validate_file("invoice.xlsx", "application/vnd.ms-excel", b"data")

    def test_pdf_too_large_raises(self):
        from app.file_handler import FileValidationError, validate_file
        with pytest.raises(FileValidationError, match="FILE_TOO_LARGE"):
            validate_file("invoice.pdf", "application/pdf", b"x" * (11 * 1024 * 1024))

    def test_image_too_large_raises(self):
        from app.file_handler import FileValidationError, validate_file
        with pytest.raises(FileValidationError, match="FILE_TOO_LARGE"):
            validate_file("invoice.jpg", "image/jpeg", b"x" * (6 * 1024 * 1024))

    def test_valid_pdf_returns_pdf_category(self):
        from app.file_handler import validate_file
        assert validate_file("invoice.pdf", "application/pdf", b"data") == "pdf"

    def test_valid_png_returns_image_category(self):
        from app.file_handler import validate_file
        assert validate_file("invoice.png", "image/png", b"data") == "image"

    def test_valid_jpg_returns_image_category(self):
        from app.file_handler import validate_file
        assert validate_file("invoice.jpg", "image/jpeg", b"data") == "image"

    def test_pdf_ext_but_image_mime_returns_image(self):
        """Edge case: extension says pdf but MIME is image → process as image."""
        from app.file_handler import validate_file
        assert validate_file("invoice.pdf", "image/jpeg", b"data") == "image"

    def test_no_extension_raises(self):
        from app.file_handler import FileValidationError, validate_file
        with pytest.raises(FileValidationError, match="UNSUPPORTED_TYPE"):
            validate_file("invoice", "text/plain", b"data")


# ===========================================================================
# claude_client — safe_parse_claude_response
# ===========================================================================

class TestSafeParse:

    def test_clean_json_parses(self):
        from app.claude_client import safe_parse_claude_response
        assert safe_parse_claude_response('{"key": "value"}') == {"key": "value"}

    def test_markdown_fence_stripped(self):
        from app.claude_client import safe_parse_claude_response
        raw = "```json\n{\"invoice_number\": \"INV-001\"}\n```"
        assert safe_parse_claude_response(raw)["invoice_number"] == "INV-001"

    def test_json_embedded_in_prose(self):
        from app.claude_client import safe_parse_claude_response
        raw = 'Here you go: {"invoice_number": "INV-002"} Done!'
        assert safe_parse_claude_response(raw)["invoice_number"] == "INV-002"

    def test_totally_invalid_raises(self):
        from app.claude_client import safe_parse_claude_response
        with pytest.raises((ValueError, json.JSONDecodeError)):
            safe_parse_claude_response("this is not json")

    def test_bare_fence_stripped(self):
        from app.claude_client import safe_parse_claude_response
        raw = "```\n{\"k\": 1}\n```"
        assert safe_parse_claude_response(raw) == {"k": 1}


# ===========================================================================
# parser pipeline — mocked AI + file handler
# ===========================================================================

def _mock_all(monkeypatch, invoice_override=None, page_count=1, text="invoice text"):
    """Patch validate_file, extract_text, call_claude, log_call for a clean success path."""
    invoice = copy.deepcopy(MINIMAL_INVOICE)
    if invoice_override:
        invoice.update(invoice_override)

    monkeypatch.setattr("app.parser.validate_file", lambda fn, ct, fb: "pdf")
    monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: (text, page_count))
    monkeypatch.setattr("app.parser.call_claude", lambda t, language="en": (copy.deepcopy(invoice), 1200, 800))
    monkeypatch.setattr("app.parser.log_call", lambda **kw: {"estimated_cost_usd": 0.001, "revenue_usd": 0.08, "profit_usd": 0.079})


class TestParser:

    # TC-001 — Standard B2B invoice
    def test_TC001_b2b_invoice(self, monkeypatch):
        _mock_all(monkeypatch)
        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", MINIMAL_PDF)

        assert result["seller"]["gstin"] == VALID_GSTIN_SELLER
        assert len(result["seller"]["gstin"]) == 15
        assert result["buyer"]["gstin"] == VALID_GSTIN_BUYER
        assert len(result["buyer"]["gstin"]) == 15
        assert len(result["line_items"]) == 1
        assert result["tax_summary"]["grand_total"] > 0
        assert result["meta"]["confidence_score"] >= 0.85

    # TC-002 — B2C invoice: buyer has no GSTIN
    def test_TC002_b2c_no_buyer_gstin(self, monkeypatch):
        _mock_all(monkeypatch, invoice_override={
            "invoice_type": "B2C",
            "buyer": {"name": "Consumer", "gstin": None, "address": "Delhi", "state_code": "07"},
        })
        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", MINIMAL_PDF)

        assert result["invoice_type"] == "B2C"
        assert result["buyer"]["gstin"] is None
        assert result["seller"]["gstin"] is not None

    # TC-003 — Multi-page invoice
    def test_TC003_multipage_sets_pages_processed(self, monkeypatch):
        items = [copy.deepcopy(MINIMAL_INVOICE["line_items"][0]) for _ in range(15)]
        for i, it in enumerate(items):
            it["description"] = f"Item {i}"
        _mock_all(monkeypatch, invoice_override={"line_items": items}, page_count=3)

        from app.parser import parse_invoice
        result = parse_invoice("multi.pdf", "application/pdf", MINIMAL_PDF)

        assert len(result["line_items"]) >= 10
        assert result["meta"]["pages_processed"] == 3

    # TC-004 — Image (JPG) invoice goes through image category
    def test_TC004_jpg_invoice(self, monkeypatch):
        monkeypatch.setattr("app.parser.validate_file", lambda fn, ct, fb: "image")
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("invoice text", 1))
        monkeypatch.setattr("app.parser.call_claude", lambda t, language="en": (copy.deepcopy(MINIMAL_INVOICE), 900, 700))
        monkeypatch.setattr("app.parser.log_call", lambda **kw: None)

        from app.parser import parse_invoice
        result = parse_invoice("invoice.jpg", "image/jpeg", b"fake-jpg")
        assert result["meta"]["confidence_score"] >= 0.75

    # TC-005 — IGST-only interstate invoice
    def test_TC005_igst_interstate(self, monkeypatch):
        igst_item = {**copy.deepcopy(MINIMAL_INVOICE["line_items"][0]),
                     "cgst_rate": 0.0, "sgst_rate": 0.0, "igst_rate": 18.0,
                     "cgst_amount": 0.0, "sgst_amount": 0.0, "igst_amount": 9000.0}
        tax = {**MINIMAL_INVOICE["tax_summary"], "total_cgst": 0.0, "total_sgst": 0.0, "total_igst": 9000.0}
        _mock_all(monkeypatch, invoice_override={"line_items": [igst_item], "tax_summary": tax})

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", MINIMAL_PDF)
        item = result["line_items"][0]
        assert item["igst_rate"] > 0
        assert item["cgst_rate"] == 0
        assert item["sgst_rate"] == 0
        assert result["tax_summary"]["total_igst"] > 0
        assert result["tax_summary"]["total_cgst"] == 0

    # TC-006 — Hindi invoice: language param passed through
    def test_TC006_hindi_language_passed(self, monkeypatch):
        received_lang = []
        monkeypatch.setattr("app.parser.validate_file", lambda fn, ct, fb: "pdf")
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("invoice text", 1))

        def mock_call(text, language="en"):
            received_lang.append(language)
            return copy.deepcopy(MINIMAL_INVOICE), 1000, 700

        monkeypatch.setattr("app.parser.call_claude", mock_call)
        monkeypatch.setattr("app.parser.log_call", lambda **kw: None)

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", MINIMAL_PDF, language="hi")
        assert received_lang == ["hi"]
        assert result["meta"]["confidence_score"] >= 0.70

    # TC-007 — Service invoice: SAC code starts with "99"
    def test_TC007_service_sac_code(self, monkeypatch):
        sac_item = {**copy.deepcopy(MINIMAL_INVOICE["line_items"][0]), "hsn_sac_code": "998314"}
        _mock_all(monkeypatch, invoice_override={"line_items": [sac_item]})

        from app.parser import parse_invoice
        result = parse_invoice("service.pdf", "application/pdf", MINIMAL_PDF)
        assert result["line_items"][0]["hsn_sac_code"].startswith("99")

    # TC-008 — Not an invoice: AI returns NOT_AN_INVOICE
    def test_TC008_not_an_invoice(self, monkeypatch):
        monkeypatch.setattr("app.parser.validate_file", lambda fn, ct, fb: "pdf")
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("random text", 1))
        monkeypatch.setattr("app.parser.call_claude", lambda t, language="en": ({"error": "NOT_AN_INVOICE"}, 400, 40))
        monkeypatch.setattr("app.parser.log_call", lambda **kw: None)

        from app.parser import parse_invoice
        with pytest.raises(ValueError, match="INVALID_FORMAT"):
            parse_invoice("photo.png", "image/png", b"fake")

    # TC-010 — Password-protected PDF raises via file_handler
    def test_TC010_password_protected_pdf(self, monkeypatch):
        from app.file_handler import FileValidationError

        monkeypatch.setattr("app.parser.validate_file", lambda fn, ct, fb: "pdf")
        monkeypatch.setattr(
            "app.parser.extract_text",
            lambda cat, fb: (_ for _ in ()).throw(
                FileValidationError("INVALID_FORMAT|PDF is password protected|Remove the password and upload again.")
            ),
        )
        monkeypatch.setattr("app.parser.log_call", lambda **kw: None)

        from app.parser import parse_invoice
        with pytest.raises(ValueError, match="INVALID_FORMAT"):
            parse_invoice("protected.pdf", "application/pdf", MINIMAL_PDF)

    # TC-013 — Missing GSTIN on both parties
    def test_TC013_missing_gstin(self, monkeypatch):
        no_gstin = copy.deepcopy(MINIMAL_INVOICE)
        no_gstin["seller"]["gstin"] = None
        no_gstin["buyer"]["gstin"] = None
        monkeypatch.setattr("app.parser.validate_file", lambda fn, ct, fb: "pdf")
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("text", 1))
        monkeypatch.setattr("app.parser.call_claude", lambda t, language="en": (no_gstin, 800, 500))
        monkeypatch.setattr("app.parser.log_call", lambda **kw: None)

        from app.parser import parse_invoice
        with pytest.raises(ValueError, match="MISSING_GSTIN"):
            parse_invoice("no_gstin.pdf", "application/pdf", MINIMAL_PDF)

    # Low confidence → warning field added
    def test_low_confidence_adds_warning(self, monkeypatch):
        inv = copy.deepcopy(MINIMAL_INVOICE)
        inv["meta"]["confidence_score"] = 0.55
        _mock_all(monkeypatch, invoice_override=inv)

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", MINIMAL_PDF)
        assert "warning" in result["meta"]
        assert "Low confidence" in result["meta"]["warning"]

    # Text truncation sets meta.truncated = True
    def test_text_truncation_sets_flag(self, monkeypatch):
        _mock_all(monkeypatch, text="A" * 13000)

        from app.parser import parse_invoice
        result = parse_invoice("invoice.pdf", "application/pdf", MINIMAL_PDF)
        assert result["meta"].get("truncated") is True

    # Credit note rejected
    def test_credit_note_rejected(self, monkeypatch):
        inv = copy.deepcopy(MINIMAL_INVOICE)
        inv["invoice_number"] = "Credit Note 001"
        monkeypatch.setattr("app.parser.validate_file", lambda fn, ct, fb: "pdf")
        monkeypatch.setattr("app.parser.extract_text", lambda cat, fb: ("text", 1))
        monkeypatch.setattr("app.parser.call_claude", lambda t, language="en": (inv, 800, 500))
        monkeypatch.setattr("app.parser.log_call", lambda **kw: None)

        from app.parser import parse_invoice
        with pytest.raises(ValueError, match="INVALID_FORMAT"):
            parse_invoice("credit.pdf", "application/pdf", MINIMAL_PDF)


# ===========================================================================
# TC-017 — Cost tracking accuracy
# ===========================================================================

class TestCostTracker:

    def test_TC017_log_creates_row_with_correct_financials(self, tmp_db):
        import importlib
        import app.cost_tracker as ct
        importlib.reload(ct)

        record = ct.log_call(success=True, input_tokens=1200, output_tokens=800, file_type="pdf")
        assert record is not None
        assert 0.001 <= record["estimated_cost_usd"] <= 0.010
        assert record["revenue_usd"] == pytest.approx(0.08)
        assert record["profit_usd"] > 0

    def test_daily_summary_aggregates_correctly(self, tmp_db):
        import importlib
        import app.cost_tracker as ct
        importlib.reload(ct)

        ct.log_call(success=True, input_tokens=1000, output_tokens=600, file_type="pdf")
        ct.log_call(success=True, input_tokens=1000, output_tokens=600, file_type="pdf")
        summary = ct.get_daily_summary()
        assert summary["total_calls"] == 2
        assert summary["successful_calls"] == 2
        assert summary["total_revenue_usd"] == pytest.approx(0.16)
        assert summary["total_profit_usd"] > 0

    def test_failed_call_zero_revenue(self, tmp_db):
        import importlib
        import app.cost_tracker as ct
        importlib.reload(ct)

        record = ct.log_call(success=False, input_tokens=500, output_tokens=50,
                             file_type="pdf", error_code="INVALID_FORMAT")
        assert record is not None
        assert record["revenue_usd"] == 0.0
        assert record["profit_usd"] < 0

    def test_circuit_breaker_fires_on_large_loss(self, tmp_db, monkeypatch):
        """Force a scenario where profit < MIN_PROFIT_MARGIN_PER_CALL."""
        import importlib
        import app.cost_tracker as ct
        importlib.reload(ct)

        # Override MIN_PROFIT_MARGIN so a normal call triggers it
        monkeypatch.setattr(ct, "MIN_PROFIT_MARGIN_PER_CALL", 999.0)

        with pytest.raises(RuntimeError, match="INTERNAL_ERROR"):
            ct.log_call(success=True, input_tokens=1000, output_tokens=600, file_type="pdf")
