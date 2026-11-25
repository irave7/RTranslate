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
