"""Run the existing desktop translation pipeline using only the local host-agent relay."""
import os
from pathlib import Path
import shutil
import tempfile
import threading

from .jobs import job_info, worker_lock, write
from .outputs import paths
from .relay import AgentRelay
from .short_labels import contexts


def run(directory):
    from upstream_ui.document_engine import Job, DocumentEngine

    with worker_lock(directory, "engine.lock") as owner:
        if not owner:
            return
        info = job_info(directory)
        with tempfile.TemporaryDirectory(prefix="render-", dir=directory) as work:
            folder = Path(work)
            source = directory / ("source." + info["format"])
            name = "paper.pdf" if info["format"] == "pdf" else "input.txt"
            try:
                os.link(source, folder / name)
            except OSError:
                shutil.copyfile(source, folder / name)
            with AgentRelay(directory) as relay, DocumentEngine() as engine:
                config = dict(mode="agent", format=info["format"], model=relay.model, output=info["output"],
                              endpoint=relay.endpoint, key=relay.token, glossary=info["glossary"],
                              figureLabels=info["figures"], agent_auto_terms=info["auto_terms"],
                              agent_min_text_length=info.get("min_text_length",5))
                if config["agent_min_text_length"] < 5:
                    config["agent_source_prepared"] = lambda pdf: write(directory/"short-label-context.json",contexts(pdf))
                job = Job("agent", folder, config)
                finished = threading.Event()

                def observe():
                    previous = None
                    while not finished.wait(.4):
                        if relay.queue.counts()["cancelled"]:
                            job.cancel.set()
                        with engine.lock:
                            current = (job.detail, job.timing.snapshot()["percent"])
                        if previous != current:
                            write(directory / "state.json", {"state":"working","detail":current[0],"percent":current[1]})
                            previous = current
                watcher = threading.Thread(target=observe, daemon=True)
                write(directory / "state.json", {"state":"working","detail":"正在解析文献。","percent":0})
                watcher.start()
                try:
                    engine.translate(job, config)
                finally:
                    finished.set()
                    watcher.join()
                if job.status != "done" or relay.queue.counts()["cancelled"]:
                    write(directory / "state.json", {"state":"cancelled" if relay.queue.counts()["cancelled"] else "error",
                                                     "detail":job.detail})
                    return
                result = folder / "completed"
                result.mkdir()
                # Publish the PDFs and their receipt together, after all integrity checks.
                for kind, destination in paths(info["output"], result).items():
                    shutil.move(getattr(job, kind), destination)
                if job.review_store and job.review_store.is_file():
                    shutil.move(job.review_store, result / "segments.json")
                if job.figure_labels:
                    write(result / "figure-labels.json", job.figure_labels)
                original_pdf = folder / "paper.pdf"
                if job.engine_source != original_pdf or info["format"] == "txt":
                    inputs = result / ".inputs"
                    inputs.mkdir()
                    if job.engine_source != original_pdf:
                        shutil.copyfile(job.engine_source, inputs / "engine-source.pdf")
                    if info["format"] == "txt":
                        shutil.copyfile(original_pdf, inputs / "source.pdf")
                write(result / "receipt.json", {"pages":job.pages, "output":info["output"], "scanned_pages":job.scans,
                      "figure_labels":len(job.figure_labels), "figure_labels_written":job.rendered_labels,
                      "requests":relay.queue.counts(), "percent":100})
                os.rename(result, directory / "result")
