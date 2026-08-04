"""CLI entry for scripts/Update-BTS.ps1 — check + apply update."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bts.update import apply_best_update, check_for_update  # noqa: E402


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    result = check_for_update(root)
    if result.message:
        print(result.message)
    if result.error:
        print(result.error, file=sys.stderr)
        return 1
    print(apply_best_update(root, result.release))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
