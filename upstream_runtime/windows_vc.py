"""Install the official Microsoft x64 runtime on first use if Windows lacks it."""
import ctypes
import os
from pathlib import Path

from .bootstrap import Asset, _obtain, _run

VC_REDIST = Asset(
    "VC_redist.x64.exe",
    "https://download.visualstudio.microsoft.com/download/pr/e9604e21-4b79-4096-a4b3-1b63d4b55b4d/"
    "CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe",
    "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b", 25635768,
)


def system_crt_ready():
    try:
        # These two imports are used by the native PDF engine. Both must
        # resolve from Windows, independent of the bootstrap Python directory.
        for library in ("msvcp140.dll", "msvcp140_1.dll"):
            ctypes.WinDLL(library, winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
    except OSError:
        return False
    return True


def prepare_system_crt(root, check, status):
    if system_crt_ready():
        return
    cache = root / "microsoft-runtime"
    cache.mkdir(exist_ok=True)
    status("正在从 Microsoft 下载并校验 Visual C++ x64 运行库（约 26 MB）…")
    offline = os.environ.get("RTRANSLATE_OFFLINE_ASSETS")
    installer = _obtain(VC_REDIST, [Path(offline)] if offline else [], cache, check, status)
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # The path crosses into PowerShell only through the process environment.
    # Windows displays its UAC confirmation for the publisher-signed installer.
    script = (
        "$ErrorActionPreference='Stop'; "
        "$p=Start-Process -FilePath $env:LLT_VC_SETUP -Verb RunAs -Wait -PassThru "
        "-ArgumentList '/install','/passive','/norestart'; "
        "if ($p.ExitCode -notin 0,1638,3010) { exit 1 }"
    )
    status("正在安装 Microsoft Visual C++ x64 运行库，请确认 Windows 的授权提示…")
    try:
        _run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
             {**os.environ, "LLT_VC_SETUP": str(installer.resolve())}, check, "安装运行库")
    except RuntimeError as exc:
        raise RuntimeError("Microsoft Visual C++ x64 运行库未安装。请重试原译任务，并在 Windows 提示时授权安装。") from exc
    if not system_crt_ready():
        raise RuntimeError("请重新启动 Windows 以完成 Microsoft Visual C++ x64 运行库安装，然后继续原译任务。")
