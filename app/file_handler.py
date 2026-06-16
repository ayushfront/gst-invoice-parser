import io
import logging
from typing import Tuple

import pdfplumber
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

# First bytes that identify each real file format
_MAGIC: dict[str, list[bytes]] = {
    "pdf":   [b"%PDF"],
    "image": [b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF8"],
}

MIME_TO_CATEGORY = {
    "application/pdf": "pdf",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/png": "image",
}

MAX_PDF_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_OCR_CHARS  = 50
MAX_PDF_PAGES  = 50


class FileValidationError(ValueError):
    """Raised for all file-level validation failures. message = 'CODE|msg|detail'"""


def validate_file(filename: str, content_type: str, file_bytes: bytes) -> str:
    """
    Validate uploaded file. Returns category: 'pdf' or 'image'.
    Raises FileValidationError with 'CODE|message|detail' on failure.
    """
    # Empty file check
    if not file_bytes:
        raise FileValidationError("INVALID_FORMAT|Empty file uploaded|The uploaded file contains no data.")

    # Extension check
    dot_pos = filename.rfind(".")
    ext = filename[dot_pos:].lower() if dot_pos != -1 else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise FileValidationError(
            f"UNSUPPORTED_TYPE|Unsupported file type '{ext}'|Only PDF, JPG, and PNG files are accepted."
        )

    # Determine category from actual MIME type (trust MIME over extension)
    category = MIME_TO_CATEGORY.get(content_type)
    if category is None:
        # MIME not recognised — fall back to extension
        if ext == ".pdf":
            category = "pdf"
        else:
            raise FileValidationError(
                "UNSUPPORTED_TYPE|Unsupported file type|Only PDF, JPG, and PNG files are accepted."
            )

    # If extension says .pdf but MIME says image, process as image (per edge cases spec)
    if ext == ".pdf" and category == "image":
        category = "image"

    # Magic bytes — verify actual content matches claimed type
    if not any(file_bytes.startswith(sig) for sig in _MAGIC[category]):
        raise FileValidationError(
            f"INVALID_FORMAT|File content does not match its extension '{ext}'|"
            "The file appears to be corrupted or misidentified."
        )

    # Size checks
    size = len(file_bytes)
    if category == "pdf" and size > MAX_PDF_BYTES:
        raise FileValidationError(
            f"FILE_TOO_LARGE|File size {size // (1024 * 1024)}MB exceeds the 10MB limit for PDFs.|"
        )
    if category == "image" and size > MAX_IMAGE_BYTES:
        raise FileValidationError(
            f"FILE_TOO_LARGE|File size {size // (1024 * 1024)}MB exceeds the 5MB limit for images.|"
        )

    return category


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, int]:
    """Extract text from PDF bytes. Returns (text, page_count)."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_count = len(pdf.pages)
            if page_count > MAX_PDF_PAGES:
                raise FileValidationError(
                    f"INVALID_FORMAT|PDF has {page_count} pages — maximum is {MAX_PDF_PAGES}|"
                    "Please upload individual invoice pages rather than a large document."
                )
            pages_text = [page.extract_text() or "" for page in pdf.pages]
            text = "\n".join(pages_text)
    except Exception as exc:
        msg = str(exc).lower()
        if "password" in msg or "encrypt" in msg:
            raise FileValidationError(
                "INVALID_FORMAT|PDF is password protected|Remove the password and upload again."
            )
        raise FileValidationError(
            f"INVALID_FORMAT|PDF file is corrupted or unreadable|{exc}"
        )

    # Scanned PDF fallback: pdfplumber found no text layer
    if not text.strip():
        text, page_count = _ocr_scanned_pdf(file_bytes, page_count)

    return text, page_count


def _ocr_scanned_pdf(file_bytes: bytes, page_count: int) -> Tuple[str, int]:
    """Run OCR on a scanned (image-only) PDF using pdf2image + pytesseract."""
    try:
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(file_bytes)
        texts = []
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            texts.append(pytesseract.image_to_string(Image.open(io.BytesIO(buf.getvalue())), lang="eng+hin"))
        return "\n".join(texts), len(images)
    except ImportError:
        # pdf2image not installed; return empty string so caller handles it
        logger.warning("pdf2image not installed — cannot OCR scanned PDFs")
        return "", page_count
    except Exception as exc:
        raise FileValidationError(f"INVALID_FORMAT|Could not OCR scanned PDF|{exc}")


def extract_text_from_image(file_bytes: bytes) -> Tuple[str, int]:
    """Extract text from image bytes via OCR. Returns (text, 1)."""
    try:
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image, lang="eng+hin")
    except Exception as exc:
        raise FileValidationError(f"INVALID_FORMAT|Could not read image file|{exc}")

    if len(text.strip()) < MIN_OCR_CHARS:
        raise FileValidationError(
            "EXTRACTION_FAILED|Image quality too low|"
            "The image does not contain enough readable text. Please upload a clearer photo."
        )

    return text, 1


def extract_text(category: str, file_bytes: bytes) -> Tuple[str, int]:
    """Dispatch to correct extractor. Returns (text, page_count)."""
    if category == "pdf":
        return extract_text_from_pdf(file_bytes)
    return extract_text_from_image(file_bytes)
