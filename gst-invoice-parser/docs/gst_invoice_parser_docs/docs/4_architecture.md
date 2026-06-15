# Tech Stack & Architecture — GST Invoice Parser

## Stack

| Layer | Technology | Version | Reason |
|---|---|---|---|
| API framework | FastAPI | 0.111.x | Fast, async, auto-generates docs |
| AI engine | Anthropic Claude (Haiku) | claude-haiku-4-5-20251001 | Cheapest, fast enough |
| PDF extraction | pdfplumber | 0.11.x | Best for structured PDFs |
| OCR (images) | pytesseract + Pillow | latest | Hindi + English OCR |
| Server | Uvicorn | 0.30.x | ASGI server for FastAPI |
| Hosting | Railway | — | Auto-deploy from GitHub, free tier |
| Monetisation | RapidAPI | — | Handles API keys + billing |
| Cost tracking | SQLite | built-in | Lightweight, no extra service needed |
| Env management | python-dotenv | latest | Local dev secrets |

---

## Folder structure

```
gst-invoice-parser/
├── docs/                        # All planning documents (this folder)
│   ├── 1_prd.md
│   ├── 2_api_schema.md
│   ├── 3_prompts.md
│   ├── 4_architecture.md
│   ├── 5_edge_cases.md
│   ├── 6_sustainability.md
│   └── 7_test_cases.md
│
├── app/
│   ├── main.py                  # FastAPI app entry point, route definitions
│   ├── parser.py                # Core logic: extract text → call Claude → return JSON
│   ├── claude_client.py         # Anthropic API wrapper, prompt templates
│   ├── file_handler.py          # PDF/image validation, text extraction
│   ├── cost_tracker.py          # SQLite logging of cost vs revenue per call
│   ├── models.py                # Pydantic models for request/response validation
│   └── errors.py                # Custom exception classes and error responses
│
├── tests/
│   ├── test_parser.py           # Unit tests for parsing logic
│   ├── test_api.py              # Integration tests for endpoints
│   └── sample_invoices/         # Real GST invoice PDFs for testing
│
├── .env                         # Local secrets (never commit this)
├── .env.example                 # Template showing required env vars
├── requirements.txt             # All dependencies with pinned versions
├── Procfile                     # Railway start command
├── railway.toml                 # Railway config
└── README.md                    # Setup instructions
```

---

## Environment variables

```bash
# .env.example — copy to .env and fill in values

ANTHROPIC_API_KEY=sk-ant-...          # From console.anthropic.com
RAPIDAPI_PROXY_SECRET=...             # From RapidAPI dashboard (validates requests)
MAX_PDF_SIZE_MB=10                    # Reject files above this
MAX_IMAGE_SIZE_MB=5
MAX_TEXT_CHARS=12000                  # Claude input cap
COST_PER_CALL_USD=0.003              # Haiku estimate, update if model changes
REVENUE_PER_CALL_USD=0.08            # Must match RapidAPI pricing
SQLITE_DB_PATH=./data/tracker.db
LOG_LEVEL=INFO
```

---

## Data flow — single request

```
User uploads invoice PDF
        |
        v
FastAPI POST /parse
        |
        v
file_handler.py
  - Validate file type (PDF/JPG/PNG only)
  - Validate file size
  - Extract raw text (pdfplumber or pytesseract)
        |
        v
parser.py
  - Validate extracted text is not empty
  - Truncate to MAX_TEXT_CHARS
        |
        v
claude_client.py
  - Build system prompt + user prompt
  - Call Claude Haiku API
  - Parse JSON response
  - Handle JSON errors (safe_parse)
        |
        v
cost_tracker.py
  - Log: timestamp, tokens_used, cost, success/fail
        |
        v
FastAPI returns JSON response to user
        |
        v
File deleted from memory (never written to disk)
```

---

## Railway deployment

```toml
# railway.toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
```

```
# Procfile (fallback)
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

---

## RapidAPI integration

RapidAPI acts as a reverse proxy. Every request from a user hits RapidAPI first, which:
1. Validates their API key
2. Checks their plan limits
3. Forwards the request to your Railway URL with a secret header

In `app/main.py`, validate this header on every request:

```python
from fastapi import Header, HTTPException

async def verify_rapidapi(x_rapidapi_proxy_secret: str = Header(...)):
    if x_rapidapi_proxy_secret != os.environ["RAPIDAPI_PROXY_SECRET"]:
        raise HTTPException(status_code=403, detail="Forbidden")
```

---

## Local dev setup

```bash
git clone https://github.com/yourusername/gst-invoice-parser
cd gst-invoice-parser
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # Fill in your keys
uvicorn app.main:app --reload   # Starts on http://localhost:8000
```
