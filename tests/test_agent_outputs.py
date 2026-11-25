import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_bridge import cli, jobs, outputs, revision


class OutputPreferencesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "profile"
        env = patch.dict(os.environ, RTRANSLATE_CONFIG_DIR=str(self.config))
        env.start()
        self.addCleanup(env.stop)
        p = patch.object(jobs, "spawn", side_effect=lambda d: jobs.job_info(d))
        p.start()
        self.addCleanup(p.stop)
        self.input = self.root / "source.txt"
        self.input.write_text("A study of climate.", encoding="utf-8")

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(args)
        return code, json.loads(output.getvalue())

    def start_job(self, name, *options):
        return self.run_cli("start", str(self.input), "--job", str(self.root/name), *options)

    def test_default_override_remember_and_reset_keep_previous_jobs_pinned(self):
        self.assertEqual(self.run_cli("preferences"), (0, {"output":"mono","saved":False}))
        self.assertFalse(self.config.exists())
        self.assertEqual(self.start_job("one")[1]["output"], "mono")
        self.assertEqual(self.start_job("two", "--output","dual")[1]["output"], "dual")
        self.assertFalse(self.config.exists())
        self.assertEqual(self.start_job("three","--output","both","--remember-output")[1]["output"], "both")
        self.assertEqual(self.start_job("four")[1]["output"], "both")
        self.assertEqual(self.run_cli("preferences","--output","dual")[1], {"output":"dual","saved":True})
        self.assertEqual(self.start_job("five")[1]["output"], "dual")
        self.assertEqual(self.run_cli("preferences","--reset")[1], {"output":"mono","saved":False})
        self.assertEqual(self.start_job("six")[1]["output"], "mono")
        self.assertEqual(jobs.job_info(self.root/"three")["output"], "both")
        self.assertEqual(jobs.job_info(self.root/"five")["output"], "dual")
        self.assertNotEqual(self.start_job("invalid","--remember-output")[0], 0)
        self.assertFalse((self.root/"invalid").exists())

    def test_status_and_latest_revision_only_advertise_selected_files(self):
        for mode in outputs.MODES:
            job = self.root/mode
            self.start_job(mode,"--output",mode)
            for number,name in enumerate(("result","revision-00000001")):
                folder = job / name
                folder.mkdir()
                jobs.write(folder/"receipt.json",{"pages":1,"percent":100})
                jobs.write(folder/"segments.json",[])
                chosen = outputs.paths(mode,folder)
                for path in chosen.values():
                    Path(path).write_bytes(b"pdf")
                status = jobs.status(job)
                self.assertEqual(status["output"], mode)
                self.assertEqual(status["revision"], number)
                self.assertEqual({k:status[k] for k in outputs.NAMES if k in status}, chosen)

    def test_older_jobs_keep_both_outputs_even_with_another_user_default(self):
        job = self.root / "old"
        job.mkdir()
        jobs.write(job/"job.json",{"format":"pdf"})
        self.assertEqual(jobs.job_info(job)["output"], "both")
        self.assertEqual(outputs.preferences()["output"], "mono")
