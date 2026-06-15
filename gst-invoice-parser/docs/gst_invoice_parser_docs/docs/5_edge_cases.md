# Edge Cases & Error Handling — GST Invoice Parser

## Rule

Every edge case listed here must be handled explicitly in code. The correct behavior is defined. Claude Code must implement exactly this behavior — no silent failures, no crashes, no generic 500 errors.

---

## File validation edge cases

| Edge case | Detection | Expected behavior |
|---|---|---|
| File is not PDF/JPG/PNG | Check MIME type and extension | Return `UNSUPPORTED_TYPE` 400 |
| PDF is password protected | pdfplumber raises exception | Return `INVALID_FORMAT` 400 with message "PDF is password protected" |
| PDF is corrupted / unreadable | pdfplumber raises exception | Return `INVALID_FORMAT` 400 |
| PDF is scanned image (no text layer) | pdfplumber returns empty string | Fall back to pytesseract OCR on PDF pages |
| Image is too blurry to OCR | tesseract returns < 50 chars | Return `EXTRACTION_FAILED` 422 with message "Image quality too low" |
| File size exceeds limit | Check len(file_bytes) | Return `FILE_TOO_LARGE` 400 before any processing |
| Empty file uploaded | len(file_bytes) == 0 | Return `INVALID_FORMAT` 400 |
| File extension says PDF but MIME is image | Check actual MIME type | Process as image using pytesseract |
| Multi-page PDF | pdfplumber reads all pages | Extract text from all pages, concatenate, truncate to MAX_TEXT_CHARS |

---

## Content validation edge cases

| Edge case | Detection | Expected behavior |
|---|---|---|
| Uploaded file is not an invoice (e.g. a letter) | Claude returns `{"error": "NOT_AN_INVOICE"}` | Return `INVALID_FORMAT` 400 with message "File does not appear to be a GST invoice" |
| Invoice has no GSTIN anywhere | Claude sets gstin fields to null | Return `MISSING_GSTIN` 422 — not a valid GST invoice |
| Invoice is a credit note, not a tax invoice | Claude detects from header text | Return `INVALID_FORMAT` 400 with message "Credit notes not supported in v1" |
| Invoice is in a language other than EN/HI | Claude still attempts extraction | Extract what is possible, set confidence_score low, return partial data with warning field |
| Invoice has handwritten fields | OCR gets partial text | Claude extracts what it can, confidence_score reflects uncertainty |
| Invoice has 50+ line items | Text truncated at MAX_TEXT_CHARS | Extract as many line items as fit, add `"truncated": true` to meta |

---

## Claude API edge cases

| Edge case | Detection | Expected behavior |
|---|---|---|
| Claude returns invalid JSON | json.loads throws | Run `safe_parse_claude_response()`, if still fails return `EXTRACTION_FAILED` 422 |
| Claude returns JSON missing required fields | Pydantic validation fails | Return `EXTRACTION_FAILED` 422 with list of missing fields |
| Claude API timeout (>30s) | httpx.TimeoutException | Return `INTERNAL_ERROR` 500 with message "AI processing timed out, please retry" |
| Claude API rate limit hit | anthropic.RateLimitError | Retry once after 2 seconds, if still fails return `INTERNAL_ERROR` 500 |
| Claude API key invalid / expired | anthropic.AuthenticationError | Log critical alert, return `INTERNAL_ERROR` 500 — do NOT expose key status to user |
| Claude confidence_score below 0.7 | Check field in response | Return 200 but add `"warning": "Low confidence extraction — please verify manually"` |
| Claude API returns empty response | content[0].text is empty | Return `EXTRACTION_FAILED` 422 |

---

## Financial / sustainability edge cases

| Edge case | Detection | Expected behavior |
|---|---|---|
| Anthropic API cost spikes (model price change) | cost_tracker detects margin < $0.01 | Log warning, send alert email, continue processing |
| Cost per call exceeds revenue per call | cost_tracker running_margin < 0 | Trigger circuit breaker — halt processing, log critical alert |
| SQLite DB locked (concurrent writes) | sqlite3.OperationalError | Retry 3 times with 100ms backoff, then log and skip (don't fail the request) |
| RapidAPI proxy secret missing from request | Header not present | Return 403 Forbidden immediately |

---

## Infrastructure edge cases

| Edge case | Detection | Expected behavior |
|---|---|---|
| Railway server restarts mid-request | Connection drops | Request fails on client side — file was never stored so no cleanup needed |
| Disk full (temp file written) | OSError on write | Return `INTERNAL_ERROR` 500 — should not happen as files are processed in memory |
| High concurrency (many simultaneous requests) | FastAPI async handles natively | No special handling needed — uvicorn + async FastAPI is designed for this |
| Memory spike from large PDF | Monitor via Railway metrics | If RAM exceeds 450MB (Railway free tier limit), restrict MAX_PDF_SIZE_MB to 5 |

---

## What never to do

- Never return a raw Python exception traceback to the user
- Never log the full invoice text to console (contains PII)
- Never write the uploaded file to disk — process entirely in memory
- Never return a 200 status with an error inside — use correct HTTP status codes
- Never expose the Anthropic API key or RapidAPI secret in any response or log

---

## Error response format (always consistent)

```python
# errors.py

from fastapi.responses import JSONResponse

def error_response(code: str, message: str, detail: str = "", status: int = 400):
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": {
                "code": code,
                "message": message,
                "detail": detail
            }
        }
    )
```

Every error in the codebase must use this function — no ad-hoc error responses.
