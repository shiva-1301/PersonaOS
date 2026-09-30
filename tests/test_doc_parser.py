import io
import zipfile

import pytest

from app.services import doc_parser
from app.services.doc_parser import (
    ParseError,
    UnsupportedFileType,
    detect_type,
    extract_text,
    safe_filename,
)
from tests.doc_factory import (
    EXE_BYTES,
    PNG_BYTES,
    make_blank_pdf,
    make_docx,
    make_encrypted_pdf,
    make_pdf,
)


def parse(name: str, data: bytes) -> str:
    return extract_text(detect_type(name, data).kind, data)


# --------------------------------------------------------------------------- happy paths


def test_pdf_text_extracted():
    text = parse("notes.pdf", make_pdf(["Gradient descent (GD) minimises loss.", "Line two."]))
    assert "Gradient descent (GD) minimises loss." in text
    assert "Line two." in text
    assert detect_type("notes.pdf", make_pdf(["x"])).mime_type == "application/pdf"


def test_docx_paragraphs_and_tables_extracted():
    data = make_docx(
        ["Decision trees split on features.", "Entropy measures impurity."],
        table=[["Term", "Meaning"], ["Gini", "impurity index"]],
    )
    text = parse("Trees.DOCX", data)
    assert "Decision trees split on features." in text
    assert "Gini | impurity index" in text


@pytest.mark.parametrize(
    "raw",
    [
        "Linear regression fits a line. Café naïve résumé".encode(),
        "\ufeffBOM-prefixed UTF-8 notes about regression".encode(),
        "Latin-1 notes: café crème brûlée and regression".encode("latin-1"),
    ],
)
def test_txt_encodings(raw):
    text = parse("notes.txt", raw)
    assert "regression" in text
    assert not text.startswith("\ufeff")


def test_whitespace_normalised():
    # extract_text directly: a NUL byte would (correctly) fail type sniffing for .txt.
    text = extract_text("txt", b"Line one   \r\n\r\n\r\n\r\nLine two with enough text\x00")
    assert text == "Line one\n\nLine two with enough text"


# --------------------------------------------------------------------------- type allowlist


@pytest.mark.parametrize(
    "name,data",
    [
        ("notes.exe", EXE_BYTES),
        ("notes.doc", make_docx(["legacy word"])),
        ("notes.md", b"# markdown is not on the allowlist"),
        ("noextension", b"plain text without an extension"),
        ("image.pdf", PNG_BYTES),  # extension lies: content is PNG
        ("program.docx", EXE_BYTES),
        ("fake.txt", make_pdf(["a pdf renamed to .txt"])),
        ("fake.txt", b"text\x00with\x00nul bytes"),
        ("fake.pdf", b"just some text, not a pdf at all"),
    ],
    # Short ids: raw bytes in test ids overflow Windows' environment-variable limit.
    ids=[
        "exe",
        "doc-extension",
        "markdown",
        "no-extension",
        "png-as-pdf",
        "exe-as-docx",
        "pdf-as-txt",
        "binary-as-txt",
        "text-as-pdf",
    ],
)
def test_disallowed_or_mismatched_types_rejected(name, data):
    with pytest.raises(UnsupportedFileType):
        detect_type(name, data)


def test_zip_without_word_document_is_not_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "not a word document")
    with pytest.raises(UnsupportedFileType):
        detect_type("archive.docx", buf.getvalue())


# --------------------------------------------------------------------------- failures


def test_scanned_pdf_has_no_extractable_text():
    with pytest.raises(ParseError, match="No extractable text.*scanned"):
        parse("scan.pdf", make_blank_pdf())


def test_encrypted_pdf_rejected():
    with pytest.raises(ParseError, match="Password-protected"):
        parse("locked.pdf", make_encrypted_pdf())


def test_corrupt_pdf_is_parse_error():
    with pytest.raises(ParseError, match="could not be read"):
        parse("broken.pdf", b"%PDF-1.4\n garbage that is not a pdf body")


def test_docx_zip_bomb_guard(monkeypatch):
    monkeypatch.setattr(doc_parser, "MAX_DOCX_UNCOMPRESSED_BYTES", 1000)
    with pytest.raises(ParseError, match="too large when uncompressed"):
        parse("bomb.docx", make_docx(["x" * 5000]))


def test_too_little_text_fails():
    with pytest.raises(ParseError, match="No extractable text"):
        parse("tiny.txt", b"hi")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("notes.pdf", "notes.pdf"),
        ("C:\\Users\\me\\secret\\notes.pdf", "notes.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("bad\x00name\x1f.txt", "badname.txt"),
        ("", "document"),
        (None, "document"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected
