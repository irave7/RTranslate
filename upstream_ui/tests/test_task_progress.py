import unittest

from task_progress import JobProgress


class JobTimeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.clock = lambda: self.now
        self.job = JobProgress(clock=self.clock)

    def test_skipped_cells_dont_predict_model_throughput(self):
        self.job.update("Translate Paragraphs", 0)
        self.now = .1
        self.job.update("Translate Paragraphs", .25, 25, 100)
        early = self.job.snapshot()
        self.assertEqual(early["stageCount"], "25 / 100 段")
        self.assertIsNone(early["remainingSeconds"])
        self.now = 70
        self.job.update("Translate Paragraphs", .26, 26, 100)
        self.assertIsNone(self.job.snapshot()["remainingSeconds"])
        self.now = 100
        self.job.update("Translate Paragraphs", .45, 45, 100)
        timed = self.job.snapshot()
        self.assertGreater(timed["remainingSeconds"], 150)
        self.assertLess(timed["remainingLow"], timed["remainingHigh"])
        self.assertGreater(timed["percent"], early["percent"])

    def test_export_and_terminal_clocks(self):
        self.job.update("Save PDF", 1)
        before = self.job.snapshot()["percent"]
        self.job.update("figure_read", 0)
        self.assertGreaterEqual(self.job.snapshot()["percent"], before)
        self.job.update("Translate Paragraphs", 0)
        self.assertEqual(self.job.stage, "figure_read")
        self.job.update("publish", 1)
        self.assertLess(self.job.snapshot()["percent"], 100)
        self.now = 30
        self.job.finish(True)
        self.now = 70
        self.assertEqual(self.job.snapshot()["elapsedSeconds"], 30)
        self.assertEqual(self.job.snapshot()["percent"], 100)
        self.assertIsNone(self.job.snapshot()["remainingSeconds"])

    def test_replay_cancel_and_prior(self):
        replay = JobProgress(replay=True, clock=self.clock)
        self.assertNotIn("Automatic Term Extraction", replay.weights)
        replay.update("Typesetting", .5)
        replay.finish(False)
        self.assertLess(replay.snapshot()["percent"], 100)
        timed = JobProgress(clock=self.clock, history={"Translate Paragraphs": (600, 100)})
        timed.update("Translate Paragraphs", 0, 0, 100)
        self.assertGreater(timed.snapshot()["remainingSeconds"], 500)

    def test_weighted_work_counts_long_text_and_caps_retries(self):
        from inference_work import InferenceWork
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            item = InferenceWork(temp)
            long = "biochemical measurements " * 20
            directory = Path(temp) / "source"
            directory.mkdir()
            (directory / "term_extractor_tracking.json").write_text(json.dumps({
                "page":[{"paragraph":[{"pdf_unicodes":[long, "No", "No", "33.7"]}]}]}))
            item._record([long, long, "unknown"])
            self.assertGreater(item.fraction(), .85)
            self.assertLess(item.fraction(), 1)
            item._record(["No", "No", "No"])
            self.assertEqual(item.fraction(), 1)
