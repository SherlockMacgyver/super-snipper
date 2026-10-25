"""Super Snipper launcher: DPI-aware region snips, screen recording and live markup.

Captures physical screen pixels, saves snips to Pictures/Screenshots and
recordings to Videos/Captures, and copies snips to the clipboard. The code
lives in the ``supersnipper`` package beside this file; Python puts this
script's folder on sys.path when it is run directly.
"""

import sys

if __name__ == "__main__":
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        sys.argv.append("--tray")
    try:
        from supersnipper.cli import run
    except ImportError as exc:
        # pythonw has no console: a missing dependency must still be visible.
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"{exc}\n\nInstall the requirements for this Python: {sys.executable}",
            "Super Snipper error",
            0x10,
        )
        raise SystemExit(1)
    raise SystemExit(run())
