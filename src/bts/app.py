from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    # Ensure project root (parent of src) is CWD when launched via module
    root = Path(__file__).resolve().parents[2]
    if str(root / "src") not in sys.path:
        sys.path.insert(0, str(root / "src"))
    from bts.ui import run_app

    raise SystemExit(run_app(root=root))


if __name__ == "__main__":
    main()
