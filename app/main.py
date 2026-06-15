import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from typing import List
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()

from app.cost_tracker import get_daily_summary
from app.errors import error_response
from app.parser import parse_invoice

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GST Invoice Parser",
    description="Extract structured data from Indian GST invoices using AI.",
    version="1.0.0",
)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Auth helpers — read env at request time so tests can override with monkeypatch
# ---------------------------------------------------------------------------

def _check_rapidapi(secret: str | None) -> None:
    required = os.environ.get("RAPIDAPI_PROXY_SECRET", "")
    if not required or required == "dev":
        return  # local / dev mode — no header needed
    if secret != required:
        raise HTTPException(status_code=403, detail="Forbidden")


def _check_internal(secret: str | None) -> None:
    required = os.environ.get("INTERNAL_SECRET", "")
    if required and secret != required:
        raise HTTPException(status_code=403, detail="Forbidden")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION}


@app.post("/parse")
async def parse(
    file: UploadFile = File(...),
    language: str = Form(default="en"),
    x_rapidapi_proxy_secret: str | None = Header(default=None),
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


@app.post("/parse/bulk")
async def parse_bulk(
    files: List[UploadFile] = File(...),
    language: str = Form(default="en"),
    x_rapidapi_proxy_secret: str | None = Header(default=None),
):
    _check_rapidapi(x_rapidapi_proxy_secret)

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


@app.get("/dashboard")
async def dashboard(x_internal_secret: str | None = Header(default=None)):
    _check_internal(x_internal_secret)
    summary = get_daily_summary()
    revenue_target = float(os.environ.get("REVENUE_PER_CALL_USD", "0.08")) * 80
    projected = (
        summary["total_profit_usd"] / summary["total_calls"] * 80
        if summary["total_calls"] > 0
        else 0
    )
    return {
        **summary,
        "on_track_for_daily_target": summary["total_profit_usd"] >= revenue_target * 0.5,
        "projected_daily_profit": round(projected, 4),
    }


# ---------------------------------------------------------------------------
# Error dispatch
# ---------------------------------------------------------------------------

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
    """Convert 'CODE|message|detail' string into an error dict for bulk results."""
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
