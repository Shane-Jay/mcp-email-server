from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import EmbeddedResource, TextContent

from mcp_email_server.app import read_attachment
from mcp_email_server.application.limits import APPLICATION_LIMITS
from mcp_email_server.application.reads import AttachmentPayload, ReadAccountSnapshot, ReadProviderAccess
from mcp_email_server.attachment_extract import extract_attachment_text
from mcp_email_server.attachment_read import (
    AttachmentTextService,
    ReadAttachmentQuery,
    build_attachment_read_result,
)


def _pdf(pages: list[str]) -> bytes:
    """Build a tiny PDF whose text operators pypdf can extract."""

    objects: list[bytes] = []
    font_id = 1
    next_id = 2
    content_ids: list[int] = []
    page_ids: list[int] = []
    for _page in pages:
        content_ids.append(next_id)
        page_ids.append(next_id + 1)
        next_id += 2
    pages_id = next_id
    catalog_id = next_id + 1
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for text, content_id in zip(pages, content_ids, strict=True):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 24 Tf 72 72 Td ({escaped}) Tj ET".encode("ascii")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects.append(
            (
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 300 144] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode("ascii")
        )
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    objects.append(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    del catalog_id
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in chunks))
        chunks.append(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = sum(len(part) for part in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n".encode(), b"0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    trailer = f"trailer<< /Size {len(objects) + 1} /Root {len(objects)} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return b"".join(chunks) + b"".join(xref) + trailer


def _xml_zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    blocks = [f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs]
    if table:
        rows = []
        for row in table:
            cells = "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row)
            rows.append(f"<w:tr>{cells}</w:tr>")
        blocks.append(f"<w:tbl>{''.join(rows)}</w:tbl>")
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(blocks)}</w:body></w:document>"
    )
    return _xml_zip({"word/document.xml": document})


def _pptx() -> bytes:
    slide = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Slide text</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>"
        "</p:sld>"
    )
    notes = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Speaker note</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>"
        "</p:notes>"
    )
    return _xml_zip({"ppt/slides/slide1.xml": slide, "ppt/notesSlides/notesSlide1.xml": notes})


def _xlsx() -> bytes:
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Name</t></si></sst>'
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1"><v>2</v></c></row>'
        "</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    return _xml_zip({
        "xl/sharedStrings.xml": shared,
        "xl/worksheets/sheet1.xml": sheet,
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": rels,
    })


def test_pdf_pages_docx_pptx_xlsx_and_gbk_text() -> None:
    pdf = extract_attachment_text(_pdf(["Hello", "World"]), "application/pdf", "note.pdf")
    assert "--- page 1 ---" in pdf.text
    assert "Hello" in pdf.text
    assert "--- page 2 ---" in pdf.text
    assert "World" in pdf.text
    docx = extract_attachment_text(_docx(["Intro"], [["A", "B"]]), "application/octet-stream", "file.docx")
    assert "Intro" in docx.text
    assert "A\tB" in docx.text
    pptx = extract_attachment_text(_pptx(), "application/octet-stream", "deck.pptx")
    assert "--- slide 1 ---" in pptx.text
    assert "Slide text" in pptx.text
    assert "[notes]" in pptx.text
    assert "Speaker note" in pptx.text
    xlsx = extract_attachment_text(_xlsx(), "application/octet-stream", "book.xlsx")
    assert "--- sheet Data ---" in xlsx.text
    assert "Name\t\t2" in xlsx.text
    gbk = extract_attachment_text("中文附件".encode("gb18030"), "text/plain", "note.txt")
    assert gbk.text == "中文附件"
    assert gbk.notes == ("charset:gb18030",)
    html = extract_attachment_text(
        b"<html><body><p>Hello</p><script>ignore-me</script></body></html>",
        "text/html",
        "page.html",
    )
    assert "Hello" in html.text
    assert "ignore-me" not in html.text
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = "Nested"
    message.set_content("Plain body")
    message.add_attachment(b"xyz", maintype="application", subtype="pdf", filename="inner.pdf")
    eml = extract_attachment_text(message.as_bytes(), "message/rfc822", "note.eml")
    assert "Plain body" in eml.text
    assert "nested_attachment" in eml.notes


def test_empty_pdf_reports_no_text_layer_and_images_are_unsupported() -> None:
    empty = extract_attachment_text(_pdf([""]), "application/pdf", "scan.pdf")
    assert empty.text == ""
    assert "no_text_layer" in empty.notes
    image = extract_attachment_text(b"\x89PNG\r\n\x1a\n", "image/png", "pic.png")
    assert image.supported is False
    assert image.notes == ("unsupported_type",)


def test_zip_bomb_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * (1024 * 1024))
    with pytest.raises(ValueError, match="compression ratio"):
        extract_attachment_text(buffer.getvalue(), "application/octet-stream", "bomb.docx")


@pytest.mark.asyncio
async def test_text_paging_and_base64_chunk_round_trip() -> None:
    content = b"abcdefghijklmnopqrstuvwxyz"
    text = await build_attachment_read_result(
        filename="note.txt",
        mime_type="text/plain",
        content=content,
        requested_format="text",
        offset=4,
        length=5,
    )
    assert text["format"] == "text"
    assert text["text"] == "efghi"
    assert text["offset"] == 4
    assert text["next_offset"] == 9
    assert text["truncated"] is True
    assert text["total_chars"] == len(content)
    assert text["sha256"] == hashlib.sha256(content).hexdigest()
    chunk = await build_attachment_read_result(
        filename="note.bin",
        mime_type="application/octet-stream",
        content=content,
        requested_format="base64",
        offset=2,
        length=4,
    )
    raw = base64.b64decode(str(chunk["content_base64"]))
    assert raw == content[2:6]
    assert chunk["chunk_sha256"] == hashlib.sha256(raw).hexdigest()
    assert chunk["sha256"] == hashlib.sha256(content).hexdigest()
    assert chunk["next_offset"] == 6
    assert chunk["total_bytes"] == len(content)
    info = await build_attachment_read_result(
        filename="note.bin",
        mime_type="application/octet-stream",
        content=content,
        requested_format="info",
        offset=0,
        length=None,
    )
    assert info["format"] == "info"
    assert "content_base64" not in info
    assert "text" not in info


@pytest.mark.asyncio
async def test_auto_empty_pdf_points_at_base64() -> None:
    result = await build_attachment_read_result(
        filename="scan.pdf",
        mime_type="application/pdf",
        content=_pdf([""]),
        requested_format="auto",
        offset=0,
        length=None,
    )
    assert result["format"] == "info"
    assert "no_text_layer" in result["notes"]
    assert "format=base64" in str(result["message"])


@pytest.mark.asyncio
async def test_max_base64_chunk_fits_under_the_response_ceiling() -> None:
    content = b"x" * (4 * 1024 * 1024)
    result = await build_attachment_read_result(
        filename="blob.bin",
        mime_type="application/octet-stream",
        content=content,
        requested_format="base64",
        offset=0,
        length=4 * 1024 * 1024,
    )
    encoded = json.dumps(result).encode("utf-8")
    assert len(encoded) < APPLICATION_LIMITS.serialized_response_bytes


def _account(*, read: bool = False, content: bool = False) -> ReadAccountSnapshot:
    return ReadAccountSnapshot(
        account_name="work",
        mode="legacy",
        allowed_senders=(),
        enable_attachment_download=False,
        enable_attachment_content=content,
        enable_attachment_read=read,
    )


@pytest.mark.asyncio
async def test_read_flag_gates_before_fetch_and_content_flag_enables_it() -> None:
    authority = Mock()
    authority.resolve.return_value = _account()
    factory = Mock()
    service = AttachmentTextService(authority, factory)
    with pytest.raises(PermissionError, match="ENABLE_ATTACHMENT_READ"):
        await service.execute(ReadAttachmentQuery("work", "1", "a.txt"))
    factory.open.assert_not_called()

    payload = AttachmentPayload("1", "a.txt", "text/plain", b"hello")
    provider = Mock()
    provider.fetch_attachment = AsyncMock(return_value=payload)
    authority.resolve.return_value = _account(content=True)
    factory.open.return_value = ReadProviderAccess(_account(content=True), provider)
    result = await service.execute(ReadAttachmentQuery("work", "1", "a.txt", format="text"))
    assert result["text"] == "hello"
    provider.fetch_attachment.assert_awaited()


@pytest.mark.asyncio
async def test_tool_result_is_text_content_only() -> None:
    runtime = Mock()
    runtime.reads.attachment_text.execute = AsyncMock(
        return_value={"filename": "a.txt", "mime_type": "text/plain", "size": 1, "sha256": "ab", "format": "info"}
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("mcp_email_server.app.get_application_runtime", lambda: runtime)
        result = await read_attachment(account_name="work", email_id="1", attachment_name="a.txt", format="info")
    assert result.content
    assert all(isinstance(item, TextContent) for item in result.content)
    assert not any(isinstance(item, EmbeddedResource) for item in result.content)
    assert json.loads(result.content[0].text)["format"] == "info"


@pytest.mark.asyncio
async def test_extraction_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _slow(*_args: object, **_kwargs: object) -> None:
        import time

        time.sleep(0.2)

    monkeypatch.setattr("mcp_email_server.attachment_read.extraction_timeout_seconds", lambda: 0.05)
    monkeypatch.setattr("mcp_email_server.attachment_read.extract_attachment_text", _slow)
    with pytest.raises(ValueError, match="timed out"):
        await build_attachment_read_result(
            filename="a.txt",
            mime_type="text/plain",
            content=b"hello",
            requested_format="text",
            offset=0,
            length=10,
        )
