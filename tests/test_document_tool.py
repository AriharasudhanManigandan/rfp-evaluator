import io
import sys
import unittest
from unittest import mock

from pypdf import PdfWriter

from rfp import pdf_tool
from rfp.samples import load_sample_metadata
from rfp.config import SAMPLE_DIR

LONG_TEXT = "The proposed platform uses a microservice architecture with documented APIs. " * 12


class FakePage:
    def __init__(self, text):
        self.text = text

    def get_text(self, mode="text"):
        return self.text


class FakeDoc(list):
    needs_pass = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_pymupdf(pages, needs_pass=False, raises=None):
    module = mock.MagicMock()

    def _open(stream=None, filetype=None):
        if raises:
            raise raises
        doc = FakeDoc(FakePage(t) for t in pages)
        doc.needs_pass = needs_pass
        return doc
    module.open = _open
    return module


def blank_pdf() -> bytes:
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.write(buf)
    return buf.getvalue()


class ExtractionTests(unittest.TestCase):
    def test_bundled_samples_are_readable(self):
        for m in load_sample_metadata():
            doc = pdf_tool.extract_text((SAMPLE_DIR / m["file"]).read_bytes(), m["file"])
            self.assertGreater(doc.chars, 500)
            self.assertIn(doc.extractor, {"pymupdf", "pypdf"})
            self.assertEqual(len(doc.sha256), 64)
            if not m["edge_case"]:
                self.assertTrue(2 <= doc.pages <= 4, m["file"])
            self.assertTrue(doc.text.startswith("[Page 1]"))

    def test_rejects_bad_files(self):
        for data, label in [(b"", "empty"), (b"hello world, not a pdf", "fake")]:
            with self.assertRaises(pdf_tool.DocumentError):
                pdf_tool.extract_text(data, label + ".pdf")
        with self.assertRaises(pdf_tool.DocumentError):
            pdf_tool.extract_text(b"%PDF-1.4 " + b"x" * (pdf_tool.MAX_PDF_BYTES + 1), "huge.pdf")
        with self.assertRaises(pdf_tool.DocumentError):
            pdf_tool.extract_text(b"%PDF-1.4 garbage", "corrupt.pdf")

    def test_scanned_or_blank_pdf_is_rejected(self):
        with self.assertRaises(pdf_tool.DocumentError) as ctx:
            pdf_tool.extract_text(blank_pdf(), "scan.pdf")
        self.assertIn("scanned", str(ctx.exception))

    def test_truncation_is_flagged(self):
        data = (SAMPLE_DIR / "apex_systems_rfp.pdf").read_bytes()
        with mock.patch.object(pdf_tool, "MAX_DOC_CHARS", 500):
            doc = pdf_tool.extract_text(data, "apex.pdf")
        self.assertTrue(doc.truncated)
        self.assertEqual(doc.chars, 500)
        self.assertTrue(doc.warnings)

    def test_same_file_gives_same_hash_and_text(self):
        data = (SAMPLE_DIR / "nexaworks_rfp.pdf").read_bytes()
        a, b = pdf_tool.extract_text(data, "n.pdf"), pdf_tool.extract_text(data, "n.pdf")
        self.assertEqual((a.sha256, a.text), (b.sha256, b.text))


class PyMuPDFBranchTests(unittest.TestCase):
    """Exercise the PyMuPDF code path with a stand-in module (the library may not be installed)."""

    DATA = b"%PDF-1.4 fake"

    def test_pymupdf_is_used_first(self):
        with mock.patch.dict(sys.modules, {"pymupdf": fake_pymupdf([LONG_TEXT, LONG_TEXT])}):
            doc = pdf_tool.extract_text(self.DATA, "a.pdf")
        self.assertEqual((doc.extractor, doc.pages), ("pymupdf", 2))
        self.assertIn("[Page 2]", doc.text)

    def test_falls_back_to_pypdf_when_pymupdf_errors(self):
        data = (SAMPLE_DIR / "apex_systems_rfp.pdf").read_bytes()
        with mock.patch.dict(sys.modules, {"pymupdf": fake_pymupdf([], raises=RuntimeError("boom"))}):
            doc = pdf_tool.extract_text(data, "a.pdf")
        self.assertEqual(doc.extractor, "pypdf")

    def test_falls_back_to_pypdf_when_pymupdf_finds_no_text(self):
        data = (SAMPLE_DIR / "apex_systems_rfp.pdf").read_bytes()
        with mock.patch.dict(sys.modules, {"pymupdf": fake_pymupdf(["", ""])}):
            doc = pdf_tool.extract_text(data, "a.pdf")
        self.assertEqual(doc.extractor, "pypdf")

    def test_password_protected_is_definitive(self):
        with mock.patch.dict(sys.modules, {"pymupdf": fake_pymupdf([LONG_TEXT], needs_pass=True)}):
            with self.assertRaises(pdf_tool.DocumentError) as ctx:
                pdf_tool.extract_text(self.DATA, "locked.pdf")
        self.assertIn("password", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
