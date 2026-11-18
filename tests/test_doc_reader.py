from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import zipfile

from docx import Document
from openpyxl import Workbook
import pytest

from jawis.config import load_config
from jawis.security.audit import AuditLogger
from jawis.security.path_guard import PathAccessDeniedError, PathSecurityGuard
from jawis.storage.db import Database
from jawis.tools.doc_reader import ScopedDocumentReader


def _make_reader(tmp_path: Path) -> tuple[ScopedDocumentReader, Database, Path]:
    cfg = load_config(tmp_path / "data")
    db = Database(cfg.db_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    db.add_folder(str(docs), "Docs")
    guard = PathSecurityGuard(cfg, db, AuditLogger(db))
    return ScopedDocumentReader(cfg, db, guard, AuditLogger(db)), db, docs


def test_reads_bounded_text_rejects_extensionless_and_frames_output(tmp_path: Path) -> None:
    reader, db, docs = _make_reader(tmp_path)
    try:
        big = docs / "big.txt"
        big.write_text("A" * 100_000, encoding="utf-8")
        seen: list[str] = []
        result = reader.read_file(str(big), on_access=seen.append)
        assert seen == [big.name]
        assert result["truncated"] is True
        assert result["untrusted_data_notice"].startswith("UNTRUSTED_DOCUMENT")
        assert len(result["content"]) <= reader.config.max_file_text_chars

        raw_bin = docs / "Login Data"
        raw_bin.write_bytes(b"\x00\x01binary")
        res_bin = reader.read_file(str(raw_bin))
        assert res_bin["error"] == "UNSUPPORTED_FORMAT"
    finally:
        db.close()


def test_reads_docx_tables_xlsx_and_blocks_zip_bombs(tmp_path: Path) -> None:
    reader, db, docs = _make_reader(tmp_path)
    try:
        (docs / ".env").write_text("SECRET=1", encoding="utf-8")
        doc = Document()
        doc.add_paragraph("J.A.R.V.I.S. project brief")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Phase"
        table.cell(0, 1).text = "Alpha"
        docx_path = docs / "brief.docx"
        doc.save(str(docx_path))

        wb = Workbook()
        ws = wb.active
        ws.title = "Budget"
        ws.append(["Item", "Cost"])
        ws.append(["API", 100])
        xlsx_path = docs / "budget.xlsx"
        wb.save(str(xlsx_path))

        hits = reader.search_files("")
        names = {Path(h["path"]).name for h in hits}
        assert ".env" not in names
        assert "brief.docx" in names

        docx_res = reader.read_file(str(docx_path))
        assert "J.A.R.V.I.S. project brief" in docx_res["content"]
        assert "Alpha" in docx_res["content"]

        assert reader.read_file(str(xlsx_path), page_or_section="Missing")["error"] == "SHEET_NOT_FOUND"
        xlsx_res = reader.read_file(str(xlsx_path), page_or_section="Budget")
        assert "API" in xlsx_res["content"]

        # Create simulated zip bomb exceeding decompressed limit (>100:1 compression ratio on >1MB entry)
        bomb = docs / "bomb.docx"
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", b"0" * 5_000_000)
        assert reader.read_file(str(bomb))["error"] == "FILE_TOO_LARGE"
    finally:
        db.close()


def test_reads_utf8_bom_utf16_and_multibyte_boundary_without_garbling(tmp_path: Path) -> None:
    reader, db, docs = _make_reader(tmp_path)
    try:
        utf8_bom = docs / "bom.md"
        utf8_bom.write_bytes(b"\xef\xbb\xbf# Header\nHello")
        res_bom = reader.read_file(str(utf8_bom))
        assert res_bom["content"] == "# Header\nHello"
        assert res_bom["truncated"] is False

        utf16_file = docs / "u16.txt"
        utf16_file.write_bytes("สวัสดี".encode("utf-16"))
        res_u16 = reader.read_file(str(utf16_file))
        assert res_u16["content"] == "สวัสดี"

        # Exceed 64KB with 3-byte UTF-8 Thai characters so byte 65536 splits a character.
        # Ensure we trim the partial trailing byte cleanly instead of falling back to cp874 mojibake.
        thai_chunk = "สวัสดี"  # 18 bytes in UTF-8
        thai_big = docs / "thai_big.txt"
        thai_big.write_bytes(("A" + thai_chunk * 5000).encode("utf-8"))
        res_thai = reader.read_file(str(thai_big))
        assert res_thai["truncated"] is True
        assert res_thai["content"].startswith("Aสวัสดีสวัสดี")
        assert "\ufffd" not in res_thai["content"]
    finally:
        db.close()


def test_reads_pdf_page_ranges_and_blocks_encrypted_pdf(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    reader, db, docs = _make_reader(tmp_path)
    try:
        pdf_path = docs / "manual.pdf"
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=200, height=200)
        with pdf_path.open("wb") as fh:
            writer.write(fh)

        res_default = reader.read_file(str(pdf_path))
        assert res_default["meta"]["total_pages"] == 3
        assert res_default["meta"]["pages_read"] == "1-3"
        assert "--- Page 1 ---" in res_default["content"]

        res_range = reader.read_file(str(pdf_path), page_or_section="2-3")
        assert res_range["meta"]["pages_read"] == "2-3"
        assert "--- Page 2 ---" in res_range["content"]
        assert "--- Page 1 ---" not in res_range["content"]

        assert reader.read_file(str(pdf_path), page_or_section="bad")["error"] == "INVALID_PAGE_RANGE"
        assert reader.read_file(str(pdf_path), page_or_section="0")["error"] == "INVALID_PAGE_RANGE"
        assert reader.read_file(str(pdf_path), page_or_section="4")["error"] == "INVALID_PAGE_RANGE"
        assert reader.read_file(str(pdf_path), page_or_section="1-20")["error"] == "INVALID_PAGE_RANGE"

        enc_path = docs / "locked.pdf"
        enc_writer = PdfWriter()
        enc_writer.add_blank_page(width=100, height=100)
        enc_writer.encrypt("secret")
        with enc_path.open("wb") as fh:
            enc_writer.write(fh)
        assert reader.read_file(str(enc_path))["error"] == "PASSWORD_PROTECTED"
    finally:
        db.close()


def test_search_skips_junctions_skip_dirs_and_handles_corrupt_files(tmp_path: Path) -> None:
    reader, db, docs = _make_reader(tmp_path)
    try:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.txt").write_text("secret outside", encoding="utf-8")

        junction = docs / "link_out"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
            text=True,
        )

        node_mods = docs / "node_modules"
        node_mods.mkdir()
        (node_mods / "hidden_pkg.js").write_text("console.log(1)", encoding="utf-8")

        keep = docs / "notes.md"
        keep.write_text("hello", encoding="utf-8")

        hits = reader.search_files("")
        names = {h["name"] for h in hits}
        assert "notes.md" in names
        assert "leak.txt" not in names
        assert "hidden_pkg.js" not in names

        with pytest.raises(PathAccessDeniedError):
            reader.read_file(str(node_mods))

        corrupt_docx = docs / "corrupt.docx"
        corrupt_docx.write_bytes(b"not a zip archive")
        assert reader.read_file(str(corrupt_docx))["error"] == "FILE_TOO_LARGE"

        # Valid zip header but invalid xlsx workbook structure -> UNREADABLE_FILE
        bad_xlsx = docs / "bad.xlsx"
        with zipfile.ZipFile(bad_xlsx, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("xl/sheet1.xml", b"x" * 100)
        assert reader.read_file(str(bad_xlsx))["error"] == "UNREADABLE_FILE"

        # Total uncompressed size exceeding max_decompressed_zip_bytes even at 1:1 compression ratio
        big_stored = docs / "stored_huge.xlsx"
        with zipfile.ZipFile(big_stored, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("xl/sheet1.xml", b"x" * 2048)
        strict_cfg = replace(reader.config, max_decompressed_zip_bytes=1024)
        strict_reader = ScopedDocumentReader(
            strict_cfg, db, PathSecurityGuard(strict_cfg, db, AuditLogger(db)), AuditLogger(db)
        )
        assert strict_reader.read_file(str(big_stored))["error"] == "FILE_TOO_LARGE"

        # folder_id filtering restricts search to matching folder only
        docs2 = tmp_path / "docs2"
        docs2.mkdir()
        (docs2 / "second.txt").write_text("two", encoding="utf-8")
        fid2 = db.add_folder(str(docs2), "Docs2")
        scoped_hits = reader.search_files("", folder_id=fid2)
        assert [h["name"] for h in scoped_hits] == ["second.txt"]
    finally:
        db.close()


def test_office_parsers_are_lazy_imported_only() -> None:
    code = (
        "import sys; "
        "import jawis.tools.doc_reader; "
        "import jawis.tools.app_controller; "
        "import jawis.tools.telemetry; "
        "import jawis.tools.registry; "
        "loaded = [m for m in ('pypdf', 'docx', 'openpyxl', 'win32com') if m in sys.modules]; "
        "assert not loaded, f'Unexpected top-level imports: {loaded}'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
