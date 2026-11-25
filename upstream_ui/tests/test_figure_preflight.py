import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from figure_preflight import FigurePreflight


class FigurePreflightTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "带 空格的文献.pdf"
        with pymupdf.open() as document:
            document.new_page()
            document.new_page()
            document.save(self.source)

    def test_serial_and_child_results_with_progress(self):
        for parallel in (True, False):
            events = []
            with FigurePreflight(self.source, self.folder, lambda:None, parallel=parallel) as scan:
                self.assertEqual(scan.process is not None, parallel)
                self.assertEqual(scan.result(lambda _:None, on_progress=lambda n,t:events.append((n,t))), [])
            self.assertEqual(events, [(1,2),(2,2)])
            self.assertEqual(list(self.folder.iterdir()), [self.source])

    def test_exit_while_body_is_cancelled(self):
        with self.assertRaises(InterruptedError):
            with FigurePreflight(self.source, self.folder, lambda:None, parallel=True) as scan:
                child = scan.process
                raise InterruptedError
        self.assertIsNotNone(child.poll())
        self.assertEqual(list(self.folder.iterdir()), [self.source])

    def test_cancel_during_result_wait(self):
        check = Mock(side_effect=[None,None,InterruptedError()])
        with self.assertRaises(InterruptedError):
            with FigurePreflight(self.source, self.folder, check, parallel=True) as scan:
                child = scan.process
                scan.result(lambda _:None)
        self.assertIsNotNone(child.poll())
        self.assertEqual(list(self.folder.iterdir()), [self.source])

    def test_interrupted_child_retries_sequentially_with_pdf_lock(self):
        from contextlib import contextmanager
        calls = []
        @contextmanager
        def lock():
            calls.append("acquired")
            yield
            calls.append("released")
        with FigurePreflight(self.source, self.folder, lambda:None, parallel=True, lock=lock()) as scan:
            scan.process.kill()
            self.assertEqual(scan.result(lambda _:None), [])
        self.assertEqual(calls, ["acquired", "released"])

    def test_parent_pipe_closed_stops_worker(self):
        with FigurePreflight(self.source, self.folder, lambda:None, parallel=True) as scan:
            scan.process.stdin.close()
            self.assertEqual(scan.process.wait(10), 1)

    def test_spawn_failure_uses_serial_path_and_removes_scratch(self):
        with patch("figure_preflight.subprocess.Popen", side_effect=OSError):
            with FigurePreflight(self.source, self.folder, lambda:None, parallel=True) as scan:
                self.assertIsNone(scan.process)
                self.assertEqual(scan.result(lambda _:None), [])
        self.assertEqual(list(self.folder.iterdir()), [self.source])
