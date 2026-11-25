"""Verified downloads and subprocess management for RTranslate."""
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    sha256: str
    size: int


_CHUNK = 1024 * 1024
PYTHON = "3.12.16"
UV_WHEELS = {
    ("Darwin", "arm64"): Asset(
        "uv-0.13.9-py3-none-macosx_11_0_arm64.whl",
        "https://files.pythonhosted.org/packages/f3/a7/a8d7b074798c7fb3b26b37584b8fe2e29df2ccdc24ce7902caab2a53edbf/uv-0.13.9-py3-none-macosx_11_0_arm64.whl",
        "66c512266d633bef48384a1f53003f41f2a8e7b36fe6e7fbfd3e0fd52d96fa63", 14660755),
    ("Windows", "amd64"): Asset(
        "uv-0.13.9-py3-none-win_amd64.whl",
        "https://files.pythonhosted.org/packages/dd/ef/ceeabf3631117f7354787d9fb1ffe943899e59ed4b887a696ed547e47e76/uv-0.13.9-py3-none-win_amd64.whl",
        "6a44b2ed8111d2f60d2b92627c5dccda3f7ed7570b18ce115e569cd68c28246b", 15662317),
    ("Darwin", "x86_64"): Asset(
        "uv-0.13.9-py3-none-macosx_10_12_x86_64.whl",
        "https://files.pythonhosted.org/packages/1e/db/08e9ef4879bd667e6d7b4705e594e5bd3e4585f10fc63cb16b9fa2fac63e/uv-0.13.9-py3-none-macosx_10_12_x86_64.whl",
        "c16ed11e013557536b3e60065f1b912f6407bd6518e041b961e23ef9e6b4fbf5", 17529974),
}
LAYOUT = Asset(
    "doclayout_yolo_docstructbench_imgsz1024.onnx",
    "https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/doclayout_yolo_docstructbench_imgsz1024.onnx",
    "fece9af02f618b603ff7921ccec6861d13e7e1f9830e091dfb7e8ad9311e5b21", 75324598)
FONT = Asset(
    "SourceHanSerifCN-Regular.ttf",
    "https://raw.githubusercontent.com/funstory-ai/BabelDOC-Assets/refs/heads/main/fonts/SourceHanSerifCN-Regular.ttf",
    "8ba5ec09db04b1d1599edeff3fb5627ca11eaaf85e339e5c32684cb94e806993", 14047768)


def _report(action, count, total, status):
    status(f"{action} {count * 100 / total:.0f}%（{count / 1048576:.0f} / {total / 1048576:.0f} MiB）")


def _verified(path, spec, check, status):
    check()
    if not path.is_file() or path.stat().st_size != spec.size:
        return False
    status("正在校验 " + spec.name)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK):
            check()
            digest.update(chunk)
    check()
    return digest.hexdigest() == spec.sha256


@contextmanager
def _download_lock(path, check, status):
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
        else:
            import fcntl
        notified = False
        while True:
            check()
            try:
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if not notified:
                    status("正在等待另一个原译任务准备排版组件")
                    notified = True
                time.sleep(.2)
        # Closing this descriptor releases the OS lock, including after a crash.
        yield


class _HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise ValueError("资源下载被重定向至非 HTTPS 地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verified_tls_context():
    """Use the caller's explicit CAs or the OS trust bundle with hostname verification."""
    cafile, capath = os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR")
    if cafile or capath:
        # Load explicitly so a broken custom CA path cannot quietly select
        # some other trust store. Do not broaden an operator's trust override.
        return ssl.create_default_context(cafile=cafile, capath=capath)
    tls = ssl.create_default_context()
    if sys.platform == "darwin" and Path("/etc/ssl/cert.pem").is_file():
        tls.load_verify_locations(cafile="/etc/ssl/cert.pem")
    try:
        import certifi
    except ImportError:
        pass
    else:
        tls.load_verify_locations(cafile=certifi.where())
    return tls


def _downloader():
    return build_opener(HTTPSHandler(context=verified_tls_context()), _HTTPSRedirect())


def _download(spec, partial, opener, check, status):
    check()
    count = partial.stat().st_size if partial.exists() else 0
    request = Request(spec.url, headers={"User-Agent": "FLA-Translate/2", "Accept-Encoding": "identity"})
    if count:
        request.add_header("Range", f"bytes={count}-")
    with opener.open(request, timeout=15) as response:
        check()
        if response.status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not match or int(match[1]) != count or int(match[3]) != spec.size:
                partial.unlink(missing_ok=True)
                raise OSError("下载源返回的断点位置不正确。")
        elif response.status == 200:
            count = 0
        else:
            raise OSError("下载源未返回资源文件。")
        next_report = 0.0
        read = getattr(response, "read1", response.read)
        with partial.open("ab" if count else "wb") as out:
            while True:
                check()
                chunk = read(_CHUNK)
                if not chunk:
                    break
                count += len(chunk)
                if count > spec.size:
                    raise ValueError("所下载资源的大小与发行版本不一致。")
                out.write(chunk)
                now = time.monotonic()
                if now >= next_report:
                    _report("正在下载 " + spec.name, count, spec.size, status)
                    next_report = now + .4
    check()
    if count != spec.size:
        raise OSError("资源下载尚未完成。")


def _reuse_asset(spec, old, cache, check, status):
    """Publish a previously verified download in the new cache without moving the old file."""
    target = cache / spec.name
    if target.exists() or not _verified(old / spec.name, spec, check, status):
        return
    cache.mkdir(parents=True, exist_ok=True)
    with _download_lock(cache / (spec.name + ".lock"), check, status):
        if target.exists():
            return
        source = old / spec.name
        try:
            os.link(source, target)
        except OSError:
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as dest:
                pending = dest.name
            try:
                shutil.copyfile(source, pending)
                check()
                os.replace(pending, target)
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)


def _obtain(spec, directories, cache, check, status):
    for folder in directories:
        local = folder / spec.name
        if local.is_file():
            if not _verified(local, spec, check, status):
                raise ValueError(f"离线资源校验失败，请重新复制 {spec.name}。")
            return local

    cache.mkdir(parents=True, exist_ok=True)
    target = cache / spec.name
    partial = cache / (spec.name + ".part")
    with _download_lock(cache / (spec.name + ".lock"), check, status):
        if _verified(target, spec, check, status):
            return target
        opener = _downloader()
        for attempt in range(2):
            check()
            if partial.exists() and partial.stat().st_size >= spec.size:
                if _verified(partial, spec, check, status):
                    os.replace(partial, target)
                    return target
                partial.unlink()
            status("正在连接下载源 " + spec.name)
            try:
                _download(spec, partial, opener, check, status)
            except InterruptedError:
                raise
            except (URLError, OSError) as exc:
                if isinstance(exc, HTTPError) and exc.code == 416:
                    partial.unlink(missing_ok=True)
                if attempt:
                    raise ValueError(f"下载 {spec.name} 失败。可重试续传，或将原版资源放入 RTRANSLATE_OFFLINE_ASSETS 所指的目录。") from exc
                continue
            if _verified(partial, spec, check, status):
                os.replace(partial, target)
                return target
            partial.unlink(missing_ok=True)
        raise ValueError(f"{spec.name} 未通过 SHA-256 校验，请重新下载。")



def _env(root):
    # Pass networking and OS settings, never credentials from unrelated engines.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "TMP", "TEMP", "TMPDIR", "HOME", "LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY",
            "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_CACHE_DIR=str(root / "uv-cache"),
               UV_SYSTEM_CERTS="true", UV_NO_PROGRESS="1",
               UV_NO_CONFIG="1", PYTHONUTF8="1", NO_COLOR="1")
    return env


def _launch(args, **kwargs):
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([str(arg) for arg in args], **kwargs)


def _stop(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def _run(args, env, check, stage):
    with tempfile.TemporaryFile() as log:
        p = _launch(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env)
        try:
            while p.poll() is None:
                check()
                time.sleep(.15)
            if p.returncode:
                raise RuntimeError(stage + "失败，请检查到 GitHub 与 PyPI 的网络连接、磁盘空间及系统版本。")
        finally:
            _stop(p)


