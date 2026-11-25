import json
from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review import (BATCH_INPUT, SINGLE_INPUT, ReviewReplay, native_slot, apply_edits,
                    batch_key, restore_running_citations)


class ReviewSlotTests(unittest.TestCase):
    def test_restore_lost_reference_from_source(self):
        row = {"id":"a", "source":"See {v1} and {v2}", "translation":"见{v1}",
               "slot":["batch", 0], "editable":True, "restoreMarkup":True}
        with self.assertRaises(ValueError):
            apply_edits([row],[{"id":"a","translation":"见{v1}、{v3}"}])
        apply_edits([row],[{"id":"a","translation":"见{v1}、{v2}"}])
        self.assertEqual(row["translation"], "见{v1}、{v2}")

    def test_same_source_uses_its_original_batch_position(self):
        first = [{"id": 0, "input": "Day"}, {"id": 1, "input": "Day"}]
        second = [{"id": 0, "input": "Day"}, {"id": 1, "input": "Month"}]
        manifest = []
        prompts = []
        for items, translations in [(first, ["天数", "当天"]), (second, ["日", "月"])]:
            prompt = "Translate\n" + BATCH_INPUT + json.dumps(items)
            prompts.append(prompt)
            for pos, item in enumerate(items):
                entry = {"multi_paragraph_index": pos, "llm_translate_trackers": [{"input": prompt}]}
                manifest.append({"slot": native_slot(entry, item["input"]),
                                 "source": item["input"], "translation": translations[pos]})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "segments.json"
            path.write_text(json.dumps(manifest))
            replay = ReviewReplay(None, path)
            self.assertEqual([i["output"] for i in json.loads(replay.llm_translate(prompts[0]))],
                             ["天数", "当天"])
            self.assertEqual([i["output"] for i in json.loads(replay.llm_translate(prompts[1]))],
                             ["日", "月"])
            with self.assertRaises(RuntimeError):
                replay.translate("Day")

    def test_native_short_output_retry_never_guesses_a_repeated_heading(self):
        heading = "Scientific Journal <style id='1'>2021</style>, 10, 3647"
        spaced = " " + heading + " "
        rows = [{"source":heading, "translation":heading}, {"source":spaced, "translation":"刊名"}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "segments.json"
            path.write_text(json.dumps(rows))
            replay = ReviewReplay(None, path)
            with self.assertRaises(RuntimeError):
                replay.llm_translate(SINGLE_INPUT + "\n" + heading)
            rows[0]["translation"] = "刊名"
            path.write_text(json.dumps(rows))
            self.assertEqual(ReviewReplay(None,path).llm_translate(SINGLE_INPUT + spaced),"刊名")

    def test_known_slot_must_match_its_source_even_if_a_text_fallback_exists(self):
        items = [{"id":0, "input":"Study population"}]
        slot = [batch_key(items),0]
        rows = [{"source":"Other heading", "translation":"其他标题", "slot":slot},
                {"source":"Study population", "translation":"研究人群"}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "segments.json"
            path.write_text(json.dumps(rows))
            with self.assertRaises(RuntimeError):
                ReviewReplay(None,path).llm_translate(BATCH_INPUT + json.dumps(items))

    def test_restore_only_damaged_unedited_citations_verified_in_repeated_margins(self):
        plain = "Example Journal 2023, 42, 715"
        source = "Example Journal <style id='1'>2023</style>, 42, 715"
        misplaced = "<style id='1'>Example Journal</style> 2023, 42, 715"
        target = "示例期刊 <style id='1'>2023</style>, 42, 715"
        rows = [dict(id=str(page), source=source, page=page, slot=["sample",page],
                     editable=True, restoreMarkup=True, translation="示例期刊")
                for page in range(1, 8)]
        rows[1].update(translation=misplaced, restoreMarkup=False)
        rows[2].update(translation=target, restoreMarkup=False)
        apply_edits(rows, [{"id":"4","translation":target}])
        self.assertTrue(rows[3]["reviewed"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper.pdf"
            with pymupdf.open() as doc:
                for number in range(1, 8):
                    pg = doc.new_page()
                    if number != 7:
                        pg.insert_text((30, 350 if number == 5 else 40), plain)
                    if number == 6:
                        pg.insert_text((30, 400), plain)
                doc.save(path)
            restore_running_citations(rows, path)
            self.assertEqual([r["translation"] for r in rows],
                             [source,source,target,target,"示例期刊","示例期刊","示例期刊"])
            # Even when an unpositioned retry refers to a verified header,
            # a separate edited translation must never leak to another page.
            manifest = Path(folder) / "segments.json"
            manifest.write_text(json.dumps(rows))
            with self.assertRaises(RuntimeError):
                ReviewReplay(None,manifest).translate(source)

    def test_two_margin_mentions_do_not_prove_a_running_citation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper.pdf"
            with pymupdf.open() as doc:
                for y in (40, 40, 300):
                    doc.new_page().insert_text((30, y), "Monthly Journal 2024, 5, 24")
                doc.save(path)
            rows = [dict(source="Monthly Journal <style id='1'>2024</style>, 5, 24",
                         page=i, slot=["m",i], restoreMarkup=True, editable=True, translation="月刊")
                    for i in range(1,4)]
            restore_running_citations(rows,path)
            self.assertTrue(all(row["translation"] == "月刊" for row in rows))


if __name__ == "__main__":
    unittest.main()
