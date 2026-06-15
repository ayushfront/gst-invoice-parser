import logging
import os
import time
from typing import Any

from app.claude_client import call_claude
from app.cost_tracker import log_call
from app.file_handler import FileValidationError, extract_text, validate_file
from app.models import InvoiceData

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "12000"))
LOW_CONFIDENCE_THRESHOLD = 0.7


def parse_invoice(
    filename: str,
    content_type: str,
    file_bytes: bytes,
    language: str = "en",
) -> dict[str, Any]:
    """
    Full pipeline: validate → extract text → call AI → post-process.

    Returns serialisable dict on success.
    Raises ValueError  with 'CODE|message|detail' for known user-facing errors.
    Raises RuntimeError with 'CODE|message|detail' for infra errors.
    """
    start_ms = int(time.time() * 1000)
    file_type = _guess_type(filename, content_type)

    # ------------------------------------------------------------------ #
    # 1. File validation (type, size, empty)
    # ------------------------------------------------------------------ #
    try:
        category = validate_file(filename, content_type, file_bytes)
    except FileValidationError as exc:
        _safe_log(success=False, file_type=file_type, error_code=_code(str(exc)))
        raise ValueError(str(exc)) from exc

    # ------------------------------------------------------------------ #
    # 2. Text extraction
    # ------------------------------------------------------------------ #
    try:
        raw_text, page_count = extract_text(category, file_bytes)
    except FileValidationError as exc:
        _safe_log(success=False, file_type=file_type, error_code=_code(str(exc)))
        raise ValueError(str(exc)) from exc

    if not raw_text.strip():
        _safe_log(success=False, file_type=file_type, error_code="EXTRACTION_FAILED")
        raise ValueError("EXTRACTION_FAILED|Could not extract any text from the file|")

    truncated = len(raw_text) > MAX_TEXT_CHARS
    raw_text = raw_text[:MAX_TEXT_CHARS]

    # ------------------------------------------------------------------ #
    # 3. AI extraction
    # ------------------------------------------------------------------ #
    parsed, input_tokens, output_tokens = call_claude(raw_text, language=language)

    # ------------------------------------------------------------------ #
    # 4. Content validation
    # ------------------------------------------------------------------ #

    # 4a. Not an invoice
    if parsed.get("error") == "NOT_AN_INVOICE":
        _safe_log(success=False, file_type=file_type, error_code="INVALID_FORMAT")
        raise ValueError(
            "INVALID_FORMAT|File does not appear to be a GST invoice|"
            "The AI could not identify invoice fields in the document."
        )

    # 4b. Credit note / debit note
    if _is_credit_note(parsed):
        _safe_log(success=False, file_type=file_type, error_code="INVALID_FORMAT")
        raise ValueError(
            "INVALID_FORMAT|Credit notes not supported in v1|"
            "Please upload a standard GST tax invoice."
        )

    # 4c. GSTIN check
    if not _has_any_gstin(parsed):
        _safe_log(success=False, file_type=file_type, error_code="MISSING_GSTIN")
        raise ValueError(
            "MISSING_GSTIN|No GSTIN found — not a valid GST invoice|"
            "A valid GST invoice must contain at least a seller GSTIN."
        )

    # ------------------------------------------------------------------ #
    # 5. Populate meta
    # ------------------------------------------------------------------ #
    elapsed_ms = int(time.time() * 1000) - start_ms
    meta = parsed.get("meta") or {}
    meta["extraction_time_ms"] = elapsed_ms
    meta["pages_processed"] = page_count
    meta["currency"] = meta.get("currency") or "INR"
    if truncated:
        meta["truncated"] = True

    confidence = meta.get("confidence_score")
    if confidence is not None and float(confidence) < LOW_CONFIDENCE_THRESHOLD:
        meta["warning"] = "Low confidence extraction — please verify manually"
    parsed["meta"] = meta

    # ------------------------------------------------------------------ #
    # 6. Pydantic validation
    # ------------------------------------------------------------------ #
    try:
        invoice_data = InvoiceData(**parsed)
    except Exception as exc:
        _safe_log(success=False, file_type=file_type, error_code="EXTRACTION_FAILED")
        raise ValueError(
            f"EXTRACTION_FAILED|AI response missing required fields|{exc}"
        ) from exc

    # ------------------------------------------------------------------ #
    # 7. Cost tracking (non-fatal — never fail the request on DB errors)
    # ------------------------------------------------------------------ #
    try:
        log_call(
            success=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            file_type=file_type,
        )
    except RuntimeError:
        raise   # circuit breaker — surface this
    except Exception as exc:
        logger.error("Cost tracking failed (non-fatal): %s", exc)

    return invoice_data.model_dump()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_any_gstin(data: dict) -> bool:
    seller_gstin = (data.get("seller") or {}).get("gstin")
    buyer_gstin = (data.get("buyer") or {}).get("gstin")
    return bool(seller_gstin) or bool(buyer_gstin)


def _is_credit_note(data: dict) -> bool:
    for field in ("invoice_number", "invoice_type"):
        val = str(data.get(field) or "").lower()
        if any(kw in val for kw in ("credit note", "cr note", "debit note", "dn ")):
            return True
    return False


def _code(error_str: str) -> str:
    """Extract the CODE from 'CODE|message|detail' format."""
    return error_str.split("|")[0] if "|" in error_str else "INTERNAL_ERROR"


def _guess_type(filename: str, content_type: str) -> str:
    if "pdf" in content_type or filename.lower().endswith(".pdf"):
        return "pdf"
    if "image" in content_type or any(filename.lower().endswith(e) for e in (".jpg", ".jpeg", ".png")):
        return "image"
    return "unknown"


def _safe_log(**kwargs) -> None:
    """Log cost record, swallowing any DB errors."""
    try:
        log_call(**kwargs)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("Cost tracking failed (non-fatal): %s", exc)
