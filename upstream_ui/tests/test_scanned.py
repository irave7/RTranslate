import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from upstream_ui.scanned import _looks_scanned, _prose_around_formulas, prepare_scanned


class ScanPreparationTests(unittest.TestCase):
    def test_full_page_raster_and_native_pdf_are_distinguished(self):
        with pymupdf.open() as native, pymupdf.open() as scan:
            p = native.new_page()
            p.insert_text((30,55), "A line of selectable English text.")
            p.insert_text((30,75), "The paper discusses the nutrition study on the following pages.")
            self.assertFalse(_looks_scanned(p))
            raster=scan.new_page()
            raster.insert_image(raster.rect, stream=p.get_pixmap().tobytes("png"))
            self.assertTrue(_looks_scanned(raster))
            # A hidden OCR layer is common in scans and should not count as native text.
            raster.insert_text((30,55),"Hidden English content for accessibility "*4,render_mode=3)
            self.assertTrue(_looks_scanned(raster))

    def test_plain_digital_page_never_downloads_ocr_or_creates_a_copy(self):
        with tempfile.TemporaryDirectory() as base:
            src, dest = Path(base)/"input.pdf",Path(base)/"work.pdf"
            with pymupdf.open() as doc:
                doc.new_page().insert_text((15,30),"A short paper.")
                doc.save(src)
            with patch("upstream_ui.scanned.prepare_english_ocr", side_effect=AssertionError):
                self.assertEqual(prepare_scanned(src,dest,lambda *_:None,threading.Event()),(src,[]))
            self.assertFalse(dest.exists())

    def test_blank_document_reports_missing_text_before_opening_a_model(self):
        with tempfile.TemporaryDirectory() as base:
            src = Path(base) / "blank.pdf"
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(src)
            with self.assertRaisesRegex(ValueError, "没有可翻译的正文"):
                prepare_scanned(src, Path(base) / "work.pdf", lambda *_: None, threading.Event())

    def test_inline_math_stays_in_source_image(self):
        line=[(0,0,1,1,t) for t in ("Risk","was","p","<","0.05","after","treatment.")]
        runs=[[w[4] for w in group] for group in _prose_around_formulas(line)]
        self.assertEqual(runs,[["Risk","was"],["after","treatment."]])
