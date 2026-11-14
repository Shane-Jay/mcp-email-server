"""Extract readable text from attachment bytes without executing them.

PDF text uses ``pypdf``. Office Open XML uses the standard-library zip reader
and ``defusedxml`` (entities and DTDs are rejected). Plain text, HTML, and
``.eml`` use the standard library plus BeautifulSoup, which is already a
dependency. Legacy binary Office files, images, and other types are not
decoded. Extracted text is untrusted external content.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from bs4 import BeautifulSoup
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as parse_xml
from pypdf import PdfReader
from pypdf.errors import PdfReadError

_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_MAX_ZIP_ENTRIES = 2_000
_MAX_XML_BYTES = 20 * 1024 * 1024
_MAX_PDF_PAGES = 200
_MAX_SLIDES = 200
_MAX_SHEETS = 50
_MAX_CELLS = 50_000
_MAX_DOCX_BLOCKS = 5_000
_MAX_EXTRACTED_CHARS = 1_000_000
_SLIDE_NAME = re.compile(r"ppt/slides/slide(\d+)\.xml\Z")
_UNSAFE_ZIP_PARTS = frozenset({".", ".."})


@dataclass(frozen=True)
class ExtractedText:
    """Text pulled from one attachment, plus machine-readable notes."""

    text: str
    notes: tuple[str, ...]
    supported: bool


class _TextBuffer:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self.capped = False
        self._chars = 0

    def add(self, text: str) -> None:
        if self.capped or not text:
            return
        room = _MAX_EXTRACTED_CHARS - self._chars
        if len(text) > room:
            self._parts.append(text[:room])
            self._chars += room
            self.capped = True
            return
        self._parts.append(text)
        self._chars += len(text)

    def finish(self) -> str:
        return "".join(self._parts)


def extract_attachment_text(content: bytes, mime_type: str, filename: str) -> ExtractedText:
    """Return extracted text, or a note when this type has no text layer."""

    kind = _classify(mime_type, filename)
    if kind == "pdf":
        return _extract_pdf(content)
    if kind == "docx":
        return _extract_docx(content)
    if kind == "pptx":
        return _extract_pptx(content)
    if kind == "xlsx":
        return _extract_xlsx(content)
    if kind == "html":
        return _extract_html(content)
    if kind == "eml":
        return _extract_eml(content)
    if kind == "text":
        text, encoding = _decode_text_bytes(content)
        notes = ("charset:" + encoding,)
        if len(text) > _MAX_EXTRACTED_CHARS:
            text = text[:_MAX_EXTRACTED_CHARS]
            notes = (*notes, "truncated_extraction")
        return ExtractedText(text, notes, True)
    return ExtractedText("", ("unsupported_type",), False)


def _classify(mime_type: str, filename: str) -> str:
    mime = mime_type.split(";", 1)[0].strip().casefold()
    extension = Path(filename).suffix.casefold()
    if mime in {"application/pdf", "application/x-pdf"} or extension == ".pdf":
        return "pdf"
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or extension == ".docx":
        return "docx"
    if mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation" or extension == ".pptx":
        return "pptx"
    if mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" or extension == ".xlsx":
        return "xlsx"
    if mime in {"text/html", "application/xhtml+xml"} or extension in {".html", ".htm"}:
        return "html"
    if mime in {"message/rfc822"} or extension == ".eml":
        return "eml"
    if mime in {"text/plain", "text/csv", "text/markdown", "text/x-markdown"} or extension in {
        ".txt",
        ".csv",
        ".md",
        ".markdown",
    }:
        return "text"
    if mime.startswith("text/") and mime not in {"text/html"}:
        return "text"
    return "other"


def _decode_text_bytes(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig"), "utf-8"
    for encoding in ("utf-8", "gb18030"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1"), "latin-1"


def _extract_pdf(content: bytes) -> ExtractedText:
    try:
        reader = PdfReader(io.BytesIO(content))
    except (PdfReadError, ValueError, TypeError, OSError):
        return ExtractedText("", ("parse_failed",), True)
    if reader.is_encrypted:
        return ExtractedText("", ("no_text_layer", "encrypted_pdf"), True)
    buffer = _TextBuffer()
    notes: list[str] = []
    pages = reader.pages
    if len(pages) > _MAX_PDF_PAGES:
        notes.append("page_cap")
    visible = False
    for index, page in enumerate(pages[:_MAX_PDF_PAGES], start=1):
        try:
            extracted = page.extract_text() or ""
        except Exception:
            return ExtractedText("", ("parse_failed",), True)
        if extracted.strip():
            visible = True
        buffer.add(f"--- page {index} ---\n{extracted}\n")
        if buffer.capped:
            notes.append("truncated_extraction")
            break
    if not visible and "truncated_extraction" not in notes:
        return ExtractedText("", ("no_text_layer",), True)
    return ExtractedText(buffer.finish(), tuple(dict.fromkeys(notes)), True)


def _extract_html(content: bytes) -> ExtractedText:
    text_bytes, encoding = _decode_text_bytes(content)
    soup = BeautifulSoup(text_bytes, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    notes = [f"charset:{encoding}"]
    if len(text) > _MAX_EXTRACTED_CHARS:
        text = text[:_MAX_EXTRACTED_CHARS]
        notes.append("truncated_extraction")
    return ExtractedText(text, tuple(notes), True)


def _eml_part_text(part: EmailMessage) -> str | None:
    content_type = part.get_content_type()
    if content_type not in {"text/plain", "text/html"}:
        return None
    try:
        payload = part.get_content()
    except Exception:
        return None
    if not isinstance(payload, str):
        return None
    if content_type == "text/plain":
        return payload + "\n"
    soup = BeautifulSoup(payload, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n", strip=True) + "\n"


def _extract_eml(content: bytes) -> ExtractedText:
    try:
        message = BytesParser(policy=policy.default).parsebytes(content)
    except Exception:
        return ExtractedText("", ("parse_failed",), True)
    if not isinstance(message, EmailMessage):
        return ExtractedText("", ("parse_failed",), True)
    buffer = _TextBuffer()
    notes: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if not isinstance(part, EmailMessage):
            continue
        text = _eml_part_text(part)
        if text is None:
            if part.get_filename():
                notes.append("nested_attachment")
            elif part.get_content_type() in {"text/plain", "text/html"}:
                notes.append("parse_failed")
            continue
        buffer.add(text)
        if buffer.capped:
            notes.append("truncated_extraction")
            break
    return ExtractedText(buffer.finish(), tuple(dict.fromkeys(notes)), True)


def _extract_docx(content: bytes) -> ExtractedText:
    try:
        with _open_ooxml(content) as archive:
            document = _xml_root(_read_zip_member(archive, "word/document.xml"))
    except ValueError:
        raise
    except KeyError:
        return ExtractedText("", ("parse_failed",), True)
    body = _first_child(document, "body")
    if body is None:
        return ExtractedText("", ("parse_failed",), True)
    buffer = _TextBuffer()
    notes: list[str] = []
    blocks = 0
    for block in list(body):
        kind = _local(block.tag)
        if kind == "p":
            buffer.add(_descendant_text(block, "t") + "\n")
        elif kind == "tbl":
            buffer.add(_table_tsv(block))
        else:
            continue
        blocks += 1
        if blocks >= _MAX_DOCX_BLOCKS:
            notes.append("truncated_extraction")
            break
        if buffer.capped:
            notes.append("truncated_extraction")
            break
    return ExtractedText(buffer.finish(), tuple(notes), True)


def _extract_pptx(content: bytes) -> ExtractedText:
    try:
        with _open_ooxml(content) as archive:
            names = {info.filename for info in archive.infolist()}
            slides = sorted(
                (int(match.group(1)), name) for name in names if (match := _SLIDE_NAME.fullmatch(name)) is not None
            )
            if len(slides) > _MAX_SLIDES:
                slides = slides[:_MAX_SLIDES]
                capped = True
            else:
                capped = False
            buffer = _TextBuffer()
            for number, name in slides:
                root = _xml_root(_read_zip_member(archive, name))
                buffer.add(f"--- slide {number} ---\n{_descendant_text(root, 't')}\n")
                notes_name = f"ppt/notesSlides/notesSlide{number}.xml"
                if notes_name in names:
                    notes_root = _xml_root(_read_zip_member(archive, notes_name))
                    notes_text = _descendant_text(notes_root, "t").strip()
                    if notes_text:
                        buffer.add(f"[notes]\n{notes_text}\n")
                if buffer.capped:
                    capped = True
                    break
    except ValueError:
        raise
    except KeyError:
        return ExtractedText("", ("parse_failed",), True)
    notes = ("truncated_extraction",) if capped else ()
    return ExtractedText(buffer.finish(), notes, True)


def _extract_xlsx(content: bytes) -> ExtractedText:
    try:
        with _open_ooxml(content) as archive:
            names = {info.filename for info in archive.infolist()}
            shared = _shared_strings(archive, names)
            sheets = _workbook_sheets(archive, names)
            buffer = _TextBuffer()
            notes: list[str] = []
            cells = 0
            if len(sheets) > _MAX_SHEETS:
                sheets = sheets[:_MAX_SHEETS]
                notes.append("truncated_extraction")
            for sheet_name, target in sheets:
                if target not in names:
                    continue
                buffer.add(f"--- sheet {sheet_name} ---\n")
                root = _xml_root(_read_zip_member(archive, target))
                sheet_data = _first_child(root, "sheetData")
                for row in _children_named(root if sheet_data is None else sheet_data, "row"):
                    values, used = _row_values(row, shared)
                    cells += used
                    buffer.add("\t".join(values) + "\n")
                    if cells >= _MAX_CELLS or buffer.capped:
                        notes.append("truncated_extraction")
                        return ExtractedText(buffer.finish(), tuple(dict.fromkeys(notes)), True)
    except ValueError:
        raise
    except KeyError:
        return ExtractedText("", ("parse_failed",), True)
    return ExtractedText(buffer.finish(), tuple(dict.fromkeys(notes)), True)


def _shared_strings(archive: zipfile.ZipFile, names: set[str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = _xml_root(_read_zip_member(archive, "xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.iter():
        if _local(item.tag) != "si":
            continue
        values.append(_descendant_text(item, "t"))
    return values


def _workbook_sheets(archive: zipfile.ZipFile, names: set[str]) -> list[tuple[str, str]]:
    if "xl/workbook.xml" not in names:
        return []
    relations: dict[str, str] = {}
    rels_name = "xl/_rels/workbook.xml.rels"
    if rels_name in names:
        rels = _xml_root(_read_zip_member(archive, rels_name))
        for rel in rels.iter():
            if _local(rel.tag) != "Relationship":
                continue
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if isinstance(rel_id, str) and isinstance(target, str) and not target.startswith("/"):
                relations[rel_id] = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
    workbook = _xml_root(_read_zip_member(archive, "xl/workbook.xml"))
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.iter():
        if _local(sheet.tag) != "sheet":
            continue
        name = sheet.attrib.get("name") or "sheet"
        rel_id = next((value for key, value in sheet.attrib.items() if key.endswith("}id") or key == "id"), None)
        target = relations.get(rel_id or "")
        if target is None:
            continue
        sheets.append((name, target))
    return sheets


def _row_values(row: Element, shared: list[str]) -> tuple[list[str], int]:
    cells: list[tuple[int, str]] = []
    for cell in list(row):
        if _local(cell.tag) != "c":
            continue
        ref = cell.attrib.get("r") or ""
        cells.append((_column_index(ref), _cell_value(cell, shared)))
    if not cells:
        return [], 0
    cells.sort(key=lambda item: item[0])
    width = cells[-1][0]
    values = [""] * width
    for index, value in cells:
        if 1 <= index <= width:
            values[index - 1] = value
    return values, len(cells)


def _cell_value(cell: Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t") or ""
    if kind == "inlineStr":
        return _descendant_text(cell, "t")
    raw = ""
    for child in list(cell):
        if _local(child.tag) == "v" and child.text:
            raw = child.text
            break
    if kind == "s":
        try:
            index = int(raw)
        except ValueError:
            return ""
        if 0 <= index < len(shared):
            return shared[index]
        return ""
    return raw


def _column_index(ref: str) -> int:
    index = 0
    for char in ref:
        if not char.isalpha():
            break
        index = index * 26 + (ord(char.upper()) - 64)
    return index or 1


def _table_tsv(table: Element) -> str:
    lines: list[str] = []
    for row in list(table):
        if _local(row.tag) != "tr":
            continue
        cells = [_descendant_text(cell, "t") for cell in list(row) if _local(cell.tag) == "tc"]
        lines.append("\t".join(cells))
    return "\n".join(lines) + ("\n" if lines else "")


def _descendant_text(element: Element, local_name: str) -> str:
    return "".join(node.text or "" for node in element.iter() if _local(node.tag) == local_name)


def _first_child(element: Element, local_name: str) -> Element | None:
    for child in element.iter():
        if _local(child.tag) == local_name:
            return child
    return None


def _children_named(element: Element, local_name: str) -> Iterable[Element]:
    return [child for child in list(element) if _local(child.tag) == local_name]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _open_ooxml(content: bytes) -> zipfile.ZipFile:
    """Open an Office zip after rejecting bombs, encryption, and unsafe names."""

    if len(content) > _MAX_UNCOMPRESSED_BYTES:
        raise ValueError("office document exceeds the uncompressed size limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("attachment is not a valid Office document") from exc
    try:
        problem = _zip_bomb_reason(archive)
    except Exception:
        archive.close()
        raise
    if problem is not None:
        archive.close()
        raise ValueError(problem)
    return archive


def _zip_bomb_reason(archive: zipfile.ZipFile) -> str | None:
    infos = archive.infolist()
    if len(infos) > _MAX_ZIP_ENTRIES:
        return "office document contains too many entries"
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            return "encrypted office documents are not extracted"
        _reject_unsafe_zip_name(info.filename)
        size = info.file_size
        if size < 0:
            return "office document contains an invalid entry size"
        total += size
        if total > _MAX_UNCOMPRESSED_BYTES:
            return "office document exceeds the uncompressed size limit"
        compressed = info.compress_size
        if compressed > 0 and size / compressed > _MAX_COMPRESSION_RATIO:
            return "office document compression ratio is not allowed"
    return None


def _reject_unsafe_zip_name(name: str) -> None:
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        raise ValueError("office document contains an unsafe path")
    parts = re.split(r"[\\/]", name)
    if any(part in _UNSAFE_ZIP_PARTS for part in parts):
        raise ValueError("office document contains an unsafe path")


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > _MAX_XML_BYTES:
        raise ValueError("office XML part is too large")
    with archive.open(info, "r") as handle:
        payload = handle.read(_MAX_XML_BYTES + 1)
    if len(payload) > _MAX_XML_BYTES:
        raise ValueError("office XML part is too large")
    return payload


def _xml_root(payload: bytes) -> Element:
    if len(payload) > _MAX_XML_BYTES:
        raise ValueError("office XML part is too large")
    try:
        root: Any = parse_xml(payload)
    except ParseError:
        raise ValueError("office XML could not be parsed") from None
    except Exception:
        raise ValueError("office XML was rejected") from None
    if not isinstance(root, Element):
        raise ValueError("office XML could not be parsed")  # noqa: TRY004 - parser contract, not a caller type error
    return root
