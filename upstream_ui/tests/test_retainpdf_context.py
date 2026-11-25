import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retainpdf_support.document import BoundaryIndex
from retainpdf_support.prompt import MARKER, add_boundary_context
from pdf2zh_next.translator.translator_impl.freellmapi import FreeLLMAPITranslator
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator


LEFT = "Most trial participants were included in the analysis of"
RIGHT = "postoperative complications after colorectal surgery."
FULL = "This separate paragraph describes a different research question."


def put(page, text, x, y, size=9):
    page.insert_text((x, y), text, fontsize=size)


def index_for(builder):
    with tempfile.TemporaryDirectory() as tmp, pymupdf.open() as doc:
        builder(doc)
        source = Path(tmp) / "clinical.pdf"
        doc.save(source)
        return BoundaryIndex.from_pdf(source)


class RetainPDFIntegrationTests(unittest.TestCase):
    def test_cross_page_context_preserves_json_ids_and_original_input(self):
        def make(doc):
            put(doc.new_page(), LEFT, 48, 720)
            put(doc.new_page(), RIGHT, 48, 75)
        index = index_for(make)
        rows = [{"id": "p1", "input": LEFT, "layout_label": "plain text"},
                {"id": "p2", "input": RIGHT, "layout_label": "plain text"},
                {"id": "title", "input": LEFT, "layout_label": "title"}]
        prompt = "Translate.\n" + MARKER + json.dumps(rows)
        amended = add_boundary_context(prompt, index)
        self.assertNotEqual(prompt, amended)
        self.assertEqual(prompt.split(MARKER)[1], amended.split(MARKER)[1])
        metadata = json.loads(amended.split(MARKER)[0].splitlines()[-2])
        self.assertEqual(metadata, [{"id":"p1","following_text":RIGHT},{"id":"p2","preceding_text":LEFT}])
        self.assertEqual(add_boundary_context(MARKER + json.dumps([rows[2]]), index),
                         MARKER + json.dumps([rows[2]]))

    def test_only_column_tail_can_reference_adjacent_column_head(self):
        def make(doc):
            p = doc.new_page(width=650, height=800)
            put(p, FULL, 34, 85)
            put(p, RIGHT, 338, 124)
            put(p, LEFT, 34, 700)
            put(p, "An independent sentence ends the right column.", 338, 666)
        index = index_for(make)
        self.assertEqual(index.context_for(LEFT), {"following_text": RIGHT})
        self.assertEqual(index.context_for(FULL), {})

    def test_native_next_provider_sends_context_and_preserves_returned_ids(self):
        def make(doc):
            put(doc.new_page(), LEFT, 44, 715)
            put(doc.new_page(), RIGHT, 44, 80)
        provider = object.__new__(FreeLLMAPITranslator)
        provider.boundary_index = index_for(make)
        row = {"id": 17, "input": RIGHT, "layout_label": "plain text"}
        response = json.dumps([{"id": 17, "output": "术后并发症。"}], ensure_ascii=False)
        with patch.object(OpenAITranslator, "llm_translate", return_value=response) as transport:
            translated = provider.llm_translate(MARKER + json.dumps([row]))
        sent = transport.call_args.args[0]
        self.assertEqual(translated, response)
        self.assertIn(LEFT, sent.split(MARKER)[0])
        self.assertEqual(json.loads(sent.split(MARKER)[1]), [row])

    def test_ambiguous_duplicate_anchor_is_not_accepted(self):
        def make(doc):
            p = doc.new_page()
            put(p, LEFT, 48, 200)
            put(p, LEFT, 48, 710)
            put(doc.new_page(), RIGHT, 48, 80)
        index = index_for(make)
        self.assertEqual(index.context_for(LEFT), {})

    def test_unrelated_columns_heading_caption_and_list_are_rejected(self):
        for candidate in ("2.3. Clinical measures and laboratory endpoints",
                          "Figure 3. The flow of study participants.",
                          "1) study populations recruited in spring."):
            with self.subTest(candidate=candidate):
                def make(doc):
                    put(doc.new_page(), LEFT, 48, 715)
                    put(doc.new_page(), candidate, 48, 80)
                self.assertEqual(index_for(make).context_for(LEFT), {})
        def unrelated(doc):
            put(doc.new_page(), LEFT, 48, 715)
            put(doc.new_page(), RIGHT, 320, 80)
        self.assertEqual(index_for(unrelated).context_for(LEFT), {})

    def test_does_not_cross_blank_page_or_unreadable_table(self):
        def blank(doc):
            put(doc.new_page(), LEFT, 48, 715)
            doc.new_page()
            put(doc.new_page(), RIGHT, 48, 80)
        self.assertEqual(index_for(blank).context_for(LEFT), {})
