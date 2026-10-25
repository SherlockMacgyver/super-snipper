"""Snip storyboard verification through the real Qt window, launcher and saved pixels.

Run with QT_QPA_PLATFORM=windows for a native window witness; the default
offscreen run is integration evidence, not installed-tray acceptance.
"""

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtWidgets, QtTest
from supersnipper import config, series, series_ui

LAUNCHER = Path(__file__).parents[1] / "super_snipper.py"
RED, BLUE, YELLOW = (220, 30, 30), (30, 60, 220), (255, 220, 64)


def pump_until(app, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate(), "GUI operation timed out"


def solid(path, color, size=(320, 180)):
    Image.new("RGB", size, color).save(path)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def near(pixel, color, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], color))


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCREENSHOT_DIR", tmp_path / "out")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = series_ui.load_series_dialog(QtCore, QtGui, QtWidgets)()
    dialog.confirm_discard = lambda: True
    dialog.show()
    app.processEvents()
    yield app, dialog
    dialog.dirty = False
    dialog.close()
    dialog.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def drag(app, canvas, image_from, image_to, tool):
    """Drag across the canvas between two points given in image pixels."""
    canvas.tool = tool
    scale, left, top = canvas._fit()
    start, end = (QtCore.QPoint(round(left + x * scale), round(top + y * scale))
                  for x, y in (image_from, image_to))
    QtTest.QTest.mousePress(canvas, QtCore.Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseMove(canvas, end)
    QtTest.QTest.mouseRelease(canvas, QtCore.Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()


def test_reuse_starts_unmarked_order_and_originals_untouched(window, tmp_path):
    app, dialog = window
    red, blue = solid(tmp_path / "red.png", RED), solid(tmp_path / "blue.png", BLUE)
    before = {red: digest(red), blue: digest(blue)}
    dialog.add_image(red)
    dialog.color.setCurrentIndex([c for _, c in series.markup.COLORS].index("#ffdc40"))
    dialog.width_choice.setCurrentIndex(2)
    drag(app, dialog.canvas, (20, 20), (120, 90), "rectangle")
    first = dialog.steps[0]
    assert len(first.strokes) == 1 and first.strokes[0][1] == "#ffdc40"
    dialog.btn_reuse.click()
    assert len(dialog.steps) == 2 and dialog.list.currentRow() == 1
    reused = dialog.steps[1]
    assert reused.original == first.original and reused.strokes == []
    drag(app, dialog.canvas, (200, 120), (300, 170), "arrow")
    assert len(reused.strokes) == 1 and len(first.strokes) == 1
    dialog.add_image(blue)
    assert [s.original.name for s in dialog.steps] == ["red.png", "red.png", "blue.png"]
    # Undo affects only the selected step.
    dialog.list.setCurrentRow(1)
    dialog.btn_undo.click()
    assert reused.strokes == [] and len(first.strokes) == 1
    drag(app, dialog.canvas, (200, 120), (300, 170), "arrow")

    dialog.btn_create.click()
    assert dialog.output is not None and dialog.output.parent == tmp_path / "out", dialog.status.text()
    assert not dialog.dirty and dialog.btn_open.isEnabled()
    assert {p: digest(p) for p in before} == before
    grid = series.plan_grid(dialog.steps, dialog.edge.value())
    with Image.open(dialog.output) as sheet:
        # Largest tiles first: three 16:9 steps stack as one column, as video storyboards do.
        assert sheet.size == grid.size and (grid.columns, grid.rows, grid.empty) == (1, 3, 0)
        assert "3 steps" in sheet.info["Description"]
        tw, th = grid.tile_width, grid.tile_height
        sx, sy = tw / 320, th / 180

        def at(index, x, y):
            return sheet.getpixel((round((index % grid.columns) * tw + x * sx),
                                   round((index // grid.columns) * th + y * sy)))
        # Step 1 carries its yellow rectangle edge; the reused step 2 does not.
        assert near(at(0, 20, 55), YELLOW) and near(at(1, 20, 55), RED)
        # Step 2's arrow is not on step 1; centers prove order.
        assert near(at(0, 250, 145), RED)
        assert near(at(0, 160, 50), RED) and near(at(1, 100, 50), RED) and near(at(2, 160, 90), BLUE)


def test_eraser_clear_move_remove_and_close_prompt(window, tmp_path):
    app, dialog = window
    dialog.add_image(solid(tmp_path / "a.png", RED))
    dialog.add_image(solid(tmp_path / "b.png", BLUE))
    drag(app, dialog.canvas, (40, 40), (280, 40), "pen")
    assert len(dialog.steps[1].strokes) == 1
    drag(app, dialog.canvas, (160, 10), (160, 70), "eraser")
    assert dialog.steps[1].strokes == []
    drag(app, dialog.canvas, (40, 40), (280, 140), "ellipse")
    dialog.btn_clear.click()
    assert dialog.steps[1].strokes == []
    dialog.btn_up.click()
    assert [s.original.name for s in dialog.steps] == ["b.png", "a.png"] and dialog.list.currentRow() == 0
    dialog.btn_remove.click()
    assert [s.original.name for s in dialog.steps] == ["a.png"]
    asked = []
    dialog.confirm_discard = lambda: asked.append(True) or False
    dialog.close()
    assert asked and dialog.isVisible()


def test_mixed_sizes_letterbox_and_missing_original_fails_loudly(window, tmp_path):
    app, dialog = window
    wide = solid(tmp_path / "wide.png", RED, (400, 100))
    square = solid(tmp_path / "square.png", BLUE, (200, 200))
    dialog.add_image(wide)
    dialog.add_image(square)
    w, h = series.cell_shape(dialog.steps)
    assert abs(w / h - 2.0) < 0.01  # geometric mean of 4:1 and 1:1
    dialog.btn_create.click()
    grid = series.plan_grid(dialog.steps, dialog.edge.value())
    with Image.open(dialog.output) as sheet:
        x0 = grid.tile_width if grid.columns == 2 else 0
        y0 = grid.tile_height if grid.columns == 1 else 0
        assert near(sheet.getpixel((x0 + grid.tile_width // 2, y0 + grid.tile_height // 2)), BLUE)
        assert near(sheet.getpixel((x0 + 2, y0 + grid.tile_height // 2)), (0, 0, 0), 5)
    saved = sorted((tmp_path / "out").glob("*.png"))
    square.unlink()
    dialog.btn_create.click()
    assert "Cannot read image" in dialog.status.text()
    assert sorted((tmp_path / "out").glob("*.png")) == saved
    with pytest.raises(ValueError):
        series.plan_grid([], 2048)


def test_capture_through_real_launcher_and_cancel(window, tmp_path, monkeypatch):
    app, dialog = window
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))  # snips land in this Pictures folder
    monkeypatch.setenv("SUPER_SNIPPER_HOME", str(tmp_path / "home"))
    dialog.capture_args = ["--capture-box", "0,0,240,135", "--no-clipboard"]
    dialog.btn_capture.click()
    assert not dialog.isVisible()
    pump_until(app, lambda: dialog.process is None and dialog.isVisible() and dialog.steps)
    step = dialog.steps[0]
    assert step.original.parent == tmp_path / "profile" / "Pictures" / "Screenshots"
    assert (step.width, step.height) == (240, 135)
    assert dialog.status.text().startswith("Added step 1")
    # A launcher run that saves nothing is reported as a cancelled snip.
    dialog.capture_args = ["--self-test"]
    dialog.btn_capture.click()
    pump_until(app, lambda: dialog.process is None and dialog.isVisible()
               and "cancelled" in dialog.status.text())
    assert len(dialog.steps) == 1
    assert dialog._result_file is None  # temporary handoff file removed
    # Exit-code handling (reached directly; a real failing launcher shows a modal error box).
    dialog._result_file = tmp_path / "unused.txt"
    dialog._result_file.write_text("", encoding="utf-8")
    dialog._capture_finished(1, QtCore.QProcess.ExitStatus.NormalExit)
    assert "Snip failed (exit code 1)" in dialog.status.text() and len(dialog.steps) == 1


def test_hud_offers_snip_storyboards_and_real_launcher_window(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="windows", SUPER_SNIPPER_HOME=str(tmp_path / "home"))
    process = subprocess.Popen([sys.executable, str(LAUNCHER), "--series"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if owner.value == process.pid and title.value == "Super Snipper - Snip storyboard":
            found.append(hwnd)
        return True
    try:
        deadline = time.monotonic() + 15
        while not found and time.monotonic() < deadline and process.poll() is None:
            user32.EnumWindows(visit, 0)
            time.sleep(0.05)
        assert found, "Real launcher did not create its snip storyboard window"
        user32.PostMessageW(wintypes.HWND(found[0]), 0x0010, 0, 0)  # WM_CLOSE to our own child only.
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert b"Traceback" not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
