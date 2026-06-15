# Test Cases — GST Invoice Parser

## How to use this document

Claude Code will read this file and generate `tests/test_parser.py` and `tests/test_api.py` from these cases. Every case has an ID, input description, and exact expected output or behavior.

Run all tests before every deployment:
```bash
pytest tests/ -v
```

---

## Happy path tests (must all pass)

### TC-001 — Standard B2B invoice PDF

```
Input:  sample_invoices/standard_b2b.pdf
        Valid 2-party GST invoice, English, 1 page, 3 line items
Expected:
  - HTTP 200
  - success: true
  - seller.gstin is 15 characters
  - buyer.gstin is 15 characters
  - len(line_items) == 3
  - tax_summary.grand_total > 0
  - meta.confidence_score >= 0.85
```

### TC-002 — B2C invoice (buyer has no GSTIN)

```
Input:  sample_invoices/b2c_invoice.pdf
        Consumer purchase, buyer GSTIN absent
Expected:
  - HTTP 200
  - success: true
  - invoice_type: "B2C"
  - buyer.gstin: null
  - seller.gstin is present and 15 chars
```

### TC-003 — Multi-page invoice PDF

```
Input:  sample_invoices/multipage.pdf
        3-page invoice with 15 line items
Expected:
  - HTTP 200
  - success: true
  - len(line_items) >= 10  (may truncate if text exceeds limit)
  - meta.pages_processed == 3
```

### TC-004 — Invoice as JPG image

```
Input:  sample_invoices/invoice_scan.jpg
        Clear photo of a printed GST invoice
Expected:
  - HTTP 200
  - success: true
  - All core fields extracted
  - meta.confidence_score >= 0.75
```

### TC-005 — IGST-only invoice (interstate transaction)

```
Input:  sample_invoices/igst_invoice.pdf
        Seller Maharashtra (27), Buyer Karnataka (29)
Expected:
  - HTTP 200
  - line_items[0].igst_rate > 0
  - line_items[0].cgst_rate == 0
  - line_items[0].sgst_rate == 0
  - tax_summary.total_igst > 0
  - tax_summary.total_cgst == 0
```

### TC-006 — Hindi invoice

```
Input:  sample_invoices/hindi_invoice.pdf
        Invoice with fields in Devanagari script
        Request: language=hi
Expected:
  - HTTP 200
  - success: true
  - All JSON values in English
  - meta.confidence_score >= 0.70
```

### TC-007 — Invoice with service items (SAC code)

```
Input:  sample_invoices/service_invoice.pdf
        IT services invoice, SAC code 998314
Expected:
  - HTTP 200
  - line_items[0].hsn_sac_code starts with "99"
  - line_items[0].unit == "NOS" or null
```

---

## Edge case tests (must return correct error)

### TC-008 — Uploaded file is a PNG photo of food (not invoice)

```
Input:  sample_invoices/not_an_invoice.png
Expected:
  - HTTP 400
  - success: false
  - error.code: "INVALID_FORMAT"
```

### TC-009 — PDF exceeds 10MB size limit

```
Input:  Generate 11MB dummy PDF in test
Expected:
  - HTTP 400
  - success: false
  - error.code: "FILE_TOO_LARGE"
```

### TC-010 — Password-protected PDF

```
Input:  sample_invoices/protected.pdf
Expected:
  - HTTP 400
  - success: false
  - error.code: "INVALID_FORMAT"
  - error.message contains "password"
```

### TC-011 — Empty file upload

```
Input:  0-byte file
Expected:
  - HTTP 400
  - success: false
  - error.code: "INVALID_FORMAT"
```

### TC-012 — Unsupported file type (Excel spreadsheet)

```
Input:  invoice.xlsx
Expected:
  - HTTP 400
  - success: false
  - error.code: "UNSUPPORTED_TYPE"
```

### TC-013 — Invoice missing GSTIN on both parties

```
Input:  sample_invoices/no_gstin.pdf
        A simple bill with no GSTIN numbers
Expected:
  - HTTP 422
  - success: false
  - error.code: "MISSING_GSTIN"
```

### TC-014 — Request with wrong / missing RapidAPI proxy secret

```
Input:  Valid PDF, but header X-RapidAPI-Proxy-Secret is absent or wrong
Expected:
  - HTTP 403
  - Forbidden (do not process the file at all)
```

### TC-015 — Health check endpoint

```
Input:  GET /health
Expected:
  - HTTP 200
  - {"status": "ok", "version": "1.0.0"}
```

---

## Performance tests

### TC-016 — Response time under load

```
Tool: pytest-asyncio + httpx AsyncClient
Action: Send 10 concurrent requests with standard_b2b.pdf
Expected:
  - All 10 return HTTP 200
  - All complete within 15 seconds
  - No 500 errors
```

### TC-017 — Cost tracking accuracy

```
Action: Process TC-001 invoice
Expected:
  - call_log table has 1 new row after request
  - estimated_cost_usd is between 0.001 and 0.010
  - revenue_usd == 0.08
  - profit_usd > 0
```

---

## Sample invoice files needed

Create or download these before running tests. Store in `tests/sample_invoices/`.

| Filename | How to get it |
|---|---|
| `standard_b2b.pdf` | Download any sample GST invoice from ClearTax or Zoho |
| `b2c_invoice.pdf` | Generate from any GST billing software |
| `multipage.pdf` | Create a 3-page invoice in Word and export |
| `invoice_scan.jpg` | Take a clear photo of a printed invoice |
| `igst_invoice.pdf` | Interstate GST invoice sample |
| `hindi_invoice.pdf` | Hindi language invoice (search on Google) |
| `service_invoice.pdf` | IT services invoice with SAC code |
| `not_an_invoice.png` | Any photo that is clearly not an invoice |
| `protected.pdf` | Create a password-protected PDF in Adobe |
| `no_gstin.pdf` | Simple shop bill with no GSTIN |

---

## Running specific test groups

```bash
# Run only happy path tests
pytest tests/ -v -k "TC_00"

# Run only edge case tests
pytest tests/ -v -k "TC_01"

# Run with cost tracking verification
pytest tests/ -v -k "TC_017"

# Run all and show coverage
pytest tests/ -v --cov=app --cov-report=term-missing
```
