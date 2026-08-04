"""Launch Battery Test Sequencer from project root."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Windows taskbar: use our app id/icon instead of python.exe
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "EBZnanoPower.BatteryTestSequencer"
        )
    except Exception:
        pass

from bts.app import main

if __name__ == "__main__":
    main()
