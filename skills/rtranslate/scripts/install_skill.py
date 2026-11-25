"""Install the verified skill into a host's documented discovery directory."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile


def inventory(directory):
    return {path.relative_to(directory): hashlib.sha256(path.read_bytes()).digest()
            for path in directory.rglob("*") if path.is_file() and "__pycache__" not in path.parts}


def install(source, argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=("shared", "workbuddy"), default="shared")
    parser.add_argument("--project", type=Path)
    args = parser.parse_args(argv)
    base = args.project.expanduser().resolve() if args.project else Path.home()
    hosts = base / (".codebuddy" if args.host == "workbuddy" else ".agents") / "skills"
    hosts.mkdir(parents=True, exist_ok=True)
    target = hosts / "rtranslate"
    from upstream_runtime.bootstrap import _download_lock
    with _download_lock(hosts / ".rtranslate-install.lock", lambda:None, lambda message:print(message,file=sys.stderr)):
        previous = None
        if target.is_symlink():
            raise ValueError(f"技能位置是符号链接，请保留并检查其目标目录：{target}")
        if target.exists():
            skill = target / "SKILL.md"
            if not skill.is_file() or "\nname: rtranslate\n" not in skill.read_text(encoding="utf-8"):
                raise ValueError(f"该目录已有其他内容：{target}")
            if not any(p.is_symlink() for p in target.rglob("*")) and inventory(target) == inventory(source):
                print(f"RTranslate 已安装：{target}")
                return
        with tempfile.TemporaryDirectory(dir=hosts, prefix=".rtranslate-stage-") as temp:
            staged = Path(temp) / "rtranslate"
            shutil.copytree(source,staged,ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if inventory(staged) != inventory(source):
                raise OSError("暂存的技能文件校验未通过。")
            if target.exists():
                backups = hosts / ".rtranslate-backups"
                backups.mkdir(exist_ok=True)
                previous = Path(tempfile.mkdtemp(dir=backups,prefix="before-update-")) / "rtranslate"
                target.rename(previous)
            try:
                staged.rename(target)
            except BaseException:
                if previous is not None:
                    previous.rename(target)
                raise
    print(f"RTranslate 已安装：{target}")
    if previous is not None:
        print(f"上一版保存在 {previous}")
