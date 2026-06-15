# API Schema — GST Invoice Parser

## Base URL

```
https://gst-invoice-parser.railway.app
```

On RapidAPI this becomes:
```
https://gst-invoice-parser.p.rapidapi.com
```

---

## Endpoints

### POST `/parse`

The only endpoint. Accepts a GST invoice file and returns structured JSON.

#### Request

```
POST /parse
Content-Type: multipart/form-data
X-RapidAPI-Key: {user_api_key}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | File | Yes | PDF or image (JPG/PNG) of the GST invoice |
| `language` | string | No | `"en"` or `"hi"` — default `"en"` |

#### Successful response — HTTP 200

```json
{
  "success": true,
  "data": {
    "invoice_number": "INV-2024-00123",
    "invoice_date": "2024-03-15",
    "invoice_type": "B2B",

    "seller": {
      "name": "Acme Pvt Ltd",
      "gstin": "27AABCU9603R1ZX",
      "address": "Plot 12, MIDC, Pune, Maharashtra 411019",
      "state_code": "27"
    },

    "buyer": {
      "name": "Buyer Corp India",
      "gstin": "29AABCU9603R1ZY",
      "address": "14 MG Road, Bengaluru, Karnataka 560001",
      "state_code": "29"
    },

    "line_items": [
      {
        "description": "Office Chair",
        "hsn_sac_code": "9401",
        "quantity": 10,
        "unit": "NOS",
        "unit_price": 2500.00,
        "taxable_amount": 25000.00,
        "cgst_rate": 9,
        "sgst_rate": 9,
        "igst_rate": 0,
        "cgst_amount": 2250.00,
        "sgst_amount": 2250.00,
        "igst_amount": 0,
        "total_amount": 29500.00
      }
    ],

    "tax_summary": {
      "subtotal": 25000.00,
      "total_cgst": 2250.00,
      "total_sgst": 2250.00,
      "total_igst": 0,
      "total_cess": 0,
      "round_off": 0,
      "grand_total": 29500.00,
      "amount_in_words": "Twenty Nine Thousand Five Hundred Rupees Only"
    },

    "payment": {
      "bank_name": "HDFC Bank",
      "account_number": "XXXX1234",
      "ifsc_code": "HDFC0001234",
      "due_date": "2024-04-15"
    },

    "meta": {
      "confidence_score": 0.97,
      "extraction_time_ms": 3200,
      "pages_processed": 1,
      "currency": "INR"
    }
  }
}
```

#### Error response — HTTP 400 / 422 / 500

```json
{
  "success": false,
  "error": {
    "code": "INVALID_FORMAT",
    "message": "Uploaded file does not appear to be a GST invoice.",
    "detail": "No GSTIN number detected in document."
  }
}
```

---

## Error codes

| Code | HTTP status | Meaning |
|---|---|---|
| `INVALID_FORMAT` | 400 | File is not a recognisable invoice |
| `FILE_TOO_LARGE` | 400 | PDF > 10MB or image > 5MB |
| `UNSUPPORTED_TYPE` | 400 | File type not PDF/JPG/PNG |
| `EXTRACTION_FAILED` | 422 | Claude could not extract fields reliably |
| `MISSING_GSTIN` | 422 | No GSTIN found — not a valid GST invoice |
| `RATE_LIMITED` | 429 | User exceeded RapidAPI plan limit |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

---

## Rate limits (set on RapidAPI plans)

| Plan | Calls/month | Price |
|---|---|---|
| Free | 20 | $0 |
| Basic | 500 | $19/month |
| Pro | 2000 | $49/month |
| Ultra | 10000 | $149/month |

---

## Health check

```
GET /health
```

Returns `{"status": "ok", "version": "1.0.0"}` — used by Railway for uptime monitoring.
