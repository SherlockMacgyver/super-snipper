"""Still snips: readable unique filenames, the region snip and box capture."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PIL import ImageGrab

from . import config, overlays, winapi


_CAPTURE_NAME_LOCK = threading.Lock()


_CAPTURE_NAME_COUNTS: dict[tuple[Path, str, str], tuple[str, int]] = {}


def _next_capture_path(directory: Path, prefix: str, suffix: str) -> Path:
    """Readable candidates; actual exclusive writes/publication enforce ownership."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    key = (directory, prefix, suffix)
    with _CAPTURE_NAME_LOCK:
        old_stamp, count = _CAPTURE_NAME_COUNTS.get(key, ("", 0))
        count = count + 1 if stamp == old_stamp else 1
        while True:
            number = f" ({count})" if count > 1 else ""
            path = directory / f"{prefix} {stamp}{number}{suffix}"
            if not path.exists():
                _CAPTURE_NAME_COUNTS[key] = (stamp, count)
                return path
            count += 1


def timestamped_path(prefix: str = "SuperSnip") -> Path:
    return _next_capture_path(config.SCREENSHOT_DIR, prefix, ".jpg")


def capture_snip() -> Optional[Path]:
    winapi.set_dpi_awareness()
    screenshot = ImageGrab.grab(all_screens=True)
    logging.info("Captured physical screen image: %sx%s", screenshot.width, screenshot.height)

    overlay = overlays.SnipOverlay(screenshot)
    box = overlay.run()
    if not box:
        logging.info("Snip cancelled")
        return None

    snip = screenshot.crop(box).convert("RGB")
    path = timestamped_path()
    with path.open("xb") as stream:
        snip.save(stream, "JPEG", quality=config.JPG_QUALITY, optimize=True)
    winapi.copy_image_to_clipboard(snip)
    logging.info("Saved %s (%sx%s) and copied to clipboard", path, snip.width, snip.height)
    return path


def capture_box(box_text: str, copy: bool = True, prefix: str = "SuperSnip") -> Path:
    parts = [int(part.strip()) for part in box_text.split(",")]
    if len(parts) != 4:
        raise ValueError("--capture-box must be left,top,right,bottom")
    left, top, right, bottom = parts
    if right <= left or bottom <= top:
        raise ValueError("--capture-box right/bottom must be greater than left/top")

    winapi.set_dpi_awareness()
    screenshot = ImageGrab.grab(all_screens=True)
    right = min(right, screenshot.width)
    bottom = min(bottom, screenshot.height)
    left = max(0, left)
    top = max(0, top)
    snip = screenshot.crop((left, top, right, bottom)).convert("RGB")
    path = timestamped_path(prefix=prefix)
    with path.open("xb") as stream:
        snip.save(stream, "JPEG", quality=config.JPG_QUALITY, optimize=True)
    if copy:
        winapi.copy_image_to_clipboard(snip)
    logging.info("Saved test/capture %s (%sx%s)", path, snip.width, snip.height)
    print(path)
    return path


def parse_box(box_text: str, option_name: str) -> Tuple[int, int, int, int]:
    parts = [int(part.strip()) for part in box_text.split(",")]
    if len(parts) != 4:
        raise ValueError(f"{option_name} must be left,top,right,bottom")
    left, top, right, bottom = parts
    if right <= left or bottom <= top:
        raise ValueError(f"{option_name} right/bottom must be greater than left/top")
    return left, top, right, bottom
