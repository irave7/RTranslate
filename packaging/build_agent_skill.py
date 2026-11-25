"""Build portable host-skill and local Codex marketplace archives from the same sources."""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "rtranslate"
SOURCE_DIRS = (
    "agent_bridge", "upstream_ui/retainpdf_support", "upstream_ui/pdf2zh_next/config",
    "upstream_ui/pdf2zh_next/utils", "upstream_ui/pdf2zh_next/translator/rate_limiter",
)
SOURCE_FILES = (
    "LICENSE", "UPSTREAMS.md", "packaging/RTranslate-NOTICES.md",
    "upstream_runtime/__init__.py", "upstream_runtime/bootstrap.py", "upstream_runtime/endpoints.py",
    "upstream_runtime/percentages.py", "upstream_runtime/windows_vc.py", "upstream_runtime/engine-requirements.in",
    "upstream_runtime/engine-macos-arm64.lock", "upstream_runtime/engine-macos-x64.lock", "upstream_runtime/engine-windows-x64.lock",
    "upstream_ui/LICENSE", "upstream_ui/UPSTREAM.md", "upstream_ui/document_engine.py", "upstream_ui/output_documents.py",
    "upstream_ui/figure_labels.py", "upstream_ui/figure_preflight.py", "upstream_ui/figure_translation.py",
    "upstream_ui/footnotes.py", "upstream_ui/inference_work.py", "upstream_ui/job.py", "upstream_ui/review.py",
    "upstream_ui/scanned.py", "upstream_ui/task_progress.py", "upstream_ui/pdf2zh_next/__init__.py",
    "upstream_ui/pdf2zh_next/const.py", "upstream_ui/pdf2zh_next/high_level.py",
    "upstream_ui/pdf2zh_next/translator/__init__.py", "upstream_ui/pdf2zh_next/translator/base_rate_limiter.py",
    "upstream_ui/pdf2zh_next/translator/base_translator.py", "upstream_ui/pdf2zh_next/translator/cache.py",
    "upstream_ui/pdf2zh_next/translator/freellmapi.py", "upstream_ui/pdf2zh_next/translator/named_numbers.py",
    "upstream_ui/pdf2zh_next/translator/utils.py", "upstream_ui/pdf2zh_next/translator/translator_impl/freellmapi.py",
    "upstream_ui/pdf2zh_next/translator/translator_impl/openai.py",
)
SOURCE_SUFFIXES = {
    ".py", ".cmd", ".sh", ".yaml", ".json", ".toml", ".lock", ".in",
    ".mjs", ".png", ".md", ".txt", ".tsv",
}
REQUIRED = ("agent_bridge/__main__.py", "agent_bridge/entry.py", "skills/rtranslate/SKILL.md",
            "skills/rtranslate/scripts/rtranslate.cmd", "skills/rtranslate/scripts/rtranslate.sh")


def files(directory):
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if any(part.startswith(".") or part in {"__pycache__", "tests"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Refusing to bundle symbolic link {path}")
        if path.is_file() and (path.name == "LICENSE" or path.suffix in SOURCE_SUFFIXES):
            yield path


def skill_files():
    for required in REQUIRED:
        if not (ROOT / required).is_file():
            raise FileNotFoundError(required)
    for path in files(SKILL):
        yield path, path.relative_to(SKILL).as_posix()
    for dirname in SOURCE_DIRS:
        for path in files(ROOT / dirname):
            yield path, "engine/" + path.relative_to(ROOT).as_posix()
    for name in SOURCE_FILES:
        yield ROOT / name, "engine/" + name


def build_zip(target, members):
    seen = set()
    with tempfile.TemporaryDirectory(dir=target.parent) as work:
        pending = Path(work) / target.name
        with zipfile.ZipFile(pending, "w") as archive:
            for path, name in members:
                if name in seen:
                    raise ValueError(f"Duplicate ZIP entry {name}")
                seen.add(name)
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
                data = path.read_bytes()
                if path.suffix != ".png":
                    data = data.replace(b"\r\n", b"\n")
                if name.endswith(".cmd"):
                    data = data.replace(b"\n", b"\r\n")
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(pending, target)
    return f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    out = parser.parse_args().output
    out.mkdir(parents=True, exist_ok=True)
    members = list(skill_files())
    checks = [build_zip(out / "rtranslate.zip", members)]
    templates = ROOT / "packaging" / "agent_plugin"
    base = "plugins/rtranslate/"
    plugin = [(templates / "marketplace.json", ".agents/plugins/marketplace.json"),
              (templates / "plugin.json", base + "plugin.json")]
    plugin.extend((path, base + "skills/rtranslate/" + name) for path, name in members)
    checks.append(build_zip(out / "rtranslate-codex-plugin.zip", plugin))
    receipt = "\n".join(checks) + "\n"
    (out / "SHA256SUMS.txt").write_bytes(receipt.encode("ascii"))
    print(receipt, end="")


if __name__ == "__main__":
    main()
