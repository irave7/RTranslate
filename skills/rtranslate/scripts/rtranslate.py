"""Run the bundled agent bridge while keeping user paths relative to their workspace."""
from pathlib import Path
import runpy
import sys


def main():
    skill = Path(__file__).resolve().parent.parent
    engine = skill / "engine"
    if not engine.is_dir() and skill.parent.name == "skills":
        engine = skill.parent.parent  # Source checkout
    if not (engine / "agent_bridge" / "__main__.py").is_file():
        raise SystemExit("缺少原译引擎。请重新解压完整的 rtranslate.zip。")
    sys.path.insert(0, str(engine))
    if sys.argv[1:] == ["--prepare"]:
        sys.path.insert(0, str(skill / "scripts"))
        from prepare_runtime import prepare_assets
        prepare_assets(engine)
        return
    if sys.argv[1:2] == ["--install-skill"]:
        sys.path.insert(0, str(skill / "scripts"))
        from install_skill import install
        install(skill, sys.argv[2:])
        return
    runpy.run_module("agent_bridge", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
