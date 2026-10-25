"""Paths, constants and logging shared by every Super Snipper module.

Super Snipper keeps its own state and logs under SUPER_SNIPPER_HOME, which
defaults to %LOCALAPPDATA%\\SuperSnipper. Captures go to the user's Pictures
and Videos folders. Other modules read these names as ``config.NAME`` at call
time, so tests and isolated runs can redirect them.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


class RecordingCancelled(Exception):
    """Raised when the user cancels recording setup before capture starts."""


def _data_dir() -> Path:
    override = os.environ.get("SUPER_SNIPPER_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is not set; set SUPER_SNIPPER_HOME to choose a state folder")
    return Path(local) / "SuperSnipper"


# The folder holding the launcher script and this package.
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = APP_DIR / "super_snipper.py"


def launch_command(args: list[str]) -> list[str]:
    """Every child uses this installation, without requiring system Python."""
    if FROZEN:
        return [str(APP_DIR / "SuperSnipper.exe"), *args]
    return [sys.executable, str(ENTRY_SCRIPT), *args]

DATA_DIR = _data_dir()
LOG_FILE = DATA_DIR / "super_snipper.log"
FFMPEG_LOG_FILE = DATA_DIR / "super_snipper_ffmpeg.log"
RECORDING_STATE_FILE = DATA_DIR / "super_snipper_recording.json"
RECORDING_STOP_FILE = DATA_DIR / "super_snipper_recording.stop"

CONTROL_PANEL_MUTEX_NAME = "Local\\StructuredChaos.SuperSnipper.ControlPanel"
# Explicit namespace for isolated automation; production uses the default.
PANEL_INSTANCE = os.environ.get("SUPER_SNIPPER_INSTANCE", "")
if PANEL_INSTANCE:
    if not PANEL_INSTANCE.isalnum():
        raise ValueError("SUPER_SNIPPER_INSTANCE must be alphanumeric")
    CONTROL_PANEL_MUTEX_NAME += "." + PANEL_INSTANCE
PANEL_COMMANDS = ("show", "hide", "reset", "dock-left", "dock-right", "dock-top",
                  "dock-bottom", "undock", "snip", "region", "arm-region", "full", "stop", "exit")

SCREENSHOT_DIR = Path.home() / "Pictures" / "Screenshots"
VIDEO_DIR = Path.home() / "Videos" / "Captures"
JPG_QUALITY = 92
DEFAULT_VIDEO_FPS = 30
DEFAULT_RECORD_COUNTDOWN_SECONDS = 3
DEFAULT_ARMED_CLICK_DELAY_MS = 250
DEFAULT_AUDIO_RATE = 48000
DEFAULT_AUDIO_BLOCK = 1024

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

CF_DIB = 8
GMEM_MOVEABLE = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
WDA_EXCLUDEFROMCAPTURE = 0x00000011
PANEL_LAUNCH_DELAY_MS = 220
PANEL_DOCK_MARGIN = 0
PANEL_SNAP_THRESHOLD = 48


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [super_snipper] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
