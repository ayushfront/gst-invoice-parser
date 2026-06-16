import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from typing import List
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()

# ── rate limiter (in-memory, no extra packages) ───────────────
_RATE_LIMIT_RPM  = int(os.environ.get("RATE_LIMIT_RPM", "20"))   # requests per minute per IP
_rate_lock       = Lock()
_ip_timestamps: dict[str, deque] = defaultdict(deque)

def _rate_limit(request: Request) -> None:
    ip  = (request.client.host if request.client else "unknown")
    now = time.monotonic()
    with _rate_lock:
        dq = _ip_timestamps[ip]
        # drop timestamps older than 60 s
        while dq and now - dq[0] > 60.0:
            dq.popleft()
        if len(dq) >= _RATE_LIMIT_RPM:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "RATE_LIMITED",
                    "message": f"Too many requests — limit is {_RATE_LIMIT_RPM} per minute.",
                    "detail": "Please wait before retrying.",
                },
            )
        dq.append(now)

from app.cost_tracker import get_daily_summary
from app.errors import error_response
from app.parser import parse_invoice

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GST Invoice Parser",
    description=(
        "AI-powered REST API for extracting structured data from Indian GST invoices. "
        "Supports PDF, JPG, and PNG. Returns seller, buyer, line items, CGST/SGST/IGST, "
        "and tax summary as typed JSON."
    ),
    version="1.0.0",
    docs_url=None,   # we serve a custom /docs below
    redoc_url=None,
    openapi_tags=[
        {
            "name": "Parsing",
            "description": "Upload invoices and receive structured JSON data.",
        },
        {
            "name": "System",
            "description": "Health checks, usage analytics, and server info.",
        },
    ],
)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

VERSION         = "1.0.0"
MAX_BATCH_FILES = int(os.environ.get("MAX_BATCH_FILES", "20"))

# ── shared response examples ─────────────────────────────────
_EXAMPLE_INVOICE_DATA = {
    "invoice_number": "INV-2024-0087",
    "invoice_date": "2024-11-14",
    "invoice_type": "B2B",
    "seller": {
        "name": "TechNova Solutions Pvt Ltd",
        "gstin": "27ABCDE1234F1Z5",
        "address": "Plot 12, Andheri East, Mumbai, Maharashtra 400069",
        "state_code": "27",
    },
    "buyer": {
        "name": "ABC Traders",
        "gstin": "29XYZAB5678C1Z2",
        "address": "15 MG Road, Bengaluru, Karnataka 560001",
        "state_code": "29",
    },
    "line_items": [
        {
            "description": "Laptop",
            "hsn_sac_code": "8471",
            "quantity": 2,
            "unit": "PCS",
            "unit_price": 50000.0,
            "taxable_amount": 100000.0,
            "cgst_rate": None,
            "sgst_rate": None,
            "igst_rate": 18.0,
            "cgst_amount": None,
            "sgst_amount": None,
            "igst_amount": 18000.0,
            "total_amount": 118000.0,
        }
    ],
    "tax_summary": {
        "subtotal": 100000.0,
        "total_cgst": None,
        "total_sgst": None,
        "total_igst": 18000.0,
        "total_cess": None,
        "round_off": 0.0,
        "grand_total": 118000.0,
        "amount_in_words": "One Lakh Eighteen Thousand Rupees Only",
    },
    "payment": {
        "bank_name": "HDFC Bank",
        "account_number": "5020012345678",
        "ifsc_code": "HDFC0001234",
        "due_date": "2024-12-14",
    },
    "meta": {
        "confidence_score": 0.95,
        "extraction_time_ms": 3241,
        "pages_processed": 1,
        "currency": "INR",
    },
}

_PARSE_200 = {
    "description": "Invoice parsed successfully.",
    "content": {
        "application/json": {
            "example": {"success": True, "data": _EXAMPLE_INVOICE_DATA}
        }
    },
}

_PARSE_400 = {
    "description": "Invalid file — wrong format, too large, or empty.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "UNSUPPORTED_TYPE",
                    "message": "Unsupported file type '.docx'",
                    "detail": "Only PDF, JPG, and PNG files are accepted.",
                }
            }
        }
    },
}

_PARSE_422 = {
    "description": "File is valid but invoice data could not be extracted.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "EXTRACTION_FAILED",
                    "message": "AI returned an empty response",
                    "detail": "",
                }
            }
        }
    },
}

_PARSE_500 = {
    "description": "Unexpected server error.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected server error occurred.",
                    "detail": "",
                }
            }
        }
    },
}


# ── auth helpers ──────────────────────────────────────────────

def _check_rapidapi(secret: str | None) -> None:
    required = os.environ.get("RAPIDAPI_PROXY_SECRET", "")
    if not required or required == "dev":
        return
    if secret != required:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Invalid or missing proxy secret.", "detail": ""},
        )


def _check_internal(secret: str | None) -> None:
    required = os.environ.get("INTERNAL_SECRET", "")
    if required and secret != required:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Invalid or missing internal secret.", "detail": ""},
        )


# ── routes ────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(str(_STATIC_DIR / "landing.html"))


@app.get("/app", include_in_schema=False)
async def spa():
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/docs", include_in_schema=False)
async def custom_docs():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="GST Invoice Parser — API Docs",
        swagger_css_url="/static/swagger-custom.css",
    )


@app.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description="Returns `ok` when the server is running. Use this for uptime monitoring.",
    responses={
        200: {
            "description": "Server is healthy.",
            "content": {"application/json": {"example": {"status": "ok", "version": "1.0.0"}}},
        }
    },
)
async def health():
    return {"status": "ok", "version": VERSION}


@app.post(
    "/parse",
    tags=["Parsing"],
    summary="Parse a single GST invoice",
    description=(
        "Upload a single PDF or image (JPG/PNG) GST invoice. "
        "The API extracts all fields and returns typed JSON.\n\n"
        "**Supported file types:** PDF, JPG, PNG (max 10 MB for PDF, 5 MB for images)\n\n"
        "**language** — `en` (default) or `hi` for Hindi/bilingual invoices.\n\n"
        "The `x-rapidapi-proxy-secret` header is only required when deployed via RapidAPI."
    ),
    responses={200: _PARSE_200, 400: _PARSE_400, 422: _PARSE_422, 500: _PARSE_500},
)
async def parse(
    request: Request,
    file: UploadFile = File(..., description="GST invoice file — PDF, JPG, or PNG."),
    language: str = Form(default="en", description="Invoice language: `en` (English) or `hi` (Hindi)."),
    x_rapidapi_proxy_secret: str | None = Header(
        default=None,
        description="RapidAPI proxy secret. Leave blank for local / direct API use.",
        include_in_schema=True,
    ),
    _rl: None = Depends(_rate_limit),
):
    _check_rapidapi(x_rapidapi_proxy_secret)

    if language not in ("en", "hi"):
        language = "en"

    file_bytes = await file.read()
    filename = file.filename or "upload.pdf"
    content_type = file.content_type or "application/octet-stream"

    try:
        invoice_data = parse_invoice(
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
            language=language,
        )
        return JSONResponse(status_code=200, content={"success": True, "data": invoice_data})

    except (ValueError, FileNotFoundError) as exc:
        logger.warning("Validation error during parsing: %s", exc)
        return _dispatch_error(str(exc), default_status=400)

    except RuntimeError as exc:
        logger.error("Runtime error during parsing: %s", exc)
        return _dispatch_error(str(exc), default_status=500)

    except Exception as exc:
        logger.exception("Unexpected error during invoice parsing: %s", exc)
        return error_response(
            code="INTERNAL_ERROR",
            message="An unexpected server error occurred.",
            detail="",
            status=500,
        )


@app.post(
    "/parse/bulk",
    tags=["Parsing"],
    summary="Parse multiple GST invoices (batch)",
    description=(
        "Upload up to **N** invoice files in a single request. "
        "Each file is processed independently — one failure never blocks the others.\n\n"
        "The response always returns HTTP 200 with a per-file `success` / `error` breakdown.\n\n"
        "**language** applies to all files in the batch."
    ),
    responses={
        200: {
            "description": "Batch completed. Check each result's `success` field.",
            "content": {
                "application/json": {
                    "example": {
                        "total": 2,
                        "successful": 1,
                        "failed": 1,
                        "results": [
                            {
                                "filename": "invoice1.pdf",
                                "success": True,
                                "data": _EXAMPLE_INVOICE_DATA,
                                "error": None,
                            },
                            {
                                "filename": "bad_file.docx",
                                "success": False,
                                "data": None,
                                "error": {
                                    "code": "UNSUPPORTED_TYPE",
                                    "message": "Unsupported file type '.docx'",
                                    "detail": "Only PDF, JPG, and PNG files are accepted.",
                                },
                            },
                        ],
                    }
                }
            },
        }
    },
)
async def parse_bulk(
    request: Request,
    files: List[UploadFile] = File(..., description="One or more GST invoice files — PDF, JPG, or PNG."),
    language: str = Form(default="en", description="Invoice language: `en` (English) or `hi` (Hindi)."),
    x_rapidapi_proxy_secret: str | None = Header(
        default=None,
        description="RapidAPI proxy secret. Leave blank for local / direct API use.",
    ),
    _rl: None = Depends(_rate_limit),
):
    _check_rapidapi(x_rapidapi_proxy_secret)

    if len(files) > MAX_BATCH_FILES:
        return error_response(
            code="TOO_MANY_FILES",
            message=f"Batch limit is {MAX_BATCH_FILES} files per request.",
            detail=f"Received {len(files)} files.",
            status=400,
        )

    if language not in ("en", "hi"):
        language = "en"

    results = []
    for upload in files:
        filename = upload.filename or "upload.pdf"
        content_type = upload.content_type or "application/octet-stream"
        try:
            file_bytes = await upload.read()
            invoice_data = parse_invoice(
                filename=filename,
                content_type=content_type,
                file_bytes=file_bytes,
                language=language,
            )
            results.append({"filename": filename, "success": True, "data": invoice_data, "error": None})
        except (ValueError, FileNotFoundError) as exc:
            logger.warning("Bulk parse validation error [%s]: %s", filename, exc)
            error = _parse_error_str(str(exc))
            results.append({"filename": filename, "success": False, "data": None, "error": error})
        except RuntimeError as exc:
            logger.error("Bulk parse runtime error [%s]: %s", filename, exc)
            error = _parse_error_str(str(exc))
            results.append({"filename": filename, "success": False, "data": None, "error": error})
        except Exception as exc:
            logger.exception("Bulk parse unexpected error [%s]: %s", filename, exc)
            results.append({
                "filename": filename,
                "success": False,
                "data": None,
                "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred.", "detail": ""},
            })

    successful = sum(1 for r in results if r["success"])
    return JSONResponse(status_code=200, content={
        "total": len(results),
        "successful": successful,
        "failed": len(results) - successful,
        "results": results,
    })


@app.get(
    "/dashboard",
    tags=["System"],
    summary="Usage & cost analytics",
    description="Returns today's call counts, AI costs, revenue, and profit. Requires `x-internal-secret` header when `INTERNAL_SECRET` is set.",
    responses={
        200: {
            "description": "Today's usage summary.",
            "content": {
                "application/json": {
                    "example": {
                        "date": "2024-11-14",
                        "total_calls": 42,
                        "successful_calls": 40,
                        "failed_calls": 2,
                        "success_rate": 95.24,
                        "total_cost_usd": 0.000182,
                        "total_revenue_usd": 3.36,
                        "total_profit_usd": 3.3598,
                        "on_track_for_daily_target": True,
                        "projected_daily_profit": 6.4,
                    }
                }
            },
        }
    },
)
async def dashboard(
    x_internal_secret: str | None = Header(
        default=None,
        description="Internal access secret. Only required when `INTERNAL_SECRET` env var is set.",
    ),
):
    _check_internal(x_internal_secret)
    summary = get_daily_summary()
    total = summary["total_calls"]
    successful = summary["successful_calls"]
    failed = total - successful
    success_rate = round(successful / total * 100, 2) if total > 0 else 0.0
    revenue_target = float(os.environ.get("REVENUE_PER_CALL_USD", "0.08")) * 80
    projected = (
        summary["total_profit_usd"] / total * 80
        if total > 0
        else 0
    )
    return {
        **summary,
        "failed_calls": failed,
        "success_rate": success_rate,
        "on_track_for_daily_target": summary["total_profit_usd"] >= revenue_target * 0.5,
        "projected_daily_profit": round(projected, 4),
    }


# ── error dispatch ────────────────────────────────────────────

_STATUS_MAP = {
    "INVALID_FORMAT": 400,
    "FILE_TOO_LARGE": 400,
    "UNSUPPORTED_TYPE": 400,
    "EXTRACTION_FAILED": 422,
    "MISSING_GSTIN": 422,
    "RATE_LIMITED": 429,
    "INTERNAL_ERROR": 500,
}


def _parse_error_str(message: str) -> dict:
    parts = message.split("|", 2)
    if len(parts) == 3:
        return {"code": parts[0], "message": parts[1], "detail": parts[2]}
    if len(parts) == 2:
        return {"code": parts[0], "message": parts[1], "detail": ""}
    return {"code": "INTERNAL_ERROR", "message": message, "detail": ""}


def _dispatch_error(message: str, default_status: int = 400) -> JSONResponse:
    parts = message.split("|", 2)
    if len(parts) == 3:
        code, msg, detail = parts
    elif len(parts) == 2:
        code, msg, detail = parts[0], parts[1], ""
    else:
        code, msg, detail = "INTERNAL_ERROR", message, ""

    status = _STATUS_MAP.get(code, default_status)
    return error_response(code=code, message=msg, detail=detail, status=status)
