"""Read an agent job's editable native segments and publish immutable offline revisions."""
from pathlib import Path
import sys

if __name__ == "__main__" and not __package__:
    # An embedded interpreter may otherwise import agent_bridge/queue.py as stdlib queue.
    here = Path(__file__).resolve().parent
    sys.path[:] = [str(here.parent)] + [p for p in sys.path if Path(p).resolve() != here]

import json
import os
import re
import shutil
import subprocess
import tempfile

from agent_bridge.jobs import job_info, read, worker_lock, write
from agent_bridge.outputs import paths
from agent_bridge.privacy import native_env


def _latest(directory):
    initial = directory / "result"
    if not (initial / "receipt.json").is_file() or not (initial / "segments.json").is_file():
        raise ValueError("任务尚未生成可校订的译本。")
    versions = [(0, initial)]
    for path in directory.glob("revision-*"):
        match = re.fullmatch(r"revision-([0-9]{8})",path.name)
        if match and (path / "receipt.json").is_file():
            versions.append((int(match[1]),path))
    return max(versions)


def segments(directory, page):
    directory = Path(directory)
    version, latest = _latest(directory)
    pages = read(latest / "receipt.json")["pages"]
    if type(page) is not int or not 1 <= page <= pages:
        raise ValueError(f"页码须介于1与{pages}之间。")
    fields = ("id","source","translation","restoreMarkup")
    rows = [{k:r.get(k) for k in fields} for r in read(latest / "segments.json")
            if r.get("editable") and r.get("page") == page]
    return {"revision":version, "page":page, "pages":pages, "segments":rows}


def latest(directory):
    """Return paths for the newest committed revision, or None before the first."""
    directory = Path(directory)
    if not (directory/"result"/"segments.json").is_file():
        return None
    version, target = _latest(directory)
    if not version:
        return None
    mode = job_info(directory)["output"]
    return {"revision":version, "output":mode, **paths(mode, target), "receipt":str(target/"receipt.json")}


def _link(source, target):
    try:
        os.link(source,target)
    except OSError:
        shutil.copyfile(source,target)


def _render(directory, changes, temp):
    from upstream_ui.document_engine import Job, DocumentEngine
    from review import apply_edits

    info = job_info(directory)
    initial = directory / "result"
    version, base = _latest(directory)
    if isinstance(changes,dict):
        if changes.get("revision") != version:
            raise ValueError("译本已更新，请重新读取段落并核对校订内容。")
        changes = changes.get("edits")
    rows = apply_edits(read(base / "segments.json"),changes)
    if version >= 99999999:
        raise ValueError("修订版本数量已达上限。")
    receipt = read(initial / "receipt.json")
    source = temp / "paper.pdf"
    # Older tasks kept OCR and converted text inputs next to their exported PDFs.
    stored_inputs = initial/".inputs" if (initial/".inputs").is_dir() else initial
    source_stored = directory/"source.pdf" if info["format"] == "pdf" else stored_inputs/"source.pdf"
    if source_stored.is_file():
        _link(source_stored, source)
    else:
        raise ValueError("任务缺少原始 PDF。")
    mode = info["output"]
    job = Job("revision",temp,{"agent_min_text_length":info.get("min_text_length",5),"output":mode})
    job.scans = receipt["scanned_pages"]
    job.engine_source = stored_inputs / "engine-source.pdf"
    if not job.engine_source.is_file():
        job.engine_source = source
        if job.scans:
            raise ValueError("任务缺少初译使用的 OCR 输入。")
    labels_file = initial / "figure-labels.json"
    if labels_file.is_file():
        job.figure_labels = read(labels_file)
    elif receipt["figure_labels_written"]:
        raise ValueError("任务缺少初译使用的图内标签。")
    if (job.figure_labels or receipt["figure_labels_written"]) and (
            len(job.figure_labels) != receipt["figure_labels"]
            or sum(bool(re.search(r"[\u3400-\u9fff]",r.get("translation",""))) for r in job.figure_labels) < receipt["figure_labels_written"]):
        raise ValueError("图内标签与初译的记录不符，无法安全重排。")
    job.pages = receipt["pages"]
    job.mono_pages = job.pages if mode != "dual" else 0
    job.dual_pages = job.pages if mode != "mono" else 0
    job.revision = version
    with DocumentEngine() as engine:
        engine.revise(job,rows)
    if job.revision_error or job.revision != version + 1:
        raise RuntimeError(job.revision_error or "本次重排未完成。")
    if job.rendered_labels < receipt["figure_labels_written"]:
        raise ValueError("部分已译图内标签无法在校订版安全写回。")
    ready = temp / "publish"
    ready.mkdir()
    for kind, destination in paths(mode, ready).items():
        shutil.move(getattr(job, kind), destination)
    shutil.move(job.review_store,ready / "segments.json")
    write(ready / "receipt.json",dict(pages=receipt["pages"], output=mode, revision=job.revision, base_revision=version,
          edited_ids=[r["id"] for r in changes], model_requests=0,figure_labels_written=job.rendered_labels))
    target = directory / f"revision-{job.revision:08d}"
    if target.exists():
        raise ValueError("目标修订版本已存在。")
    ready.rename(target)
    return {"revision":job.revision, "output":mode, **paths(mode, target), "receipt":str(target/"receipt.json")}


def revise(directory, edits_file):
    """Run native dependencies in the same managed Python used for the original translation."""
    from upstream_runtime import prepare
    directory, edits_file = Path(directory).resolve(), Path(edits_file).resolve()
    _latest(directory)
    if edits_file.stat().st_size > 2_000_000:
        raise ValueError("校订文件须小于2 MB。")
    changes = json.loads(edits_file.read_text(encoding="utf-8-sig"))
    if not isinstance(changes,(list,dict)):
        raise ValueError("校订文件须为段落数组或含版本号和 edits 数组的对象。")
    with worker_lock(directory, "revision.lock") as acquired:
        if not acquired:
            raise ValueError("已有一项校订正在写回，请在完成后重试。")
        runtime = prepare(lambda:None, lambda *_:None)
        with tempfile.TemporaryDirectory(prefix=".revise-",dir=directory) as tmp:
            temp = Path(tmp)
            write(temp/"edits.json",changes)
            env = native_env(runtime.env())
            env["RTRANSLATE_TRANSLATION_CACHE"] = str(directory/"native-cache")
            cmd = [str(runtime.python), "-B", str(Path(__file__).resolve()),str(directory),str(temp)]
            with (temp/"worker.log").open("wb") as log:
                proc = subprocess.run(cmd, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                        stdout=log,stderr=subprocess.STDOUT, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if not (temp/"response.json").is_file():
                raise RuntimeError(f"校订进程意外退出（{proc.returncode}）。")
            result = read(temp/"response.json")
            if "error" in result:
                raise ValueError(result["error"])
            return result


if __name__ == "__main__":
    directory, temp = map(Path, sys.argv[1:])
    try:
        reply = _render(directory, read(temp/"edits.json"),temp)
    except Exception as err:
        reply = {"error":str(err)}
    write(temp/"response.json",reply)
