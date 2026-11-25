"""Download the private PDF/OCR environment without sending any paper to a model."""
from pathlib import Path
import subprocess
import sys


def prepare_assets(engine):
    from upstream_runtime import prepare
    runtime = prepare(lambda:None, lambda message:print(message, flush=True))
    args = [str(runtime.python), "-I", "-B", "-X", "utf8", str(Path(__file__).resolve()), str(engine)]
    subprocess.run(args, env=runtime.env(), check=True)
    print(f"RTranslate 排版及英文 OCR 环境已准备好：{runtime.root}")


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root), str(root / "upstream_ui")]
    from babeldoc.assets.assets import warmup
    from scanned import prepare_english_ocr
    warmup()
    prepare_english_ocr(lambda:None, lambda message:print(message, flush=True))
