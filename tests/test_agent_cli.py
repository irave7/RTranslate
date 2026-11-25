import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_bridge import cli, jobs
from agent_bridge.queue import TaskQueue


def native(items):
    return [{"role":"user","content":(
        "Academic translation\n## Structure Rules\nKeep IDs.\n## Output Format\n"
        "JSON.\n## Here is the input:\n"+json.dumps([
            {"id":n+10,"input":text,"layout_label":"table"} for n,text in enumerate(items)]))}]


class BridgeCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        jobs.write(self.folder/"job.json", {"glossary":"control group = 对照组","format":"pdf"})
        jobs.write(self.folder/"state.json", {"state":"working","detail":"test"})
        self.queue = TaskQueue(self.folder)

    def next(self):
        with patch.object(jobs,"active",return_value=True):
            return cli.next_batch(self.folder,8,0,True)

    def test_omit_mixed_numbers_and_preserve_native_slots_on_retry(self):
        task = self.queue.enqueue("host-agent",native(["25.6%","Control group","10","Study {v1}"]),{})
        batch = self.next()
        self.assertEqual(batch["tasks"][0]["items"],["Control group","Study {v1}"])
        self.assertEqual(batch["glossary"],{"control group":"对照组"})
        invalid = cli.submit(self.folder,{"batch":batch["batch"],"answers":[["对照组","研究"]]})
        self.assertTrue(invalid["errors"])
        self.assertIsNone(self.queue.answer(task))
        value = {"batch":batch["batch"],"answers":{"1":["对照组","研究{v1}"]}}
        for _ in range(2):
            self.assertEqual(cli.submit(self.folder,value)["accepted"],[1])
        response = json.loads(self.queue.answer(task))
        self.assertEqual([(row["id"],row["output"]) for row in response],
                         [(10,"25.6%"),(11,"对照组"),(12,"10"),(13,"研究{v1}")])

    def test_local_fulfilment_and_public_glossary_priority(self):
        fixed = self.queue.enqueue("host-agent",native(["12.4","20%"]),{})
        self.assertEqual(self.next()["tasks"],[])
        self.assertEqual(json.loads(self.queue.answer(fixed))[1]["output"],"20%")
        cli.remember(self.folder,{"control group":"另一译法","outcome":"结局"})
        self.queue.enqueue("host-agent",native(["Outcome of the control group."]),{})
        batch = self.next()
        self.assertEqual(batch["glossary"],{"control group":"对照组","outcome":"结局"})

    def test_completed_folder_is_never_reused_as_new_input(self):
        paper = self.folder/"demo.txt"
        paper.write_text("test")
        with self.assertRaisesRegex(ValueError,"已存在"):
            jobs.create(paper, self.folder, None, False, False)
        self.assertTrue(paper.exists())

    def test_abandoned_batch_cannot_overwrite_reclaimed_answer(self):
        self.queue.enqueue("host-agent",native(["Outcome"]),{})
        old=self.next()
        self.queue.reclaim()
        new=self.next()
        self.assertTrue(cli.submit(self.folder,{"batch":old["batch"],"answers":[["旧结果"]]})["errors"])
        self.assertEqual(cli.submit(self.folder,{"batch":new["batch"],"answers":[["结局"]]})["accepted"],[1])
