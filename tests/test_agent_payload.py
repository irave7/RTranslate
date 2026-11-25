import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_bridge import cli, jobs
from agent_bridge.payload import streamline
from agent_bridge.queue import TaskQueue


class PayloadTests(unittest.TestCase):
    def test_context_is_lossless_and_self_contained_and_terms_apply_only_to_source(self):
        context = "A sufficiently long original figure caption provides the surrounding clinical context."
        hint = "1. First title in full text: (cid:1)(cid:2)\n2. The most recent title is: Surgical approach"
        task = {"kind":"figure","items":["Open"],"context":["Laparoscopy"],"nearbyText":context,
                "hints":hint,"figureTerminology":{"1st day":"术后第1天"},"glossary":"Open=打开\nabsent=无",
                "glossaries":[{"name":"one","terms":[["Laparoscopy","腹腔镜"],["Open","打开"],["another","其他"]]}]}
        import copy
        batch = streamline({"tasks":[task, copy.deepcopy(task)],"glossary":{"Open":"开腹","absent":"不存在"}})
        self.assertEqual(batch["glossary"],{"Open":"开腹"})
        for item in batch["tasks"]:
            self.assertEqual(batch["sharedContext"][item["nearbyTextRef"]], context)
            self.assertEqual(item["hints"], "Section: Surgical approach")
            self.assertEqual(item["glossaries"][0]["terms"],[["Laparoscopy","腹腔镜"]])
            self.assertNotIn("glossary",item)
            self.assertEqual(item["figureTerminology"]["1st day"],"术后第1天")

    def test_private_packet_stdout_collision_budget_and_complete_native_oversize(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            jobs.write(directory/"job.json",{"glossary":""})
            jobs.write(directory/"state.json",{"state":"working"})
            queue = TaskQueue(directory)
            secret = "private paper words"
            content = "Now\n## Rules\nplain\n## Output\ntranslation\nNow translate the following text:"
            for index in range(3):
                queue.enqueue("agent",[{"role":"user","content":content+secret+str(index)+" longer " * 900}],{})
            dest = directory/"private-packet"
            command = ["next","--compact","--job",folder,"--packet-file",str(dest),"--wait","0"]
            with patch.object(jobs,"active", return_value=True), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(command),0)
                record = json.loads(out.getvalue())
                self.assertEqual(set(record),{"state","file","count","batch"})
                self.assertEqual(record["count"],1)
                self.assertNotIn(secret,out.getvalue())
                self.assertIn(secret,jobs.read(dest)["tasks"][0]["text"])
                if os.name != "nt":
                    self.assertEqual(dest.stat().st_mode & 0o777,0o600)
                self.assertEqual(cli.main(command),1)
            self.assertEqual(queue.counts()["claimed"],1)


class CoalescingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = TaskQueue(Path(self.tmp.name))

    def add(self, message):
        return self.queue.enqueue("agent", [{"role":"user", "content": message}], {})

    def test_delayed_arrivals_share_one_claim_with_boundaries_intact(self):
        import threading
        from time import monotonic,sleep
        first=self.add("first label")
        def producer():
            sleep(.12)
            self.add("second label")
            sleep(.09)
            self.add("oversize "+"x"*3000)
        thread=threading.Thread(target=producer)
        thread.start()
        now=monotonic()
        rows=self.queue.collect(3, compact_budget=800)
        self.assertLess(monotonic()-now,2.2)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["id"],first)
        self.assertEqual(self.queue.counts()["claimed"],2)
        thread.join()
        final=self.queue.collect(2,compact_budget=800)
        self.assertEqual(len(final),1)
        self.assertTrue(final[0]["messages"][0]["content"].startswith("oversize"))

    def test_empty_probe_and_direct_single_calls_never_wait(self):
        with patch("agent_bridge.queue.time.sleep", side_effect=AssertionError("no aggregation wait")):
            self.assertFalse(self.queue.collect(8))
            self.add("engine Input:\n\nHello")
            self.assertEqual(len(self.queue.collect(8)),1)
            self.add("normal task")
            self.assertEqual(len(self.queue.collect(1)),1)

    def test_preexisting_full_batch_and_interrupted_collection(self):
        for i in range(3):
            self.add("task"+str(i))
        with patch("agent_bridge.queue.time.sleep",side_effect=KeyboardInterrupt):
            self.assertEqual(len(self.queue.collect(2)),2)
            with self.assertRaises(KeyboardInterrupt):
                self.queue.collect(4)
        self.assertEqual(self.queue.counts()["pending"],1)
        self.assertEqual(self.queue.counts()["claimed"],2)

    def test_measures_final_output_after_terms_and_neighbor_annotations(self):
        from agent_bridge.payload import bounded_batch
        tasks = [{"kind":"single", "text":"Dose", "nearbyLabels":{"0":["A"+"B"*55]}, "n":n} for n in (1,2)]
        selected = lambda rows:{"dose":"剂量"}
        out = bounded_batch(tasks,"123456789abc",selected,270)
        self.assertEqual(len(out["tasks"]),1)
        self.assertEqual(out["glossary"],{"dose":"剂量"})
        self.assertEqual(len(tasks),2)
