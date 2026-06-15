# Product Requirements Document — GST Invoice Parser API

## What this API does

Accepts a GST invoice (PDF or image) and returns structured JSON containing all key invoice fields — buyer, seller, GSTIN numbers, line items, tax breakdown, and totals. The intelligence layer is Claude AI. The transport layer is FastAPI. The monetisation layer is RapidAPI.

---

## Problem it solves

Indian accountants, CA firms, ERP vendors, and e-commerce sellers process hundreds of GST invoices daily. Extracting structured data from these PDFs manually is slow, error-prone, and expensive. No affordable, reliable API exists specifically tuned for Indian GST invoice formats.

---

## Target users

| User type | How they use it | Volume |
|---|---|---|
| CA firms & accounting offices | Batch-process client invoices into Tally/Zoho | 50–500 invoices/day |
| ERP vendors (small Indian SaaS) | Embed in their invoice module | 1000+ calls/day |
| E-commerce sellers (Amazon/Flipkart) | Parse supplier invoices for reconciliation | 20–100/day |
| Freelance developers | Build invoice tools for their clients | Low volume, one-time |

---

## What it must do

- Accept PDF uploads up to 10MB
- Accept image uploads (JPG, PNG) up to 5MB
- Extract all standard GST invoice fields (see API Schema doc)
- Return valid JSON every single time — never return raw text
- Handle both B2B and B2C invoice formats
- Handle invoices in English and Hindi
- Process a single invoice in under 10 seconds
- Return a clear error object (never crash silently) when extraction fails

---

## What it must NOT do

- Store the uploaded invoice file permanently (delete after processing)
- Return partial JSON — either full extraction or a clean error
- Make up data that isn't present in the invoice (hallucinate fields)
- Allow unauthenticated requests (all calls require RapidAPI key)
- Process files that are not invoices (return error, not garbage data)
- Exceed $0.02 cost per API call (self-sustainability requirement)

---

## Success metrics

- Extraction accuracy above 95% on standard GST invoices
- Response time under 10 seconds (p95)
- Cost per call under $0.02 (Claude API tokens)
- Revenue per call $0.08 (set on RapidAPI)
- Profit margin per call: $0.06 minimum

---

## Out of scope (v1)

- Multi-invoice batch processing (v2)
- Direct Tally / Zoho integration (v3)
- E-way bill parsing (separate API)
- Credit note / debit note parsing (separate API)
