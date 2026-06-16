"""
AI extraction client — uses Google Gemini internally.
Filename kept as claude_client.py for project consistency.
"""
import json
import logging
import os
import re
import time

from json_repair import repair_json

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert Indian GST invoice data extraction engine.

Your job is to extract structured data from GST invoice text and return it as valid JSON.

Rules you must follow without exception:
1. Return ONLY valid JSON. No explanation, no markdown, no code blocks.
2. Never invent or guess field values. If a field is not present in the invoice, set it to null.
3. All monetary amounts must be numbers (float), never strings.
4. Dates must be in YYYY-MM-DD format.
5. GSTIN must be exactly 15 characters if present.
6. If this does not appear to be a GST invoice, return: {"error": "NOT_AN_INVOICE"}
7. confidence_score must reflect how certain you are (0.0 to 1.0). Below 0.7 means unreliable.
8. Tax type inference — apply these rules in order for each line item:
   a. If the invoice explicitly labels CGST and SGST amounts → use cgst_rate/cgst_amount and sgst_rate/sgst_amount.
   b. If the invoice explicitly labels IGST → use igst_rate/igst_amount.
   c. If only a combined GST% is stated (e.g. "18% GST") without CGST/SGST/IGST labels:
      - If seller and buyer share the same 2-digit state code → split equally: cgst_rate = X/2, sgst_rate = X/2.
      - Otherwise (different states, or buyer has no GSTIN) → treat as IGST: igst_rate = X.
   d. Always compute the corresponding tax amounts from (taxable_amount × rate / 100) when the rate is known.
9. Tax amount from math — if grand_total and subtotal are both present and all tax amounts are still null, compute total_tax = grand_total − subtotal and populate the most likely tax field (igst_amount if inter-state or B2C, else cgst_amount + sgst_amount equally)."""

SYSTEM_PROMPT_HINDI_ADDON = """
The invoice may be in Hindi or a mix of Hindi and English.
Extract all fields regardless of language.
Return all extracted values in English in the JSON output."""

JSON_SCHEMA_STRING = """{
  "invoice_number": null,
  "invoice_date": null,
  "invoice_type": null,
  "seller": {"name": null, "gstin": null, "address": null, "state_code": null},
  "buyer":  {"name": null, "gstin": null, "address": null, "state_code": null},
  "line_items": [{
    "description": null, "hsn_sac_code": null, "quantity": null, "unit": null,
    "unit_price": null, "taxable_amount": null,
    "cgst_rate": null, "sgst_rate": null, "igst_rate": null,
    "cgst_amount": null, "sgst_amount": null, "igst_amount": null,
    "total_amount": null
  }],
  "tax_summary": {
    "subtotal": null, "total_cgst": null, "total_sgst": null, "total_igst": null,
    "total_cess": null, "round_off": null, "grand_total": null, "amount_in_words": null
  },
  "payment": {"bank_name": null, "account_number": null, "ifsc_code": null, "due_date": null},
  "meta": {"confidence_score": 0.0, "extraction_time_ms": null, "pages_processed": null, "currency": "INR"}
}"""

USER_PROMPT_TEMPLATE = """{system}

Extract GST invoice data. Return JSON only, matching this structure exactly:

{json_schema}

Types: invoice_type = "B2B"|"B2C"|null. Dates = YYYY-MM-DD. Amounts = float. GSTIN = 15 chars.

Invoice:
---
{extracted_text}
---"""

MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "12000"))


def _build_prompt(extracted_text: str, language: str) -> str:
    system = SYSTEM_PROMPT
    if language == "hi":
        system += SYSTEM_PROMPT_HINDI_ADDON
    return USER_PROMPT_TEMPLATE.format(
        system=system,
        json_schema=JSON_SCHEMA_STRING,
        extracted_text=extracted_text[:MAX_TEXT_CHARS],
    )


def _clean_json(raw: str) -> str:
    """Fix common model output issues before JSON parsing."""
    # Strip markdown fences
    raw = re.sub(r"```json|```", "", raw).strip()
    # Python literals → JSON
    raw = re.sub(r'\bNone\b', 'null', raw)
    raw = re.sub(r'\bTrue\b', 'true', raw)
    raw = re.sub(r'\bFalse\b', 'false', raw)
    # Trailing commas before } or ]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    return raw


def safe_parse_claude_response(raw: str) -> dict:
    """Strip markdown, fix common JSON issues, and parse Gemini response."""
    raw = _clean_json(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Use json-repair to fix any LLM output quirks (unescaped quotes, newlines, etc.)
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
        raise ValueError("AI returned unparseable response")


def call_claude(extracted_text: str, language: str = "en") -> tuple[dict, int, int]:
    """
    Call Gemini and return (parsed_dict, input_tokens, output_tokens).
    One automatic retry on rate limit. Auth errors never exposed to caller.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("INTERNAL_ERROR|GEMINI_API_KEY not configured|")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=8192,
        ),
    )
    prompt = _build_prompt(extracted_text, language)

    def _do_call():
        return model.generate_content(prompt)

    logger.info("Calling Gemini API (prompt length: %d chars)", len(prompt))
    try:
        response = _do_call()
    except google_exceptions.ResourceExhausted:
        logger.warning("Gemini rate limit hit — retrying in 2 seconds")
        time.sleep(2)
        try:
            response = _do_call()
        except google_exceptions.ResourceExhausted as exc:
            raise RuntimeError(
                "INTERNAL_ERROR|AI processing rate limited|Please retry in a few seconds."
            ) from exc
    except google_exceptions.PermissionDenied as exc:
        logger.critical("Gemini API key invalid or permission denied")
        raise RuntimeError("INTERNAL_ERROR|AI service authentication failed|") from exc
    except Exception as exc:
        msg = str(exc)
        logger.error("Gemini call failed: %s: %s", type(exc).__name__, msg)
        if "timeout" in msg.lower() or "timed out" in msg.lower() or "deadline" in msg.lower():
            raise RuntimeError("INTERNAL_ERROR|AI processing timed out, please retry|") from exc
        raise RuntimeError(f"INTERNAL_ERROR|Unexpected AI error|{msg}") from exc

    result = response.text
    if not result or not result.strip():
        raise ValueError("EXTRACTION_FAILED|AI returned an empty response|")

    input_tokens = 0
    output_tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

    try:
        parsed = safe_parse_claude_response(result)
    except ValueError:
        logger.warning("JSON parse failed on first attempt — retrying")
        time.sleep(1)
        response = _do_call()
        result = response.text
        if not result or not result.strip():
            raise ValueError("EXTRACTION_FAILED|AI returned an empty response|")
        parsed = safe_parse_claude_response(result)

    return parsed, input_tokens, output_tokens
