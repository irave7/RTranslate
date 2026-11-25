"""Exercises native wire templates with synthetic publication text and identifiers."""
import json
import unittest

from agent_bridge.codec import pack, unpack


def task(text, system=None):
    messages = [{"role":"user", "content":text}]
    if system:
        messages.insert(0, {"role":"system", "content":system})
    return {"id":"task-42", "lease":"local-lease", "messages":messages}


def batch(rows, extra=""):
    return task("Research style: keep British spellings.\n\n## Structure Rules\nTranslate each item."
                "\n## Output Format\nJSON\n### Example\n[{}]\n"
                "## Contextual Hints for Better Translation\n1. First title in full text: Example Study\n"
                "2. The most recent title is: Methods\n\n## Glossary Tables\n"
                "### Glossary: User terminology\n\n| Source Term | Target Term |\n"
                "|-------------|-------------|\n| panel | 分图 |\n\n"
                + extra + "## Here is the input:\n" + json.dumps(rows))


class NativeCodecTest(unittest.TestCase):
    def test_batch_reduces_prompt_but_keeps_local_context_and_ids(self):
        rows = [{"id":9,"input":"The first panel{v4}","layout_label":"plain text"},
                {"id":2,"input":"<style id='1'>7%</style>","layout_label":"table"}]
        extra = ("The following neighboring source text continues across a page or column boundary. "
                 "Translate only the specified ID.\n"
                 '[{"id":9,"following_text":"shows the mechanism.","previous_text":"Example beginning"}]\n\n')
        original = batch(rows, extra)
        compact = pack(original)
        self.assertEqual((compact["id"],compact["lease"],compact["kind"]),("task-42","local-lease","paragraphs"))
        self.assertEqual(compact["items"],[r["input"] for r in rows])
        self.assertEqual(compact["layout"], ["plain text","table"])
        self.assertIn("Example Study", compact["hints"])
        self.assertIn("Methods", compact["hints"])
        self.assertEqual(compact["instructions"], "Research style: keep British spellings.")
        self.assertEqual(compact["glossaries"], [{"name":"User terminology","terms":[["panel","分图"]]}])
        self.assertEqual(compact["neighbors"], [{"index":0,"following_text":"shows the mechanism.","previous_text":"Example beginning"}])
        outputs = ["第一个分图{v4}", "<style id='1'>7%</style>"]
        self.assertEqual(json.loads(unpack(original, outputs)), [{"id":9,"output":outputs[0]},{"id":2,"output":outputs[1]}])
        for answer in ([outputs[0]], ["第一个分图{v3}",outputs[1]], [outputs[0],"<style id='2'>7%</style>"], [outputs[0],"<style id='1'>8%</style>"]):
            with self.subTest(answer=answer),self.assertRaises(ValueError):
                unpack(original, answer)

    def test_single_and_numeric_table(self):
        original = task("Translation role\n\n## Rules\nTranslate only.\n## Context / Hints\n"
                        "1. Formula placeholder hint:\n{v1} = alpha\n\n## Output\nOnly text."
                        "\nNow translate the following text:\n\nRatio {v1} is 25 per cent.")
        compact = pack(original)
        self.assertEqual(compact["text"], "Ratio {v1} is 25 per cent.")
        self.assertIn("{v1} = alpha", compact["hints"])
        self.assertEqual(unpack(original,"比率{v1}为25%。"),"比率{v1}为25%。")
        with self.assertRaises(ValueError):
            unpack(original,"比率{v1}为35%。")
        cell = batch([{"id":1,"input":"12 (47.5)"}])
        self.assertEqual(json.loads(unpack(cell, ["12（47.5）"]))[0]["output"], "12 (47.5)")
        with self.assertRaises(ValueError):
            unpack(cell, ["13（47.5）"])

    def test_terms_and_reference(self):
        original = task("You are an expert multilingual terminologist.\n### Extraction Rules\n"
                        "Reference Glossaries (for consistency and quality):\nInstitution:\n- site → 中心\n"
                        "\nPlease consider these existing translations for consistency.\n### Output Format\nJSON\n"
                        "Input Text:\n```\nIn the test site.\n```\n\nReturn JSON ONLY. NO OTHER TEXT.\nResult:\n")
        compact = pack(original)
        self.assertEqual(compact["text"],"In the test site.")
        self.assertIn("site → 中心",compact["referenceGlossary"])
        self.assertEqual(json.loads(unpack(original,[["site","中心"]])),[{"src":"site","tgt":"中心"}])
        self.assertEqual(unpack(original,[]),"[]")
        with self.assertRaises(ValueError):
            unpack(original,[["site",""]])

    def test_figure_source_context_terms_ids_reconstructed(self):
        body = {"glossary":"Group A = A组","figureTerminology":{"eligible":"符合资格"},
                "nearbyText":"Groups in a controlled study","context":["Group A","Group B"],
                "items":[{"id":"t9","source":"Group A (n=14)"},{"id":"t2","source":"Group B (n=16)"}]}
        original = task(json.dumps(body),"你是科研论文图表翻译。简洁表述。")
        compact = pack(original)
        self.assertEqual(compact["kind"],"figure")
        for key in ("glossary","figureTerminology","nearbyText","context"):
            self.assertEqual(compact[key],body[key])
        result = json.loads(unpack(original, ["A组（n=14）","B组（n=16）"]))
        self.assertEqual(result[1],{"id":"t2","source":"Group B (n=16)","translation":"B组（n=16）"})
        with self.assertRaises(ValueError):
            unpack(original,["A组（n=15）","B组（n=16）"])

    def test_raw_unknown_and_malformed_requests_remain_lossless(self):
        unknown = task("User-defined special task","Keep the form exactly")
        broken = batch([{"id":1,"input":"hello"}], "The following neighboring source text continues across a page or column boundary. bad json")
        for original in (unknown,broken):
            compact = pack(original)
            self.assertEqual(compact["kind"],"raw")
            self.assertEqual(compact["messages"],original["messages"])
            self.assertEqual(unpack(original, "As requested"),"As requested")

    def test_retry_sentences_keep_context_and_masks(self):
        original = task("任务：将指定的一个英文科研句子译成学术简体中文。\n【整段上下文】\nBefore. Now. After.\n【本次只译这一句】\nNow.")
        self.assertEqual(pack(original)["text"],"Now.")
        self.assertEqual(pack(original)["context"],"Before. Now. After.")
        strict = task("将输入的医学文献片段逐字义翻译为简体中文。\n输入：The cell: [[PERCENT_0]]")
        with self.assertRaises(ValueError):
            unpack(strict,"该单元格25%。")


if __name__ == "__main__":
    unittest.main()
