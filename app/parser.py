import logging
import os
import time
from typing import Any

from app.claude_client import call_claude
from app.cost_tracker import COST_PER_CALL_USD, log_call
from app.file_handler import extract_text, validate_file
from app.models import InvoiceData, Meta

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "12000"))
LOW_CONFIDENCE_THRESHOLD = 0.7


def _check_gstin(data: dict) -> bool:
    seller_gstin = (data.get("seller") or {}).get("gstin")
    buyer_gstin = (data.get("buyer") or {}).get("gstin")
    return bool(seller_gstin) or bool(buyer_gstin)


def _validate_gstin_length(gstin: str | None) -> bool:
    return gstin is None or len(gstin) == 15


def parse_invoice(
    filename: str,
    content_type: str,
    file_bytes: bytes,
    language: str = "en",
) -> dict[str, Any]:
    """
    Main pipeline: validate → extract text → call Claude → post-process.
    Returns the full success response dict.
    Raises ValueError with embedded code for known errors.
    Raises RuntimeError with embedded code for infra errors.
    """
    start_ms = int(time.time() * 1000)

    # 1. File validation
    category = validate_file(filename, content_type, file_bytes)

    # 2. Text extraction
    raw_text, page_count = extract_text(category, file_bytes)

    if not raw_text.strip():
        raise ValueError("EXTRACTION_FAILED|Could not extract any text from the file|")

    truncated = len(raw_text) > MAX_TEXT_CHARS
    raw_text = raw_text[:MAX_TEXT_CHARS]

    # 3. Call Claude
    parsed, input_tokens, output_tokens = call_claude(raw_text, language=language)

    # 4. Handle Claude's NOT_AN_INVOICE signal
    if parsed.get("error") == "NOT_AN_INVOICE":
        log_call(success=False)
        raise ValueError(
            "INVALID_FORMAT|File does not appear to be a GST invoice|"
            "Claude could not identify invoice fields in the document."
        )

    # 5. Check for credit notes
    invoice_num_str = str(parsed.get("invoice_number") or "").lower()
    if "credit note" in invoice_num_str or _is_credit_note(parsed):
        log_call(success=False)
        raise ValueError(
            "INVALID_FORMAT|Credit notes not supported in v1|"
            "Please upload a standard GST tax invoice."
        )

    # 6. GSTIN validation
    if not _check_gstin(parsed):
        log_call(success=False)
        raise ValueError(
            "MISSING_GSTIN|No GSTIN found — not a valid GST invoice|"
            "A valid GST invoice must contain at least a seller GSTIN."
        )

    # 7. Populate meta fields
    elapsed_ms = int(time.time() * 1000) - start_ms
    meta = parsed.get("meta") or {}
    meta["extraction_time_ms"] = elapsed_ms
    meta["pages_processed"] = page_count
    meta["currency"] = meta.get("currency") or "INR"
    if truncated:
        meta["truncated"] = True
    confidence = meta.get("confidence_score")
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        meta["warning"] = "Low confidence extraction — please verify manually"
    parsed["meta"] = meta

    # 8. Validate with Pydantic (raises if schema violated)
    try:
        invoice_data = InvoiceData(**parsed)
    except Exception as e:
        missing = str(e)
        log_call(success=False)
        raise ValueError(f"EXTRACTION_FAILED|Claude response missing required fields|{missing}") from e

    # 9. Cost tracking (best-effort — don't fail the request if DB is broken)
    try:
        log_call(success=True, input_tokens=input_tokens, output_tokens=output_tokens)
    except RuntimeError:
        raise  # circuit breaker — re-raise
    except Exception as e:
        logger.error("Cost tracking failed (non-fatal): %s", e)

    return invoice_data.model_dump()


def _is_credit_note(data: dict) -> bool:
    """Heuristic to detect credit notes from extracted fields."""
    for field in ["invoice_number", "invoice_type"]:
        val = str(data.get(field) or "").lower()
        if "credit" in val or "cr note" in val or "debit" in val:
            return True
    return False
