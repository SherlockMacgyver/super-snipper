"""Ordered video contact sheets, using the existing external FFmpeg installation."""

from __future__ import annotations

from dataclasses import dataclass
import io
import math
from pathlib import Path
import re
import subprocess
import threading
import time

from PIL import Image, PngImagePlugin

from .recording import resolve_ffmpeg

MAX_FRAMES = 1000
MAX_EDGE = 4096


class StoryboardCancelled(Exception):
    """Export cancelled before publication."""


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration: float
    width: int
    height: int


@dataclass(frozen=True)
class Grid:
    columns: int
    rows: int
    tile_width: int
    tile_height: int
    count: int

    @property
    def size(self):
        return self.columns * self.tile_width, self.rows * self.tile_height

    @property
    def empty(self):
        return self.columns * self.rows - self.count


@dataclass(frozen=True)
class Plan:
    info: VideoInfo
    rate: float  # frames per second
    grid: Grid
    edge: int


def choose_grid(count: int, width: int, height: int, edge: int) -> Grid:
    if not 1 <= count <= MAX_FRAMES:
        raise ValueError(f"Choose a rate producing 1 to {MAX_FRAMES} frames.")
    if width <= 0 or height <= 0 or not 64 <= edge <= MAX_EDGE:
        raise ValueError(f"Dimensions must be positive; sheet edge must be 64 to {MAX_EDGE} pixels.")
    candidates = []
    for columns in range(1, count + 1):
        rows = math.ceil(count / columns)
        scale = min(edge / (columns * width), edge / (rows * height))
        tw, th = math.floor(width * scale), math.floor(height * scale)
        if tw and th:
            candidates.append(Grid(columns, rows, tw, th, count))
    if not candidates:
        raise ValueError("Frames cannot fit at this resolution; lower the rate or increase resolution.")
    return max(candidates, key=lambda g: (g.tile_width * g.tile_height, -g.empty,
                                         -abs(g.size[0] - g.size[1]), -g.columns))


def make_plan(info: VideoInfo, rate: float, edge: int, count: int | None = None) -> Plan:
    if not math.isfinite(info.duration) or info.duration <= 0:
        raise ValueError("Video duration must be finite and positive.")
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Sample rate must be finite and greater than zero.")
    product = info.duration * rate
    if not math.isfinite(product):
        raise ValueError("Sample rate is too high.")
    frames = max(1, math.ceil(product - 1e-9)) if count is None else count
    grid = choose_grid(frames, info.width, info.height, edge)
    return Plan(info, rate if count is None else frames / info.duration, grid, edge)


def nearby_plans(plan: Plan) -> list[Plan]:
    """Nearest lower/higher counts whose best grid is full; never auto-apply."""
    result = []
    for counts in (range(plan.grid.count - 1, 0, -1),
                   range(plan.grid.count + 1, MAX_FRAMES + 1)):
        for count in counts:
            candidate = make_plan(plan.info, plan.rate, plan.edge, count)
            if candidate.grid.empty == 0:
                result.append(candidate)
                break
    return result


def _ffmpeg_output(args: list[str], cancel: threading.Event | None = None) -> tuple[bytes, str]:
    command = [str(resolve_ffmpeg()), "-hide_banner", "-nostdin", *args]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 90
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise StoryboardCancelled("Storyboard cancelled; no sheet saved.")
            if time.monotonic() > deadline:
                raise RuntimeError("FFmpeg took over 90 seconds for one frame. Export stopped.")
            try:
                data, errors = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        message = errors.decode("utf-8", "replace")
        if process.returncode or not data:
            raise RuntimeError("FFmpeg could not decode the requested video frame.\n" + message[-2500:])
        return data, message
    finally:
        if process.poll() is None:
            process.kill()  # Only this export's own decoder child.
        process.communicate()


def inspect_video(path: Path, cancel: threading.Event | None = None) -> VideoInfo:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Select a video file.")
    # PNG gives the actual display orientation; scale also normalizes non-square pixels.
    data, message = _ffmpeg_output([
        "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-an",
        "-vf", "scale=trunc(iw*sar):ih,setsar=1", "-f", "image2pipe", "-c:v", "png", "pipe:1",
    ], cancel)
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", message)
    if duration is None:
        raise ValueError("This video has no readable duration; remux it to a finite video file first.")
    hours, minutes, seconds = map(float, duration.groups())
    with Image.open(io.BytesIO(data)) as first:
        width, height = first.size
    info = VideoInfo(path, hours * 3600 + minutes * 60 + seconds, width, height)
    if info.duration <= 0:
        raise ValueError("Video duration is zero or too short to read.")
    return info


def export_sheet(plan: Plan, cancel: threading.Event | None = None, progress=None) -> Path:
    """Decode one tile at a time; fail rather than skip any requested frame."""
    validated = make_plan(plan.info, plan.rate, plan.edge, plan.grid.count)
    if validated.grid != plan.grid:
        raise ValueError("Storyboard layout does not match the video dimensions.")
    sheet = Image.new("RGB", plan.grid.size, "black")
    try:
        for index in range(plan.grid.count):
            timestamp = index / plan.rate
            if timestamp >= plan.info.duration:
                raise ValueError("Sample time is outside the video.")
            data, _ = _ffmpeg_output([
                "-ss", f"{timestamp:.9f}", "-i", str(plan.info.path), "-map", "0:v:0",
                "-frames:v", "1", "-an", "-vf",
                f"scale={plan.grid.tile_width}:{plan.grid.tile_height},setsar=1",
                "-f", "image2pipe", "-c:v", "png", "pipe:1",
            ], cancel)
            with Image.open(io.BytesIO(data)) as tile:
                sheet.paste(tile.convert("RGB"), ((index % plan.grid.columns) * plan.grid.tile_width,
                                                  (index // plan.grid.columns) * plan.grid.tile_height))
            if progress:
                progress(index + 1, plan.grid.count)
        if cancel is not None and cancel.is_set():
            raise StoryboardCancelled("Storyboard cancelled; no sheet saved.")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Description", f"Left to right, then down; {plan.grid.count} frames; "
                          f"{plan.rate:.9g} frames/second; first sample 0s; interval {1 / plan.rate:.9g}s")
        for suffix in range(1, 10000):
            tag = "" if suffix == 1 else f" ({suffix})"
            output = plan.info.path.with_name(f"{plan.info.path.stem} storyboard{tag}.png")
            try:
                stream = output.open("xb")
            except FileExistsError:
                continue
            try:
                with stream:
                    sheet.save(stream, "PNG", pnginfo=metadata)
                if cancel is not None and cancel.is_set():
                    raise StoryboardCancelled("Storyboard cancelled; no sheet saved.")
                return output
            except BaseException:
                output.unlink(missing_ok=True)
                raise
        raise FileExistsError("Too many storyboards with this filename.")
    finally:
        sheet.close()
