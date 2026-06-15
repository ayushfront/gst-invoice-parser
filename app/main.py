import logging
import os

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.errors import error_response
from app.parser import parse_invoice

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GST Invoice Parser",
    description="Extract structured data from Indian GST invoices using Claude AI.",
    version="1.0.0",
)

VERSION = "1.0.0"
RAPIDAPI_PROXY_SECRET = os.getenv("RAPIDAPI_PROXY_SECRET", "")


def _verify_rapidapi(secret: str | None) -> None:
    """Raise 403 if the RapidAPI proxy secret header is missing or wrong."""
    if not RAPIDAPI_PROXY_SECRET:
        # Not configured — running in local dev mode, skip check
        return
    if secret != RAPIDAPI_PROXY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION}


@app.post("/parse")
async def parse(
    file: UploadFile = File(...),
    language: str = Form(default="en"),
    x_rapidapi_proxy_secret: str | None = Header(default=None),
):
    _verify_rapidapi(x_rapidapi_proxy_secret)

    if language not in ("en", "hi"):
        language = "en"

    file_bytes = await file.read()
    filename = file.filename or "upload"
    content_type = file.content_type or ""

    try:
        invoice_data = parse_invoice(
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
            language=language,
        )
        return JSONResponse(
            status_code=200,
            content={"success": True, "data": invoice_data},
        )

    except ValueError as e:
        return _handle_known_error(str(e))

    except RuntimeError as e:
        return _handle_known_error(str(e), default_status=500)

    except Exception as e:
        logger.exception("Unexpected error during invoice parsing")
        return error_response(
            code="INTERNAL_ERROR",
            message="An unexpected server error occurred.",
            detail="",
            status=500,
        )


def _handle_known_error(message: str, default_status: int = 400) -> JSONResponse:
    """
    Errors raised by our pipeline are encoded as 'CODE|message|detail'.
    Parse them and return the correct HTTP response.
    """
    parts = message.split("|", 2)
    if len(parts) == 3:
        code, msg, detail = parts
    elif len(parts) == 2:
        code, msg, detail = parts[0], parts[1], ""
    else:
        code, msg, detail = "INTERNAL_ERROR", message, ""

    status_map = {
        "INVALID_FORMAT": 400,
        "FILE_TOO_LARGE": 400,
        "UNSUPPORTED_TYPE": 400,
        "EXTRACTION_FAILED": 422,
        "MISSING_GSTIN": 422,
        "INTERNAL_ERROR": 500,
    }
    status = status_map.get(code, default_status)
    return error_response(code=code, message=msg, detail=detail, status=status)
