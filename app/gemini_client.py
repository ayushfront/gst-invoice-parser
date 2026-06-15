import json
import logging
import os
import re
import time

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

logger = logging.getLogger(__name__)

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
      "quantity": number_or_null,
      "unit": "string or null",
      "unit_price": number_or_null,
      "taxable_amount": number_or_null,
      "cgst_rate": number_or_null,
      "sgst_rate": number_or_null,
      "igst_rate": number_or_null,
      "cgst_amount": number_or_null,
      "sgst_amount": number_or_null,
      "igst_amount": number_or_null,
      "total_amount": number_or_null
    }
  ],
  "tax_summary": {
    "subtotal": number_or_null,
    "total_cgst": number_or_null,
    "total_sgst": number_or_null,
    "total_igst": number_or_null,
    "total_cess": number_or_null,
    "round_off": number_or_null,
    "grand_total": number_or_null,
    "amount_in_words": "string or null"
  },
  "payment": {
    "bank_name": "string or null",
    "account_number": "string or null",
    "ifsc_code": "string or null",
    "due_date": "YYYY-MM-DD or null"
  },
  "meta": {
    "confidence_score": 0.0_to_1.0,
    "extraction_time_ms": null,
    "pages_processed": null,
    "currency": "INR"
  }
}"""

USER_PROMPT_TEMPLATE = """Extract all GST invoice data from the following invoice text and return as JSON matching this exact structure:

{json_schema}

Invoice text:
---
{extracted_text}
---

Return only the JSON object. Nothing else."""

MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "12000"))
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


def _build_system_prompt(language: str) -> str:
    prompt = SYSTEM_PROMPT
    if language == "hi":
        prompt += SYSTEM_PROMPT_HINDI_ADDON
    return prompt


def safe_parse_gemini_response(raw: str) -> dict:
    raw = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError("Gemini returned unparseable response")


def call_gemini(extracted_text: str, language: str = "en") -> tuple[dict, int, int]:
    """
    Call Gemini and return (parsed_dict, input_tokens, output_tokens).
    Handles rate limit with one retry and auth errors gracefully.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    genai.configure(api_key=api_key)

    system = _build_system_prompt(language)
    user_content = USER_PROMPT_TEMPLATE.format(
        json_schema=JSON_SCHEMA_STRING,
        extracted_text=extracted_text[:MAX_TEXT_CHARS],
    )

    model = genai.GenerativeModel(
        model_name=DEFAULT_MODEL,
        system_instruction=system,
    )

    def _do_call():
        return model.generate_content(
            user_content,
            generation_config=genai.GenerationConfig(
                max_output_tokens=2048,
                temperature=0.0,
            ),
        )

    try:
        response = _do_call()
    except google_exceptions.ResourceExhausted:
        logger.warning("Gemini rate limit hit — retrying in 2 seconds")
        time.sleep(2)
        try:
            response = _do_call()
        except google_exceptions.ResourceExhausted as e:
            raise RuntimeError("INTERNAL_ERROR|AI processing rate limited|Please retry in a few seconds.") from e
    except google_exceptions.PermissionDenied as e:
        logger.critical("Gemini API key invalid or permission denied")
        raise RuntimeError("INTERNAL_ERROR|AI service authentication failed|Please contact support.") from e
    except google_exceptions.DeadlineExceeded as e:
        raise RuntimeError("INTERNAL_ERROR|AI processing timed out, please retry|") from e
    except Exception as e:
        msg = str(e)
        if "timeout" in msg.lower() or "timed out" in msg.lower():
            raise RuntimeError("INTERNAL_ERROR|AI processing timed out, please retry|") from e
        raise RuntimeError(f"INTERNAL_ERROR|Unexpected AI error|{msg}") from e

    raw = response.text.strip() if response.text else ""
    if not raw:
        raise ValueError("EXTRACTION_FAILED|Gemini returned an empty response|")

    input_tokens = 0
    output_tokens = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        input_tokens = response.usage_metadata.prompt_token_count or 0
        output_tokens = response.usage_metadata.candidates_token_count or 0

    parsed = safe_parse_gemini_response(raw)
    return parsed, input_tokens, output_tokens


# Keep backward-compatible alias so any code importing call_claude still works
call_claude = call_gemini
safe_parse_claude_response = safe_parse_gemini_response
