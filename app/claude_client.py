"""
AI extraction client — uses Google Gemini internally.
Filename kept as claude_client.py for project consistency.
"""
import json
import logging
import os
import re
import time

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
8. If a line item's tax type cannot be determined, set cgst_rate, sgst_rate, igst_rate all to null."""

SYSTEM_PROMPT_HINDI_ADDON = """
The invoice may be in Hindi or a mix of Hindi and English.
Extract all fields regardless of language.
Return all extracted values in English in the JSON output."""

JSON_SCHEMA_STRING = """{
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "invoice_type": "B2B or B2C or null",
  "seller": {
    "name": "string or null",
    "gstin": "15-char string or null",
    "address": "string or null",
    "state_code": "2-digit string or null"
  },
  "buyer": {
    "name": "string or null",
    "gstin": "15-char string or null (null for B2C)",
    "address": "string or null",
    "state_code": "2-digit string or null"
  },
  "line_items": [
    {
      "description": "string or null",
      "hsn_sac_code": "string or null",
      "quantity": "number or null",
      "unit": "string or null",
      "unit_price": "number or null",
      "taxable_amount": "number or null",
      "cgst_rate": "number or null",
      "sgst_rate": "number or null",
      "igst_rate": "number or null",
      "cgst_amount": "number or null",
      "sgst_amount": "number or null",
      "igst_amount": "number or null",
      "total_amount": "number or null"
    }
  ],
  "tax_summary": {
    "subtotal": "number or null",
    "total_cgst": "number or null",
    "total_sgst": "number or null",
    "total_igst": "number or null",
    "total_cess": "number or null",
    "round_off": "number or null",
    "grand_total": "number or null",
    "amount_in_words": "string or null"
  },
  "payment": {
    "bank_name": "string or null",
    "account_number": "string or null",
    "ifsc_code": "string or null",
    "due_date": "YYYY-MM-DD or null"
  },
  "meta": {
    "confidence_score": "float 0.0-1.0",
    "extraction_time_ms": null,
    "pages_processed": null,
    "currency": "INR"
  }
}"""

USER_PROMPT_TEMPLATE = """{system}

Extract all GST invoice data from the following invoice text and return as JSON matching this exact structure:

{json_schema}

Invoice text:
---
{extracted_text}
---

Return only the JSON object. Nothing else."""

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


def safe_parse_claude_response(raw: str) -> dict:
    """Strip markdown fences and parse JSON from Gemini response."""
    raw = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
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
    model = genai.GenerativeModel("gemini-3.5-flash")
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

    parsed = safe_parse_claude_response(result)
    return parsed, input_tokens, output_tokens
