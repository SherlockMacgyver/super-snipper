"""Capture-series storyboards: ordered steps, each an untouched original image plus its own marks.

Reusing a snip creates a new step that points at the same original file with no
marks, so earlier annotations never carry forward and the original is never
written. Marks are stored in the original image's pixel coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path

from . import capture, config, markup
from .storyboard import MAX_FRAMES, choose_grid


@dataclass(eq=False)
class Step:
    original: Path
    width: int
    height: int
    strokes: list = field(default_factory=list)  # (QPainterPath in image pixels, color, width)


def mark_scale(width: int, height: int) -> float:
    """Widths grow on large snips so marks stay visible once the sheet shrinks them."""
    return max(1.0, max(width, height) / 1280)


def cell_shape(steps: list[Step]) -> tuple[int, int]:
    """One cell shape for mixed snip sizes: the geometric mean of their aspect ratios."""
    aspect = math.exp(sum(math.log(s.width / s.height) for s in steps) / len(steps))
    return max(1, round(aspect * 1000)), 1000


def plan_grid(steps: list[Step], edge: int):
    if not steps:
        raise ValueError("Add at least one step before creating a storyboard.")
    if len(steps) > MAX_FRAMES:
        raise ValueError(f"A storyboard holds at most {MAX_FRAMES} steps.")
    return choose_grid(len(steps), *cell_shape(steps), edge)


def load_series_tools(QtCore, QtGui):
    """Qt rendering for steps and sheets; imported lazily like the other Qt modules."""
    Qt = QtCore.Qt
    ink = markup.load_ink_tools(QtCore, QtGui)

    def load_image(path: Path):
        reader = QtGui.QImageReader(str(path))
        reader.setAutoTransform(True)  # honour camera orientation tags on added photos
        image = reader.read()
        if image.isNull():
            raise ValueError(f"Cannot read image: {path} ({reader.errorString()})")
        return image

    def render_step(step: Step, image=None):
        """The original at full size with this step's marks drawn on a copy."""
        image = (image if image is not None else load_image(step.original)).convertToFormat(
            QtGui.QImage.Format.Format_RGB32)
        if (image.width(), image.height()) != (step.width, step.height):
            raise ValueError(f"Image changed size on disk since it was added: {step.original}")
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        ink.paint(painter, step.strokes)
        painter.end()
        return image

    def thumbnail(step: Step, image, size: int):
        return render_step(step, image).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)

    def stitch(steps: list[Step], edge: int, directory: Path | None = None):
        """Render every step in order into one PNG; any unreadable step fails the whole sheet."""
        grid = plan_grid(steps, edge)
        sheet = QtGui.QImage(grid.size[0], grid.size[1], QtGui.QImage.Format.Format_RGB32)
        sheet.fill(QtGui.QColor("black"))
        painter = QtGui.QPainter(sheet)
        try:
            for index, step in enumerate(steps):
                tile = render_step(step).scaled(grid.tile_width, grid.tile_height,
                                                Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation)
                left = (index % grid.columns) * grid.tile_width + (grid.tile_width - tile.width()) // 2
                top = (index // grid.columns) * grid.tile_height + (grid.tile_height - tile.height()) // 2
                painter.drawImage(left, top, tile)
        finally:
            painter.end()
        sheet.setText("Description", f"Capture-series storyboard; left to right, then down; "
                      f"{len(steps)} steps")
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        if not sheet.save(buffer, "PNG"):
            raise RuntimeError("Qt could not encode the storyboard PNG.")
        data = bytes(buffer.data())
        directory = directory or config.SCREENSHOT_DIR
        for _ in range(100):
            output = capture._next_capture_path(directory, "SuperSnip Storyboard", ".png")
            try:
                stream = output.open("xb")
            except FileExistsError:
                continue
            try:
                with stream:
                    stream.write(data)
            except BaseException:
                output.unlink(missing_ok=True)
                raise
            return output, grid
        raise FileExistsError("Could not find a free storyboard filename.")

    class SeriesTools:
        pass

    tools = SeriesTools()
    tools.ink, tools.load_image, tools.render_step = ink, load_image, render_step
    tools.thumbnail, tools.stitch = thumbnail, stitch
    return tools
