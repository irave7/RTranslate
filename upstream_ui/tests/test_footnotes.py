import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from footnotes import repair_table_footnotes


def _paper(folder, *, blocked=False, rules=True, different_right=False, superscript=False):
    paths = [folder / (name + ".pdf") for name in ("source", "mono", "dual")]
    with fitz.open() as source, fitz.open() as translated:
        src, out = source.new_page(width=300, height=300), translated.new_page(width=300, height=300)
        rule_stream = ""
        if rules:
            for top in (55, 106):
                src.draw_line((35, top), (263, top))
                rule_stream += f"q 35 {300-top} m 263 {300-top} l S Q\n"
        src.insert_text((35, 114), "FT refers to fluid therapy. A second comment on study methods", fontsize=8)
        src.insert_text((35, 124), "continues in this line.", fontsize=8)
        out.insert_font(fontname="helv")
        def glyphs(text, x, y):
            stream = []
            for char in text:
                stream.append(f"q 0 g BT/helv 8 Tf {x:.5f} {300-y} TD ({char})Tj ET Q")
                x += fitz.get_text_length(char, fontname="helv", fontsize=8)
            return "\n".join(stream)
        raw = [rule_stream, glyphs("First note in the footnote.", 35, 114),
               glyphs("Another sentence.", 48, 114)]
        if superscript:
            end = 48 + fitz.get_text_length("Another sentence.", fontname="helv", fontsize=8)
            raw.append(glyphs("1", end, 111))
        if blocked:
            raw.append(glyphs("Occupied space.", 35, 124))
        raw.append(glyphs("Next paragraph starts here.", 35, 160))
        xref = translated.get_new_xref()
        translated.update_object(xref, "<<>>")
        translated.update_stream(xref, "\n".join(raw).encode("ascii"))
        out.set_contents(xref)
        source.save(paths[0])
        translated.save(paths[1])
        with fitz.open() as pair:
            page = pair.new_page(width=600, height=300)
            page.show_pdf_page(fitz.Rect(0, 0, 300, 300), source, 0)
            page.show_pdf_page(fitz.Rect(300, 0, 600, 300), source if different_right else translated, 0)
            pair.save(paths[2])
    return paths


def _glyphs(page):
    return [(code, gid, tuple(round(v, 3) for v in xy)) for span in page.get_texttrace()
            for code, gid, xy, _ in span["chars"]]


class FootnoteTest(unittest.TestCase):
    def test_pair_keeps_all_other_glyphs_and_original_half(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, mono, dual = _paper(Path(tmp))
            left = fitz.Rect(0, 0, 300, 300)
            with fitz.open(mono) as old, fitz.open(dual) as pair:
                before = _glyphs(old[0])
                original_half = pair[0].get_pixmap(clip=left, matrix=fitz.Matrix(2, 2)).samples
            self.assertEqual(repair_table_footnotes(src, mono, dual), 1)
            with fitz.open(mono) as new, fitz.open(dual) as pair:
                after = _glyphs(new[0])
                self.assertEqual(original_half, pair[0].get_pixmap(clip=left, matrix=fitz.Matrix(2, 2)).samples)
                self.assertEqual([g[:2] for g in before], [g[:2] for g in after])
                moves = [(a, b) for a, b in zip(before, after) if a != b]
                self.assertEqual("".join(chr(b[0]) for _, b in moves), "Another sentence.")
                self.assertEqual(moves[0][1][2][0], 35)
                self.assertGreaterEqual(moves[0][1][2][1], 124)
                right = [g for g in _glyphs(pair[0]) if g[2][0] >= 300]
                self.assertEqual(len(right), len(after))
                for (c, gid, (x, y)), (cr, gidr, (xr, yr)) in zip(after, right):
                    self.assertEqual((c, gid, y), (cr, gidr, yr))
                    self.assertAlmostEqual(x, xr-300, places=2)
            self.assertEqual(repair_table_footnotes(src, mono, dual), 0)

    def test_single_output_keeps_footnotes_separated(self):
        for mode in ("mono", "dual"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                source, mono, dual = _paper(Path(tmp))
                unused = dual if mode == "mono" else mono
                unused.unlink()
                chosen = mono if mode == "mono" else dual
                self.assertEqual(repair_table_footnotes(source, mono if mode == "mono" else None,
                                                        dual if mode == "dual" else None), 1)
                self.assertFalse(unused.exists())
                with fitz.open(chosen) as out:
                    page = out[0]
                    x = 300 if mode == "dual" else 0
                    runs = [g for g in _glyphs(page) if g[2][0] >= x and 123 <= g[2][1] < 140]
                    self.assertEqual("".join(chr(g[0]) for g in runs), "Another sentence.")

    def test_leaves_pair_byte_identical_without_safe_supported_placement(self):
        for options in ({"blocked": True}, {"rules": False}, {"different_right": True}, {"superscript": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp:
                paths = _paper(Path(tmp), **options)
                snapshots = [p.read_bytes() for p in paths]
                self.assertEqual(repair_table_footnotes(*paths), 0)
                self.assertEqual([p.read_bytes() for p in paths], snapshots)


if __name__ == "__main__":
    unittest.main()
