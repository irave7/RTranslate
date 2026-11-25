import json
import unittest

from pdf2zh_next.translator.translator_impl.freellmapi import FreeLLMAPITranslator, numeric_literal, numeric_reply, source_verified_percent_spelling, unchanged_literal


class NumericFastPathTests(unittest.TestCase):
    def test_statistical_intervals_preserve_format_and_ids(self):
        items = [{"id": 9, "input": "1.14 (0.29–4.82)"}, {"id": 2, "input": "78%"}]
        reply = numeric_reply("## Here is the input:" + json.dumps(items))
        self.assertEqual(json.loads(reply), [{"id":row["id"],"output":row["input"]} for row in items])

    def test_mixed_batch_keeps_all_original_context(self):
        items = [{"id": 1, "input": "1.14"}, {"id": 2, "input": "1.14 million cases"}]
        self.assertIsNone(numeric_reply("## Here is the input:" + json.dumps(items)))
        for text in ("p < 0.05", "{v1}", "PPN vs. FC", "10 mg", "<style id='0'>1.0</style>"):
            self.assertFalse(numeric_literal(text))


    def test_equations_with_native_glyph_placeholders_survive_single_and_mixed_prompts(self):
        equation = "E {v1}(x - y) / n"
        self.assertEqual(numeric_reply("Now translate the following text: " + equation), equation)
        self.assertEqual(FreeLLMAPITranslator._recover_quantities(None, equation, "{n}({v1}(x - y) / n)", False, None), equation)
        for prose in ("A dose of 5 mg/kg", "Sensor calibration: x / n", "See Table 1 / 2",
                      "Fasting / feeding", "p = value", "<style id='0'>x</style>"):
            self.assertFalse(unchanged_literal(prose))
        from review import suggest
        self.assertEqual(suggest({"source":equation}, [], {}, "", "", ""), equation)

    def test_misplaced_percent_only_when_source_agrees(self):
        self.assertEqual(source_verified_percent_spelling("73% higher risk","风险增加%73"),"风险增加73%")
        self.assertEqual(source_verified_percent_spelling("73% higher risk","风险增加%78"),"风险增加%78")
        self.assertEqual(source_verified_percent_spelling("73%", "70%73"), "70%73")

    def test_decimal_comma_comparison_and_retry_spans_share_one_interpretation(self):
        from upstream_runtime.percentages import quantities_match, replace_percentages
        src = "Stage II (30.1%) and III (20, 7%)."
        self.assertTrue(quantities_match(src, "II级（30.1%）和III级（20.7%）。"))
        self.assertFalse(quantities_match(src, "II级（30.1%）和III级（7%）。"))
        spans = []
        def mark(match):
            spans.append(match.group())
            return "[P]"
        self.assertEqual(replace_percentages(src, mark), "Stage II ([P]) and III ([P]).")
        self.assertEqual(spans, ["30.1%", "20, 7%"])
        self.assertTrue(quantities_match("Rates: 0,5–3,75%", "比率为0.5%–3.75%"))
        self.assertTrue(quantities_match("Rates: 2,50%", "比率为2,50%"))

    def test_unproven_spacing_and_english_lists_are_not_merged(self):
        from upstream_runtime.percentages import quantities_match, replace_percentages
        for source in ("Measurements incl. 40, 5%", "including 40,5%", "Values (40, 5%)"):
            self.assertFalse(quantities_match(source, "40.5%"))
            self.assertTrue(quantities_match(source, "40、5%"))
        self.assertEqual(replace_percentages("incl. 40, 5%",lambda m: "[P]"),"incl. 40, [P]")
        self.assertFalse(quantities_match("1,250%","1.250%"))
