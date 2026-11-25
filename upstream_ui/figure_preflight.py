"""Read figures alongside the translation engine using a separate MuPDF process."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


def _can_overlap():
    # The worker can load the layout model at the same time as the main engine.
    # Leave room for both inference runtimes and avoid competing on small CPUs.
    try:
        import psutil
        ram = psutil.virtual_memory()
        return ((os.cpu_count() or 1) >= 6 and ram.total >= 16 * 1024**3
                and ram.available >= 5 * 1024**3)
    except (ImportError, OSError):
        return False


def _worker(source, directory):
    # The embedded Windows interpreter uses a ._pth file and may omit the
    # application root. Always use the UI directory paired with this worker.
    ui = Path(__file__).resolve().parent
    sys.path[:0] = [str(ui), str(ui.parent)]

    def watch_parent():
        # Only the parent holds the pipe's write end. Also release native OCR
        # resources when the app quits during a long blocking native call.
        try:
            os.read(0, 1)
        finally:
            os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    if hasattr(os, "nice"):
        os.nice(5)
    from figure_labels import extract
    with (directory / "events").open("w", encoding="utf-8") as events:
        def emit(event):
            events.write(json.dumps(event, ensure_ascii=False) + "\n")
            events.flush()

        labels = extract(source, lambda: None, lambda value: emit([value]),
                         on_progress=lambda done, total: emit([done, total]))
    # The parent reads this only after a successful child exit.
    (directory / "labels.json").write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")


class FigurePreflight:
    """Enter before body translation; consume result() once after body translation."""

    def __init__(self, source, job_folder, check, *, lock=None, parallel=None):
        self.source = Path(source).resolve()
        self.folder = Path(job_folder)
        self.check = check
        self.lock = lock if lock is not None else nullcontext()
        self.parallel = parallel
        self.process = None
        self.temp = None

    def __enter__(self):
        self.check()
        if not (_can_overlap() if self.parallel is None else self.parallel):
            return self
        try:
            self.temp = tempfile.TemporaryDirectory(prefix="figure-ocr-", dir=self.folder)
            self.directory = Path(self.temp.name).resolve()
            (self.directory / "events").touch()
            # No shell or system Python. All DLLs, models and tessdata resolve
            # in the same managed environment as the active translation engine.
            flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
            self.process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()),
                 str(self.source), str(self.directory)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags, env={**os.environ, "OMP_NUM_THREADS": "1"},
            )
        except OSError:
            # Resource exhaustion and restrictions on spawning are recoverable.
            self.__exit__(None, None, None)
        return self

    def result(self, status, *, on_progress=None):
        self.check()
        if self.process is not None:
            with (self.directory / "events").open("rb") as events:
                while True:
                    self.check()
                    finished = self.process.poll() is not None
                    while True:
                        start = events.tell()
                        line = events.readline()
                        if not line.endswith(b"\n"):
                            events.seek(start)
                            break
                        event = json.loads(line)
                        if len(event) == 1:
                            status(event[0])
                        elif on_progress:
                            on_progress(*event)
                    if finished:
                        break
                    try:
                        self.process.wait(.15)
                    except subprocess.TimeoutExpired:
                        pass
            if self.process.returncode == 0:
                return json.loads((self.directory / "labels.json").read_text(encoding="utf-8"))
            status("图内识别进程已退出，正在重新读取原图。")
        from figure_labels import extract
        with self.lock:
            return extract(self.source, self.check, status, on_progress=on_progress)

    def __exit__(self, *_):
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            finally:
                self.process.stdin.close()
                self.process = None
        if self.temp is not None:
            self.temp.cleanup()
            self.temp = None


if __name__ == "__main__":
    _worker(Path(sys.argv[1]), Path(sys.argv[2]))
