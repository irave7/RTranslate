import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from agent_bridge import revision
from agent_bridge.jobs import worker_lock, write


class RevisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.initial = self.root/"result"
        self.initial.mkdir()
        self.row = dict(id="a",source="Results",translation="结果",page=1,editable=True,restoreMarkup=False)
        write(self.initial/"segments.json", [self.row, {**self.row,"id":"b","editable":False}])
        write(self.initial/"receipt.json",dict(pages=1,scanned_pages=[],figure_labels=0,figure_labels_written=0))
        write(self.root/"job.json",dict(format="pdf",figures=False,glossary=""))
        (self.root/"source.pdf").write_bytes(b"original")
        for name in ("translated.pdf","bilingual.pdf"):
            (self.initial/name).write_bytes(b"initial")
        self.changes = [dict(id="a",translation="研究结果")]

    def test_only_committed_revisions_and_editable_rows_are_visible(self):
        self.assertIsNone(revision.latest(self.root))
        self.assertEqual([r["id"] for r in revision.segments(self.root,1)["segments"]],["a"])
        (self.root/"revision-00000099").mkdir()
        (self.root/"revision-00000099"/"translated.pdf").touch()
        (self.root/".revise-in-progress").mkdir()
        self.assertIsNone(revision.latest(self.root))
        for page in (0,2,True):
            with self.assertRaises(ValueError):
                revision.segments(self.root,page)

    def test_concurrent_revision_is_rejected_before_preparing_runtime(self):
        path = self.root/"edits.json"
        write(path,self.changes)
        with worker_lock(self.root,"revision.lock") as held:
            self.assertTrue(held)
            with self.assertRaisesRegex(ValueError,"正在写回"):
                revision.revise(self.root,path)

    def test_publish_next_revision_or_keep_previous_on_failed_render(self):
        class FakeServer:
            def __init__(self,*args):
                pass
            def __enter__(self):
                return self
            def __exit__(self,*args):
                pass
            def revise(self, job, rows):
                self_check = job.folder/"paper.pdf"
                assert self_check.read_bytes() == b"original"
                for attr,name in (("mono","m.pdf"),("dual","d.pdf")):
                    path = job.folder/name
                    path.write_bytes(b"native output")
                    setattr(job,attr,path)
                job.review_store = job.folder/"native-segments.json"
                write(job.review_store,rows)
                job.revision += 1
                job.revision_error = ""
        def fake_apply(rows, changes):
            rows[0]["translation"] = changes[0]["translation"]
            return rows
        def fake_job(_identifier, path, _config):
            return SimpleNamespace(folder=path,figure_labels=[],rendered_labels=0)

        modules = {"upstream_ui.document_engine":SimpleNamespace(Job=fake_job,DocumentEngine=FakeServer),
                   "review":SimpleNamespace(apply_edits=fake_apply)}
        def run(changes):
            with tempfile.TemporaryDirectory(dir=self.root) as work:
                return revision._render(self.root,changes,Path(work))

        originals = [(p, hashlib.sha256(p.read_bytes()).digest()) for p in self.initial.iterdir()]
        with patch.dict("sys.modules",modules):
            result = run(self.changes)
            self.assertEqual(result,revision.latest(self.root))
            self.assertEqual(revision.segments(self.root,1)["segments"][0]["translation"],"研究结果")
            self.assertEqual(json.loads(Path(result["receipt"]).read_text())["model_requests"],0)
            with self.assertRaisesRegex(ValueError,"已更新"):
                run({"revision":0,"edits":self.changes})
            with patch.object(FakeServer,"revise",side_effect=RuntimeError("layout failed")):
                with self.assertRaisesRegex(RuntimeError,"layout failed"):
                    run(self.changes)
            self.assertEqual(revision.latest(self.root),result)
            self.assertFalse((self.root/"revision-00000002").exists())
        self.assertTrue(all(hashlib.sha256(p.read_bytes()).digest() == digest for p,digest in originals))


if __name__ == "__main__":
    unittest.main()
