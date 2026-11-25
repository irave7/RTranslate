"""Create private workspaces and manage the independent layout worker."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from .queue import TaskQueue
from .privacy import native_env
from . import outputs

ENTRY = Path(__file__).resolve().with_name("entry.py")
LIMIT = 128 * 1024 * 1024


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    pending = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as stream:
            pending = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False)
        for attempt in range(10):
            try:
                os.replace(pending, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 9:
                    raise
                time.sleep(.03)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


@contextmanager
def worker_lock(directory, name):
    """The OS releases the lock on exit, including after an interrupted agent session."""
    with (directory / name).open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        yield True


def active(directory):
    for name in ("bootstrap.lock", "engine.lock"):
        with worker_lock(directory, name) as acquired:
            if not acquired:
                return True
    return False


def job_info(directory):
    info = directory / "job.json"
    if not info.is_file():
        raise ValueError("指定目录不是原译 RTranslate 翻译任务。")
    data = read(info)
    # Before selectable formats, every job rendered both variants.
    data.setdefault("output", "both")
    if data["output"] not in outputs.MODES:
        raise ValueError("任务中记录了无效的输出类型。")
    return data


def status(directory):
    mode = job_info(directory)["output"]
    result = directory / "result"
    if (result / "receipt.json").is_file():
        from .revision import latest
        return {"state":"done", **read(result / "receipt.json"), "revision":0,
                "output":mode, **outputs.paths(mode, result),
                **(latest(directory) or {})}
    st = read(directory / "state.json")
    counts = TaskQueue(directory).counts()
    if counts["cancelled"]:
        st = {"state":"cancelled", "detail":"可用 resume 延续已答的批次。"}
    elif st["state"] in {"working", "preparing", "starting"} and not active(directory):
        if st["state"] != "starting" or time.time() - st.get("started", 0) > 15:
            st = {"state":"interrupted", "detail":"工作进程已停止，可运行 resume。"}
    return {**st, "requests":counts, "output":mode}


def spawn(directory):
    args = [sys.executable, str(ENTRY), "_bootstrap", "--job", str(directory)]
    with (directory / "worker.log").open("ab") as log:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=native_env(),
                         start_new_session=os.name != "nt", close_fds=True,
                         creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    return {"state":"starting", "job":str(directory), "output":job_info(directory)["output"]}


def create(source, directory, glossary, figures, auto_terms, *, output=None, remember_output=False):
    mode = outputs.requested(output, remember_output)
    extension = source.suffix.lower()
    if extension not in {".txt", ".pdf"} or not source.is_file():
        raise ValueError("请选择英文 PDF 或 TXT。")
    if not 0 < source.stat().st_size <= LIMIT:
        raise ValueError("输入需大于 0 字节且至多 128 MB。")
    if glossary is not None:
        if glossary.stat().st_size > 1024*1024:
            raise ValueError("术语表请限制在 1 MiB 以内。")
        terms = glossary.read_text(encoding="utf-8-sig")
    else:
        terms = ""
    for line in filter(str.strip, terms.splitlines()):
        en, delim, zh = line.partition("=")
        if not delim or not en.strip() or not zh.strip():
            raise ValueError("术语表应每行写作 英文 = 中文。")
    if directory.exists():
        raise ValueError("任务目录已存在。请选用新路径，或使用 status / resume 查看已有任务。")
    directory.mkdir(mode=0o700, parents=True)
    try:
        shutil.copyfile(source, directory / ("source" + extension))
        TaskQueue(directory)
        write(directory / "state.json", {"state":"starting", "started":time.time()})
        write(directory / "job.json", {"format":extension[1:], "glossary":terms,
                                      "figures":figures, "auto_terms":auto_terms, "min_text_length":2, "output":mode})
        if remember_output:
            outputs.preferences(mode)
    except BaseException:
        shutil.rmtree(directory)
        raise
    return spawn(directory)


def bootstrap(directory):
    from upstream_runtime import prepare
    with worker_lock(directory, "bootstrap.lock") as acquired:
        if not acquired:
            return
        with worker_lock(directory, "engine.lock") as engine_free:
            if not engine_free or (directory / "result/receipt.json").is_file():
                return
        queue = TaskQueue(directory)

        def check():
            if queue.counts()["cancelled"]:
                raise InterruptedError("任务已取消。")

        def preparing(msg):
            write(directory / "state.json", {"state":"preparing", "detail":msg})
        try:
            preparing("正在准备 PDF 排版与识别环境。")
            runtime = prepare(check, preparing)
            check()
            env = native_env(runtime.env())
            env["RTRANSLATE_TRANSLATION_CACHE"] = str(directory / "native-cache")
            for var in ("NO_PROXY", "no_proxy"):
                env[var] = ",".join(filter(None, [env.get(var), "127.0.0.1", "localhost", "::1"]))
            command = [str(runtime.python), str(ENTRY), "_engine", "--job", str(directory)]
            # Run from the job, not the package folder (which may be read-only).
            result = subprocess.run(command, cwd=directory, env=env, stdin=subprocess.DEVNULL)
            if result.returncode and not queue.counts()["cancelled"]:
                raise RuntimeError("排版进程意外退出，详见任务目录的 worker.log。")
        except Exception as exc:
            state = "cancelled" if queue.counts()["cancelled"] else "error"
            write(directory / "state.json", {"state":state, "detail":str(exc)})
