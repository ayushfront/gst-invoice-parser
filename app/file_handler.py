import io
import logging
from typing import Tuple

import pdfplumber
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

SUPPORTED_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/png": "image",
}

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_OCR_CHARS = 50


def validate_file(filename: str, content_type: str, file_bytes: bytes) -> Tuple[str, str]:
    """
    Validate the uploaded file.
    Returns (file_type, detected_category) where file_type is 'pdf' or 'image'.
    Raises ValueError with error code embedded in message for each failure.
    """
    if len(file_bytes) == 0:
        raise ValueError("INVALID_FORMAT|Empty file uploaded|The uploaded file has no content.")

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"UNSUPPORTED_TYPE|Unsupported file type '{ext}'|Only PDF, JPG, and PNG files are accepted."
        )

    # Determine actual MIME type category; trust content_type first
    category = SUPPORTED_MIME_TYPES.get(content_type)

    # If content_type mismatch (e.g. extension says pdf but MIME is image), trust MIME
    if category is None:
        # Fall back to extension
        if ext == ".pdf":
            category = "pdf"
        else:
            raise ValueError(
                "UNSUPPORTED_TYPE|Unsupported file type|Only PDF, JPG, and PNG files are accepted."
            )

    # If extension says pdf but actual MIME is image, process as image
    if ext == ".pdf" and category == "image":
        category = "image"

    size = len(file_bytes)
    if category == "pdf" and size > MAX_PDF_SIZE_BYTES:
        raise ValueError(
            f"FILE_TOO_LARGE|File too large ({size // (1024*1024)}MB). Maximum allowed for PDF is 10MB.|"
        )
    if category == "image" and size > MAX_IMAGE_SIZE_BYTES:
        raise ValueError(
            f"FILE_TOO_LARGE|File too large ({size // (1024*1024)}MB). Maximum allowed for images is 5MB.|"
        )

    return category


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, int]:
    """Extract text from PDF bytes. Returns (text, page_count)."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = pdf.pages
            page_count = len(pages)
            text = "\n".join(page.extract_text() or "" for page in pages)
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg:
            raise ValueError(
                "INVALID_FORMAT|PDF is password protected|Please provide an unprotected PDF."
            )
        raise ValueError(
            f"INVALID_FORMAT|PDF file is corrupted or unreadable|{str(e)}"
        )

    # If pdfplumber returned no text, try OCR (scanned PDF)
    if not text.strip():
        text, page_count = _ocr_pdf(file_bytes, page_count)

    return text, page_count


def _ocr_pdf(file_bytes: bytes, page_count: int) -> Tuple[str, int]:
    """Run OCR on each page of a scanned PDF."""
    try:
        from pdf2image import convert_from_bytes  # optional dep only needed for scanned PDFs
        images = convert_from_bytes(file_bytes)
        texts = []
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            texts.append(pytesseract.image_to_string(Image.open(io.BytesIO(buf.getvalue())), lang="eng+hin"))
        return "\n".join(texts), len(images)
    except ImportError:
        # pdf2image not installed; return empty so caller can handle
        return "", page_count
    except Exception as e:
        raise ValueError(f"INVALID_FORMAT|Could not extract text from scanned PDF|{str(e)}")


def extract_text_from_image(file_bytes: bytes) -> Tuple[str, int]:
    """Extract text from an image using OCR. Returns (text, 1)."""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image, lang="eng+hin")
    except Exception as e:
        raise ValueError(f"INVALID_FORMAT|Could not read image file|{str(e)}")

    if len(text.strip()) < MIN_OCR_CHARS:
        raise ValueError(
            "EXTRACTION_FAILED|Image quality too low|The image does not contain enough readable text. "
            "Please upload a clearer photo."
        )

    return text, 1


def extract_text(category: str, file_bytes: bytes) -> Tuple[str, int]:
    """Dispatch to the correct extractor. Returns (text, page_count)."""
    if category == "pdf":
        return extract_text_from_pdf(file_bytes)
    return extract_text_from_image(file_bytes)
