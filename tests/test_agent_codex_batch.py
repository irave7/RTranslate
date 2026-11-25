import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from agent_bridge import cli, jobs
from agent_bridge.queue import TaskQueue


class FreshSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name)
        jobs.write(self.job/"job.json",{"format":"pdf","glossary":""})
        jobs.write(self.job/"state.json",{"state":"working"})
        self.queue = TaskQueue(self.job)
        self.calls = []
        self.origins = []

    def run_cli(self, *args, expected=0):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main([*args,"--job",str(self.job)]), expected)
        return json.loads(output.getvalue())

    def packet(self, text, filename):
        ident = self.queue.enqueue("agent",[{"role":"user","content":
              "Translate\n## Rules\nOK\n## Output\nOK\nNow translate the following text:"+text}],{})
        with patch.object(jobs,"active", return_value=True):
            summary = self.run_cli("next","--compact","--packet-file",str(filename),"--wait","0","--limit","1")
        self.assertNotIn(text,json.dumps(summary))
        return ident

    def fake_exec(self, command, **options):
        if command[1] == "login":
            return CompletedProcess(command,0,stdout=b"Logged in using ChatGPT",stderr=b"")
        self.assertEqual(command[1],"exec")
        self.assertIn("--ephemeral",command)
        self.assertEqual(command[command.index("--model")+1],"explicit-model")
        self.assertNotIn("fork",command)
        self.assertNotIn("resume",command)
        self.assertNotIn("CODEX_THREAD_ID",options["env"])
        self.assertNotIn("RTRANSLATE_API_KEY",options["env"])
        folder = Path(options["cwd"])
        self.assertEqual(set(p.name for p in folder.iterdir()),{"translation-instructions.txt"})
        data = json.loads(options["input"])
        self.assertEqual(set(data),{"tasks"})
        self.assertEqual(len(data["tasks"]),1)
        self.calls.append(data["tasks"][0]["text"]); self.origins.append(folder)
        Path(command[command.index("--output-last-message")+1]).write_text('{"answers":["独立译文"]}')
        return CompletedProcess(command,0)

    def test_normal_entry_takes_separate_contexts_commits_once_and_reports_only_metadata(self):
        with patch("agent_bridge.codex_batch.shutil.which",return_value="codex"), patch("agent_bridge.codex_batch.subprocess.run",side_effect=self.fake_exec), patch.dict(os.environ,{"CODEX_THREAD_ID":"ancestor","RTRANSLATE_API_KEY":"private-secret"}):
            for n,text in enumerate(("First-only sentence","Second-only sentence")):
                path = self.job/f"packet{n}.json"
                ident = self.packet(text,path)
                for _ in range(2):
                    result = self.run_cli("codex-batch","--packet-file",str(path),"--model","explicit-model")
                    self.assertEqual(set(result),{"state","batch","accepted"})
                    self.assertEqual(result["accepted"],1)
                self.assertEqual(self.queue.answer(ident),"独立译文")
        self.assertEqual(self.calls,["First-only sentence","Second-only sentence"])
        self.assertNotEqual(self.origins[0],self.origins[1])

    def test_reclaimed_packet_does_not_spend_host_quota(self):
        path = self.job/"packet.json"
        self.packet("Expired request",path)
        self.queue.reclaim()
        with patch("agent_bridge.codex_batch.subprocess.run",side_effect=AssertionError("called host")):
            result = self.run_cli("codex-batch","--packet-file",str(path),expected=1)
        self.assertIn("过期",result["detail"])
