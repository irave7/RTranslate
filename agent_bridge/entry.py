"""Absolute script entry point for embedded Windows Python and detached workers."""
from pathlib import Path
import sys

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    sys.path[:] = [str(here.parent)] + [part for part in sys.path if Path(part).resolve() != here]
    from agent_bridge.cli import main
    raise SystemExit(main())
