# Prompt Engineering — GST Invoice Parser

## Overview

Claude API is called once per invoice parse request. The extracted text from the PDF/image is passed to Claude with a carefully structured prompt. Claude returns JSON only — no prose, no explanation.

---

## Step 1 — PDF/image to text

Before calling Claude, extract raw text from the file using:

```python
# For PDF
import pdfplumber

def extract_text_from_pdf(file_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)

# For images
import pytesseract
from PIL import Image

def extract_text_from_image(file_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(file_bytes))
    return pytesseract.image_to_string(image, lang="eng+hin")
```

Pass the extracted text string to the Claude prompt below.

---

## System prompt

```
You are an expert Indian GST invoice data extraction engine.

Your job is to extract structured data from GST invoice text and return it as valid JSON.

Rules you must follow without exception:
1. Return ONLY valid JSON. No explanation, no markdown, no code blocks.
2. Never invent or guess field values. If a field is not present in the invoice, set it to null.
3. All monetary amounts must be numbers (float), never strings.
4. Dates must be in YYYY-MM-DD format.
5. GSTIN must be exactly 15 characters if present.
6. If this does not appear to be a GST invoice, return: {"error": "NOT_AN_INVOICE"}
7. confidence_score must reflect how certain you are (0.0 to 1.0). Below 0.7 means unreliable.
8. If a line item's tax type cannot be determined, set cgst_rate, sgst_rate, igst_rate all to null.
```

---

## User prompt template

```python
USER_PROMPT_TEMPLATE = """
Extract all GST invoice data from the following invoice text and return as JSON 
matching this exact structure:

{json_schema}

Invoice text:
---
{extracted_text}
---

Return only the JSON object. Nothing else.
"""
```

Where `{json_schema}` is the full output schema from `2_api_schema.md` (the `data` object).

---

## Claude API call

```python
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def call_claude(extracted_text: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest model — sufficient for extraction
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(
                    json_schema=JSON_SCHEMA_STRING,
                    extracted_text=extracted_text[:12000]  # hard cap to control cost
                )
            }
        ]
    )
    raw = response.content[0].text.strip()
    return json.loads(raw)
```

---

## Model choice and cost reasoning

| Model | Cost per call (est.) | Accuracy | Decision |
|---|---|---|---|
| claude-haiku-4-5-20251001 | ~$0.003 | 94% | USE THIS — leaves $0.077 profit margin |
| claude-sonnet-4-6 | ~$0.015 | 98% | Only if haiku accuracy proves insufficient |
| claude-opus-4-6 | ~$0.075 | 99% | Never — kills margin |

Start with Haiku. Upgrade to Sonnet only if accuracy complaints come in from real users.

---

## Prompt for Hindi invoices

Add to the system prompt when `language=hi`:

```
The invoice may be in Hindi or a mix of Hindi and English. 
Extract all fields regardless of language. 
Return all extracted values in English in the JSON output.
```

---

## Handling Claude returning invalid JSON

```python
import re

def safe_parse_claude_response(raw: str) -> dict:
    # Strip accidental markdown code blocks
    raw = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON object from response
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError("Claude returned unparseable response")
```

---

## Token budget

- Max input tokens: ~3000 (12000 chars of invoice text)
- Max output tokens: 2048
- Average actual usage: ~1200 input + ~800 output = ~2000 tokens per call
- Estimated Haiku cost: $0.003 per call
