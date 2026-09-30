"""Uploaded file -> plain text. PDF (pypdf), DOCX (python-docx), TXT (encoding detection).

Type allowlist is enforced by CONTENT SNIFFING plus extension: a file is accepted only if
its bytes look like the type its extension claims. Nothing is ever executed; parsers only
read. OCR is out of scope: image-only PDFs fail with a clear "no extractable text" error.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}
EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt"}

MAX_PDF_PAGES = 500
# DOCX is a zip: refuse archives that would expand to more than this (zip bombs).
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MIN_TEXT_CHARS = 20


class UnsupportedFileType(Exception):
    """Extension not allowed, or content does not match the extension (-> 415)."""


class ParseError(Exception):
    """File is of an allowed type but its text cannot be extracted (-> document failed).

    The message is shown to the user, so it must be short and contain no internals.
    """


@dataclass(frozen=True)
class DetectedType:
    kind: str  # pdf | docx | txt

    @property
    def mime_type(self) -> str:
        return MIME_TYPES[self.kind]


def safe_filename(name: str | None) -> str:
    """Basename only, no control characters, bounded length."""
    base = PurePath((name or "").replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip() or "document"
    return base[-255:]


def _looks_like_pdf(data: bytes) -> bool:
    # The spec allows the header anywhere in the first 1024 bytes.
    return b"%PDF-" in data[:1024]


def _looks_like_docx(data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return False
    return "word/document.xml" in names and "[Content_Types].xml" in names


def _looks_like_text(data: bytes) -> bool:
    head = data[:8192]
    if b"\x00" in head or _looks_like_pdf(head) or head.startswith(b"PK\x03\x04"):
        return False
    return _decode_text(data) is not None


def detect_type(filename: str, data: bytes) -> DetectedType:
    kind = EXTENSIONS.get(PurePath(filename).suffix.lower())
    if kind is None:
        raise UnsupportedFileType("Only PDF, DOCX and TXT files are supported")
    checks = {"pdf": _looks_like_pdf, "docx": _looks_like_docx, "txt": _looks_like_text}
    if not checks[kind](data):
        raise UnsupportedFileType(f"The file content is not a valid {kind.upper()} file")
    return DetectedType(kind)


# --------------------------------------------------------------------------- extraction


def _decode_text(data: bytes) -> str | None:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    from charset_normalizer import from_bytes

    best = from_bytes(data).best()
    return str(best) if best is not None else None


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("Password-protected PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ParseError(f"PDF has more than {MAX_PDF_PAGES} pages")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ParseError("The PDF could not be read (damaged or unsupported)") from exc


def _docx_text(data: bytes) -> str:
    import docx

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if sum(i.file_size for i in zf.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise ParseError("The DOCX file is too large when uncompressed")
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of lxml/zip errors
        raise ParseError("The DOCX could not be read (damaged or unsupported)") from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _normalise(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(kind: str, data: bytes) -> str:
    if kind == "pdf":
        text = _pdf_text(data)
    elif kind == "docx":
        text = _docx_text(data)
    elif kind == "txt":
        decoded = _decode_text(data)
        if decoded is None:
            raise ParseError("The text file encoding could not be detected")
        text = decoded
    else:
        raise UnsupportedFileType(kind)
    text = _normalise(text)
    if len(text) < MIN_TEXT_CHARS:
        hint = " (it may be a scanned image; OCR is not supported)" if kind == "pdf" else ""
        raise ParseError(f"No extractable text found in this file{hint}")
    return text
