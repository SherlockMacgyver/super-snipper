"""Real FFmpeg/CLI and Qt storyboard verification; no production tray changes.

Run this file with QT_QPA_PLATFORM=windows for an isolated native window witness;
the default offscreen run is integration evidence, not installed acceptance.
"""

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtWidgets, QtTest
from supersnipper import storyboard as sb
from supersnipper import storyboard_ui


@pytest.mark.parametrize("count,aspect,expected", [(12, (16, 9), (3, 4)),
    (13, (16, 9), (3, 5)), (1, (16, 9), (1, 1)), (4, (1, 1), (2, 2))])
def test_largest_tiles_before_empty_cells(count, aspect, expected):
    grid = sb.choose_grid(count, *aspect, 2048)
    assert (grid.columns, grid.rows) == expected
    assert max(grid.size) <= 2048
    assert abs(grid.tile_width / grid.tile_height - aspect[0] / aspect[1]) < 0.01


def test_limits_rates_and_alternatives(tmp_path):
    info = sb.VideoInfo(tmp_path / "x.mp4", 130, 1920, 1080)
    exact = sb.make_plan(info, 0.1, 2048)
    options = sb.nearby_plans(exact)
    assert exact.grid.count == 13
    assert [p.grid.count for p in options] == [12, 15]
    assert all(p.grid.empty == 0 for p in options)
    assert options[0].rate < exact.rate < options[1].rate
    assert sb.make_plan(sb.VideoInfo(info.path, 1.01, 16, 9), 2, 2048).grid.count == 3
    for rate in (0, -1, float("nan"), float("inf"), 10000):
        with pytest.raises(ValueError):
            sb.make_plan(info, rate, 2048)
    for count in (0, 1001):
        with pytest.raises(ValueError):
            sb.choose_grid(count, 16, 9, 2048)
    assert sb.choose_grid(1000, 16, 9, 4096).count == 1000


@pytest.fixture
def video(tmp_path):
    # Four known colors, one second each; pixel assertions prove temporal order.
    source = tmp_path / "ordered sample.mp4"
    raw = b"".join(bytes(color) * (160 * 90 * 10) for color in
                   ((240, 20, 20), (20, 240, 20), (20, 20, 240), (240, 240, 20)))
    command = [str(sb.resolve_ffmpeg()), "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-s", "160x90", "-r", "10", "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)]
    subprocess.run(command, input=raw, check=True, capture_output=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return source


def assert_order(output, grid):
    with Image.open(output) as image:
        assert image.size == grid.size
        for index, dominant in enumerate(((0,), (1,), (2,), (0, 1))):
            pixel = image.getpixel(((index % grid.columns) * grid.tile_width + grid.tile_width // 2,
                                    (index // grid.columns) * grid.tile_height + grid.tile_height // 2))
            for channel in range(3):
                assert pixel[channel] > 180 if channel in dominant else pixel[channel] < 70
        assert "first sample 0s" in image.info["Description"]


def test_real_cli_export_and_collision(video, tmp_path):
    env = dict(os.environ, SUPER_SNIPPER_HOME=str(tmp_path / "state"))
    command = [sys.executable, str(Path(__file__).parents[1] / "super_snipper.py"),
               "--storyboard", str(video), "--sample-rate", "1", "--sample-unit", "second",
               "--sheet-edge", "512"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=45, env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    output = video.with_name("ordered sample storyboard.png")
    assert f"storyboard={output}" in result.stdout
    info = sb.inspect_video(video)
    assert info.duration == pytest.approx(4)
    plan = sb.make_plan(info, 1, 512)
    assert_order(output, plan.grid)
    original = output.read_bytes()
    second = sb.export_sheet(plan)
    assert second.name == "ordered sample storyboard (2).png"
    assert output.read_bytes() == original
    assert_order(second, plan.grid)


def test_cancel_failure_and_audio_only_publish_nothing(video, tmp_path):
    plan = sb.make_plan(sb.inspect_video(video), 1, 512)
    cancel = threading.Event()
    with pytest.raises(sb.StoryboardCancelled):
        sb.export_sheet(plan, cancel, lambda *_: cancel.set())
    assert not list(tmp_path.glob("*storyboard*.png"))
    bad = tmp_path / "invalid.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(RuntimeError, match="could not decode"):
        sb.inspect_video(bad)
    audio = tmp_path / "audio.wav"
    subprocess.run([str(sb.resolve_ffmpeg()), "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc", "-t", "1", str(audio)], check=True, capture_output=True,
                   timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with pytest.raises(RuntimeError, match="could not decode"):
        sb.inspect_video(audio)
    video.unlink()
    with pytest.raises(RuntimeError, match="could not decode"):
        sb.export_sheet(plan)
    assert not list(tmp_path.glob("*storyboard*.png"))


def test_cancellation_during_png_save_removes_output(video, tmp_path, monkeypatch):
    plan = sb.make_plan(sb.inspect_video(video), 0.25, 512)
    cancel = threading.Event()
    original_save = Image.Image.save
    def save_then_cancel(image, *args, **kwargs):
        original_save(image, *args, **kwargs)
        cancel.set()
    monkeypatch.setattr(Image.Image, "save", save_then_cancel)
    with pytest.raises(sb.StoryboardCancelled):
        sb.export_sheet(plan, cancel)
    assert not list(tmp_path.glob("*storyboard*.png"))


def test_rotated_display_dimensions(video, tmp_path):
    rotated = tmp_path / "rotated.mp4"
    subprocess.run([str(sb.resolve_ffmpeg()), "-v", "error", "-display_rotation", "90",
        "-i", str(video), "-c", "copy", str(rotated)],
        check=True, capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    info = sb.inspect_video(rotated)
    assert (info.width, info.height) == (90, 160)
    plan = sb.make_plan(info, 0.25, 512)
    output = sb.export_sheet(plan)
    with Image.open(output) as image:
        assert image.width < image.height
        assert image.getpixel((image.width // 2, image.height // 2))[0] > 180


def pump_until(app, predicate, timeout=35):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate(), "GUI operation timed out"


def test_real_window_load_export_error_and_close(video, tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = storyboard_ui.load_storyboard_dialog(QtCore, QtGui, QtWidgets)()
    window.show()
    try:
        window.load_video(video)
        pump_until(app, lambda: window.worker is None)
        assert window.info is not None, window.status.text()
        window.units.setCurrentIndex(1)
        window.rate.setValue(1)
        window.edge.setValue(512)
        assert window.save.isEnabled()
        assert "Actual rate: 1 frames/second" in window.summary.text()
        assert not window.preview.pixmap().isNull()
        app.processEvents()
        assert window.summary.height() >= window.summary.heightForWidth(window.summary.width())
        if os.environ.get("SUPER_SNIPPER_STORYBOARD_WITNESS"):
            media = Path(os.environ["SC_WORKROOM_GENERATED_MEDIA_ROOT"])
            media.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(media / "video-storyboard-window.png"))
        QtTest.QTest.mouseClick(window.save, QtCore.Qt.MouseButton.LeftButton)
        assert not window.browse.isEnabled()
        pump_until(app, lambda: window.worker is None)
        assert window.output and window.output.exists(), window.status.text()
        assert_order(window.output, window.choices.currentData().grid)
        window.rate.setValue(60000)
        assert not window.save.isEnabled()
        window.rate.setValue(1)
        window.create_sheet()
        window.close()
        pump_until(app, lambda: window.worker is None)
        assert not window.isVisible()
        assert "cancel" in window.status.text().lower()
        assert len(list(tmp_path.glob("*storyboard*.png"))) == 1
        # An invalid file must clear the prior plan rather than export the old video.
        window._closing = False
        window.show()
        bad = tmp_path / "broken.mp4"
        bad.write_bytes(b"bad")
        window.load_video(bad)
        pump_until(app, lambda: window.worker is None)
        assert not window.save.isEnabled()
        assert "could not decode" in window.status.text()
    finally:
        if window.worker:
            window.cancel_work()
            pump_until(app, lambda: window.worker is None)
        window.close()


def test_hud_storyboard_button_is_wired(monkeypatch, tmp_path):
    from tests.super_snipper_support import load
    monkeypatch.setenv("SUPER_SNIPPER_HOME", str(tmp_path / "home"))
    snipper = load()
    monkeypatch.setattr(snipper.recording, "_active_recording_state", lambda: None)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hud = snipper._load_hud_class(QtCore, QtGui, QtWidgets)()
    launched = []
    monkeypatch.setattr(snipper.hud_widget.subprocess, "Popen", lambda command, **kw: launched.append(command))
    try:
        hud.show_panel()
        app.processEvents()
        assert hud.btn_storyboard.isVisible()
        hud.btn_storyboard.click()
        assert launched == [snipper.hud_launch_command(["--storyboard"])]
        hud._set_recording_mode(True)
        assert not hud.btn_storyboard.isVisible()
    finally:
        hud.markup.finish()
        hud._record_timer.stop()
        hud.hide()
        hud.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
