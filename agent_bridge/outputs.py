"""Per-user output preference and the formats pinned to each translation job."""
import json
import os
from pathlib import Path
import sys


MODES = ("mono", "dual", "both")
NAMES = {"mono": "translated.pdf", "dual": "bilingual.pdf"}


def add_output_arguments(parser):
    parser.add_argument("--output", choices=MODES, help="本次导出的 PDF 类型")
    parser.add_argument("--remember-output", action="store_true", help="将本次显式选定的类型设为当前用户的默认值")


def preference_file():
    if override := os.environ.get("RTRANSLATE_CONFIG_DIR"):
        folder = Path(override).expanduser()
    elif os.name == "nt":
        folder = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "RTranslate"
    elif sys.platform == "darwin":
        folder = Path.home() / "Library/Application Support/RTranslate"
    else:
        folder = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "rtranslate"
    return folder / "output.json"


def preferences(output=None, reset=False):
    from .jobs import write
    path = preference_file()
    if reset:
        path.unlink(missing_ok=True)
    if output is not None:
        if output not in MODES:
            raise ValueError("无效的 PDF 输出类型。")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write(path, {"output": output})
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"output": "mono", "saved": False}
    if not isinstance(stored, dict) or stored.get("output") not in MODES:
        raise ValueError("输出偏好无效，请使用 preferences --reset 恢复默认。")
    return {"output": stored["output"], "saved": True}


def requested(output, remember_output):
    if output is None:
        if remember_output:
            raise ValueError("--remember-output 须与 --output 一起使用。")
        return preferences()["output"]
    if output not in MODES:
        raise ValueError("无效的 PDF 输出类型。")
    return output


def paths(mode, directory):
    if mode not in MODES:
        raise ValueError("任务中记录了无效的输出类型。")
    return {key: str(directory / name) for key, name in NAMES.items() if mode in (key, "both")}
