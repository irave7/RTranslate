"""Create the pinned, private RTranslate PDF worker on first use."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import shutil
import tempfile
import zipfile

from .bootstrap import FONT, LAYOUT, PYTHON, UV_WHEELS, _download_lock, _env, _obtain, _run

HERE = Path(__file__).resolve().parent
PLATFORMS = {("Darwin", "arm64"): "macos-arm64", ("Darwin", "x86_64"): "macos-x64",
             ("Windows", "amd64"): "windows-x64"}


def cache_root():
    if path := os.environ.get("RTRANSLATE_RUNTIME_CACHE"):
        return Path(path).expanduser().resolve()
    base = (Path(os.environ.get("LOCALAPPDATA") or Path.home()/"AppData/Local") if os.name == "nt"
            else Path.home() / "Library/Caches")
    return base / "RTranslate" / "pdf"


def workspace_dir():
    if path := os.environ.get("RTRANSLATE_WORK_DIR"):
        return Path(path).expanduser().resolve()
    return cache_root().parent / "workspace"


@dataclass(frozen=True)
class Runtime:
    python: Path
    model: Path
    font: Path
    root: Path

    def env(self):
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "_MEIPASS2"):
            env.pop(key, None)
        env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                   RTRANSLATE_LAYOUT_MODEL=str(self.model), RTRANSLATE_BABELDOC_CACHE=str(self.root/"babeldoc"))
        return env


def _install_uv(archive, target):
    with zipfile.ZipFile(archive) as wheel:
        suffix = ".data/scripts/" + target.name
        member, = (name for name in wheel.namelist() if name.endswith(suffix))
        with wheel.open(member) as source, tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as dest:
            pending = Path(dest.name)
            try:
                shutil.copyfileobj(source, dest)
            except BaseException:
                pending.unlink(missing_ok=True)
                raise
    try:
        pending.chmod(0o700)
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)


def _isolate_babeldoc(venv):
    # BabelDOC 0.7.1 hardcodes the home cache. Its spawned workers must all use our private asset directory.
    lib = venv / ("Lib" if os.name == "nt" else "lib/python3.12") / "site-packages/babeldoc/const.py"
    original = 'CACHE_FOLDER = Path.home() / ".cache" / "babeldoc"'
    patched = 'CACHE_FOLDER = Path(os.environ.get("RTRANSLATE_BABELDOC_CACHE", Path.home() / ".cache" / "babeldoc"))'
    content = lib.read_text(encoding="utf-8")
    if patched in content:
        return
    if content.count(original) != 1:
        raise RuntimeError("BabelDOC 版本与锁定的资源配置不符。")
    # uv may link original wheel files from its cache. Replace the file rather than mutating a shared inode.
    with tempfile.NamedTemporaryFile("w", dir=lib.parent, delete=False, encoding="utf-8") as dest:
        pending = Path(dest.name)
        dest.write(content.replace(original, patched))
    try:
        os.replace(pending, lib)
    finally:
        pending.unlink(missing_ok=True)


def prepare(check_cancelled, status):
    machine = platform.machine().lower()
    target = (platform.system(), "amd64" if os.name == "nt" and machine == "x86_64" else machine)
    if target not in PLATFORMS:
        raise RuntimeError("原译支持 Apple 芯片及 Intel Mac、Windows x64。")
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = HERE / ("engine-" + PLATFORMS[target] + ".lock")
    spec = UV_WHEELS[target]
    digest = hashlib.sha256(lock.read_bytes() + PYTHON.encode() + spec.sha256.encode()).hexdigest()[:16]
    venv = root / ("engine-" + digest)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    provided = os.environ.get("RTRANSLATE_OFFLINE_ASSETS")
    sources = [Path(provided)] if provided else []
    with _download_lock(root / "install.lock", check_cancelled, status):
        check_cancelled()
        if os.name == "nt":
            from .windows_vc import prepare_system_crt
            prepare_system_crt(root, check_cancelled, status)
        ready = venv / ".installed"
        if not (python.is_file() and ready.is_file()):
            status("正在准备原译的独立排版运行环境…")
            uv = root / ("uv.exe" if os.name == "nt" else "uv")
            _install_uv(_obtain(spec, sources, root/"assets", check_cancelled, status), uv)
            env = _env(root)
            _run([uv,"python","install","--no-bin","--no-registry",PYTHON],env,check_cancelled,"获取专用 Python")
            _run([uv,"venv","--clear","--managed-python","--no-python-downloads","--python",PYTHON,venv],
                 env,check_cancelled,"创建排版环境")
            status("正在获取经过校验的 PDF 解析与排版组件…")
            _run([uv,"pip","sync","--require-hashes","--only-binary",":all:","--index-url","https://pypi.org/simple",
                  "--python",python,lock],env,check_cancelled,"安装排版组件")
            _isolate_babeldoc(venv)
            _run([python,"-I","-B","-c","import babeldoc, onnxruntime, pymupdf, cv2, openai, tomlkit"],
                 env,check_cancelled,"验证排版组件")
            ready.touch()
        model = _obtain(LAYOUT,sources,root/"babeldoc/models",check_cancelled,status)
        font = _obtain(FONT,sources,root/"babeldoc/fonts",check_cancelled,status)
    return Runtime(python,model,font,root)
