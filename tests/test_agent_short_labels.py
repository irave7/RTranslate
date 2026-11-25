"""Short-label eligibility and grounding without positional guesses."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_bridge import jobs
from agent_bridge.codec import unpack
from agent_bridge.compact import omit_literals, restore
from agent_bridge.short_labels import annotate, contexts, protected


class ShortLabelsTests(unittest.TestCase):
    def test_new_jobs_persist_their_native_segmentation_threshold(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(jobs,"spawn",return_value={}):
            base = Path(tmp)
            (base/"source.txt").write_text("Test input",encoding="utf8")
            jobs.create(base/"source.txt",base/"job",None,False,False)
            self.assertEqual(jobs.job_info(base/"job")["min_text_length"],2)

    def test_abbreviations_symbols_and_units_vs_ordinary_uppercase_words(self):
        for text in ("ASA","OR","PPN","FT","CI","mL","mg","Na","T3","≤","±","11.0"):
            self.assertTrue(protected(text),text)
        for text in ("yes","YES","NO","No","Open","OPEN","Age","MALE","diet","Sex"):
            self.assertFalse(protected(text),text)
        source = ["ASA","YES","Open","10","mg"]
        task = {"kind":"paragraphs","items":list(source),"layout":["cell"]*5}
        mapping = omit_literals(task)
        self.assertEqual(task["items"],["YES","Open"])
        self.assertEqual(restore(source,mapping,["是","开腹"]),["ASA","是","开腹","10","mg"])
        original = {"id":"id","lease":"lease","messages":[{"role":"user","content":
                    "Role\n\n## Rules\nKeep text.\n## Output\nOnly text.\nNow translate the following text:\nASA"}]}
        self.assertEqual(unpack(original,"美国麻醉医师协会"),"ASA")

    def test_homographs_get_only_supported_context(self):
        choices = {"Open":[["Approach","Laparoscopy"],["Masking","Placebo"]]}
        case = {"kind":"paragraphs", "items":["Open","Laparoscopy"]}
        annotate(case,choices)
        self.assertEqual(case["nearbyLabels"],{0:["Approach","Laparoscopy"]})
        isolated = {"kind":"single","text":"Open"}
        annotate(isolated,choices)
        self.assertNotIn("nearbyLabels",isolated)

    def test_extraction_pairs_a_short_cell_with_its_row_heading(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest("requires the managed PDF runtime")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"synthetic.pdf"
            with pymupdf.open() as doc:
                pg=doc.new_page(width=500,height=600)
                pg.insert_text((55,120),"Surgical approach")
                pg.insert_text((270,120),"Open")
                pg.insert_text((55,150),"Comparison")
                pg.insert_text((270,150),"Laparoscopy")
                doc.save(path)
            nearby = contexts(path)
            self.assertEqual(nearby["Open"][0][0],"Surgical approach")
            self.assertIn("Laparoscopy",nearby["Open"][0])


if __name__ == "__main__":
    unittest.main()
