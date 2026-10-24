"""Lifecycle regressions: real Qt widgets, isolated IPC and cancellation.

Offscreen tests cannot prove capture: for HUD, markup or capture changes, also
check a real snip and recording on the desktop.
"""
import os
from pathlib import Path
import sys
import subprocess
import time
import uuid
from unittest.mock import Mock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets, QtTest

from tests.super_snipper_support import LAUNCHER as SCRIPT, load


@pytest.fixture
def snipper(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPER_SNIPPER_INSTANCE", uuid.uuid4().hex)
    monkeypatch.setenv("SUPER_SNIPPER_HOME", str(tmp_path / "home"))
    return load()


@pytest.fixture
def hud(snipper, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(snipper, "_active_recording_state", lambda: None)
    widget = snipper._load_hud_class(QtCore, QtGui, QtWidgets)()
    widget._exclude_from_capture = Mock()
    widget.show_panel()
    app.processEvents()
    yield widget
    widget.markup.finish()
    widget._record_timer.stop()
    widget.hide()
    widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("tool", ["pen", "rectangle", "ellipse", "arrow"])
def test_markup_draws_real_widget_paths_from_menu(hud, tool):
    hud._set_recording_mode(True)
    hud.markup_actions[tool].trigger()
    overlay = hud.markup.overlays[0]
    QtTest.QTest.mousePress(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(60, 70))
    QtTest.QTest.mouseMove(overlay, QtCore.QPoint(180, 150))
    QtTest.QTest.mouseRelease(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(180, 150))
    assert len(overlay.paths) == 1
    image = overlay.grab().toImage()
    assert any(image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).alpha() > 200
               for x in range(55, 186) for y in range(65, 156))
    assert overlay.parent() is None  # must not inherit HUD capture exclusion
    assert overlay.geometry() == overlay.display.geometry()
    hud.markup_actions["pointer"].trigger()
    assert overlay.windowFlags() & QtCore.Qt.WindowType.WindowTransparentForInput
    assert len(overlay.paths) == 1


def test_markup_eraser_hits_strokes_not_shape_interior_and_clear(hud):
    hud._set_recording_mode(True)
    hud.markup_actions["rectangle"].trigger()
    overlay = hud.markup.overlays[0]
    QtTest.QTest.mousePress(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(40, 40))
    QtTest.QTest.mouseRelease(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(200, 200))
    hud.markup_actions["eraser"].trigger()
    QtTest.QTest.mouseClick(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(100, 100))
    assert len(overlay.paths) == 1
    QtTest.QTest.mouseClick(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(40, 100))
    assert not overlay.paths
    hud.markup_actions["pen"].trigger()
    QtTest.QTest.mouseClick(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(90, 90))
    assert overlay.paths
    next(a for a in hud.markup_menu.actions() if a.text() == "Clear all").trigger()
    assert not overlay.paths


def test_markup_escape_hide_stop_and_failed_exit_release_input(hud, snipper, monkeypatch):
    hud._set_recording_mode(True)
    hud.markup_actions["pen"].trigger()
    overlay = hud.markup.overlays[0]
    QtTest.QTest.keyClick(overlay, QtCore.Qt.Key.Key_Escape)
    assert hud.markup.tool == "pointer"
    hud.markup_actions["arrow"].trigger()
    hud.hide_panel()
    assert hud.markup.tool == "pointer"
    assert overlay.windowFlags() & QtCore.Qt.WindowType.WindowTransparentForInput
    hud.show_panel()
    hud.markup_actions["pen"].trigger()
    hud._set_recording_mode(True, stopping=True)
    assert hud.markup.tool == "pointer"
    assert not hud.btn_markup.isEnabled()
    hud._record_process = Mock()
    hud._record_process.poll.return_value = 1
    hud._exit_requested = True
    hud._exit_callback = Mock()
    hud._sync_recording_lifecycle()
    assert not hud.markup.active
    assert not hud.markup.overlays
    assert not overlay.isVisible()
    assert not hud._exit_requested


def test_markup_available_before_snip_and_cleared_after_recording(hud):
    hud.markup_actions["pen"].trigger()
    assert hud.markup.overlays
    hud._set_recording_mode(True)
    hud.markup_actions["pen"].trigger()
    overlay = hud.markup.overlays[0]
    QtTest.QTest.mouseClick(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(90, 90))
    hud._set_recording_mode(False)
    assert not hud.markup.overlays
    assert not overlay.isVisible()
    hud._set_recording_mode(True)
    hud.markup_actions["pen"].trigger()
    assert not hud.markup.overlays[0].paths


def test_snip_releases_drawing_input_preserves_ink_and_blocks_tools_during_selection(hud, monkeypatch):
    hud.markup_actions["pen"].trigger()
    overlay = hud.markup.overlays[0]
    QtTest.QTest.mouseClick(overlay, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(80, 90))
    monkeypatch.setattr(QtCore.QTimer, "singleShot", lambda *args: None)
    hud._launch(["--snip"])
    assert hud.markup.tool == "pointer"
    assert len(overlay.paths) == 1
    assert not hud.btn_markup.isEnabled()
    # Same terminal path for a finished still snip and a cancelled selector.
    hud._capture_pending = False
    hud._sync_recording_lifecycle()
    assert hud.btn_markup.isEnabled()
    assert len(overlay.paths) == 1


def test_close_hides_and_reopening_reuses_window_and_position(hud):
    hud._drag.snapped_edge = None
    hud.move(120, 150)
    handle = hud.winId()
    for _ in range(2):
        hud.close()
        assert not hud.isVisible()
        hud.show_panel()
        assert hud.isVisible()
        assert hud.winId() == handle
        assert (hud.x(), hud.y()) == (120, 150)


def test_close_during_recording_hides_without_stopping(hud):
    hud._set_recording_mode(True)
    hud._request_stop = Mock()
    hud.close()
    assert not hud.isVisible()
    assert hud._recording_active
    hud._request_stop.assert_not_called()


def test_hidden_recording_does_not_reshow_when_it_finishes(hud):
    hud._set_recording_mode(True)
    hud.hide_panel()
    hud._sync_recording_lifecycle()
    assert not hud.isVisible()
    assert not hud._recording_active


def test_offscreen_position_recovers_inside_current_monitor(hud):
    hud._drag.snapped_edge = None
    hud.move(-20000, 25000)
    hud.show_panel()
    x, y, w, h = hud._screen_bounds()
    assert x <= hud.x() <= x + w - hud.width()
    assert y <= hud.y() <= y + h - hud.height()


def test_explicit_docking_and_undocking(hud):
    for edge in ("left", "right", "top", "bottom"):
        hud.dock_panel(edge)
        assert hud._drag.snapped_edge == edge
        assert hud._orientation == ("vertical" if edge in ("left", "right") else "horizontal")
        hud.hide_panel()
        hud.show_panel()
        assert hud._drag.snapped_edge == edge
    hud.dock_panel(None)
    assert hud._drag.snapped_edge is None
    assert hud._orientation == "horizontal"


def test_exit_waits_for_child_and_does_not_ack_failed_finalize(hud):
    child = Mock()
    child.poll.return_value = None
    hud._record_process = child
    done = Mock()
    hud.request_exit(done)
    done.assert_not_called()
    child.poll.return_value = 1
    hud._sync_recording_lifecycle()
    assert "Capture failed" in done.call_args.args[0]
    assert not hud._exit_requested
    assert hud.isVisible()


def test_exit_waits_for_pending_launch_then_cancels_it(hud):
    hud._capture_pending = True
    done = Mock()
    hud.request_exit(done)
    done.assert_not_called()
    with patch.object(QtCore.QTimer, "singleShot"):
        hud._start_recording_process(["must-not-launch"])
    done.assert_called_once_with(None)
    assert hud._record_process is None


def test_stop_cancels_pending_capture_before_process_launch(hud, snipper):
    with patch.object(QtCore.QTimer, "singleShot") as timer:
        hud._launch(["--snip"])
    done = Mock()
    hud.handle_command("stop", done)
    with patch.object(snipper.subprocess, "Popen") as launch:
        timer.call_args.args[1]()
    launch.assert_not_called()
    assert not hud._capture_pending
    assert hud.isVisible()


def test_exit_is_scheduled_even_if_ack_transport_fails(hud):
    done = Mock(side_effect=RuntimeError("client disconnected"))
    with patch.object(QtCore.QTimer, "singleShot") as timer:
        with pytest.raises(RuntimeError, match="client disconnected"):
            hud.request_exit(done)
    timer.assert_called_once()
    event = QtGui.QCloseEvent()
    hud.closeEvent(event)
    assert event.isAccepted()


def test_adopted_recorder_retains_real_exit_status(snipper):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.4); raise SystemExit(7)"],
                               **snipper.hidden_subprocess_kwargs())
    observed = snipper.ExistingRecordingProcess(process.pid)
    assert observed.poll() is None
    process.wait(timeout=5)
    assert observed.poll() == 7
    assert observed.poll() == 7
    assert observed.handle is None


def test_stop_adopts_standalone_recorder_before_state_disappears(hud, snipper, tmp_path):
    observed = Mock()
    observed.poll.return_value = None
    with patch.object(snipper, "_active_recording_state", return_value={"pid": 123, "pid_create_time": 456.0, "recording_id": "a" * 32}), \
         patch.object(snipper, "ExistingRecordingProcess", return_value=observed) as attach, \
         patch.object(snipper, "RECORDING_STOP_FILE", tmp_path / "stop"):
        hud._request_stop()
    attach.assert_called_once_with(123, 456.0)
    assert hud._record_process is observed
    request = tmp_path / ("stop." + "a" * 32)
    assert request.exists()
    assert '"recording_id"' in request.read_text()


def test_corrupt_state_timer_reports_once_and_releases_drawing_input(hud, snipper):
    hud.markup_actions["pen"].trigger()
    hud._record_timer.start()
    with patch.object(snipper, "_active_recording_state", side_effect=ValueError("corrupt state")), \
         patch.object(QtWidgets.QMessageBox, "critical") as report:
        hud._poll_recording_lifecycle()
    assert not hud._record_timer.isActive()
    assert hud.markup.tool == "pointer"
    report.assert_called_once()


def test_exit_signals_real_worker_event_and_waits_for_finish(hud, snipper):
    name = "Local\\SuperSnipper.Test." + uuid.uuid4().hex
    hud._cancel_handle = snipper._capture_event(name, create=True)
    snipper._CANCEL_EVENT = snipper._capture_event(name)
    child = Mock()
    child.poll.return_value = None
    hud._record_process = child
    done = Mock()
    try:
        assert not snipper._capture_cancelled()
        hud.request_exit(done)
        assert snipper._capture_cancelled()
        done.assert_not_called()
        child.poll.return_value = 0
        with patch.object(QtCore.QTimer, "singleShot"):
            hud._sync_recording_lifecycle()
        done.assert_called_once_with(None)
        assert hud._cancel_handle is None
    finally:
        snipper._close_handle(snipper._CANCEL_EVENT)
        snipper._CANCEL_EVENT = None


def test_capture_returns_to_panel_without_closing(hud, snipper):
    child = Mock()
    child.poll.return_value = None
    with patch.object(QtCore.QTimer, "singleShot") as timer:
        hud._launch(["--snip"])
    assert not hud.isVisible()
    assert hud._capture_pending
    assert timer.call_count == 1  # no delayed close()
    with patch.object(snipper.subprocess, "Popen", return_value=child):
        timer.call_args.args[1]()
    assert hud._record_process is child
    child.poll.return_value = 0
    hud._sync_recording_lifecycle()
    assert hud.isVisible()
    assert hud._record_process is None


@pytest.mark.parametrize("command", ["snip", "region", "arm-region", "full"])
def test_hotkey_capture_preserves_explicit_hide(hud, snipper, command):
    hud.hide_panel()
    child = Mock()
    child.poll.return_value = None
    with patch.object(QtCore.QTimer, "singleShot") as timer:
        hud.handle_command(command, Mock())
    with patch.object(snipper.subprocess, "Popen", return_value=child):
        timer.call_args.args[1]()
    child.poll.return_value = 0
    hud._sync_recording_lifecycle()
    assert not hud.isVisible()
    assert hud._user_hidden


def test_service_begins_hidden_for_capture_hotkey(snipper):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = snipper._load_hud_class(QtCore, QtGui, QtWidgets)()
    assert not widget.isVisible()
    assert widget._user_hidden
    widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def test_cancelled_armed_capture_never_clicks(snipper):
    process = Mock()
    process.poll.return_value = None
    with patch.object(snipper, "_capture_cancelled", return_value=True), \
         patch.object(snipper, "_click_screen_point") as click:
        snipper._fire_armed_click_after_start(process, (20, 30), 0)
    click.assert_not_called()


def test_duplicate_launch_does_not_silently_accept_unresponsive_owner(snipper):
    with patch.object(snipper, "_panel_request", return_value=False), \
         patch.object(snipper, "_acquire_control_panel_mutex", return_value=None), \
         patch.object(snipper.time, "monotonic", side_effect=[0, 6]), \
         patch.object(snipper.subprocess, "Popen") as launch:
        with pytest.raises(RuntimeError, match="not responding"):
            snipper.show_control_panel()
    launch.assert_not_called()


def test_missing_server_starts_service_then_requires_ack(snipper):
    with patch.object(snipper, "_panel_request", side_effect=[False, True]), \
         patch.object(snipper, "_acquire_control_panel_mutex", return_value=123), \
         patch.object(snipper, "_close_handle"), \
         patch.object(snipper.subprocess, "Popen") as launch:
        snipper.show_control_panel()
    assert "--panel-service" in launch.call_args.args[0]


def test_local_pipe_transports_command_and_ack(snipper, hud):
    server = QtNetwork.QLocalServer()
    assert server.listen(snipper._panel_server_name())
    received = []
    sockets = []

    def accept():
        socket = server.nextPendingConnection()
        sockets.append(socket)
        def read():
            if socket.canReadLine():
                received.append(bytes(socket.readLine()).strip())
                socket.write(b'{"error": null}\n')
                socket.flush()
        socket.readyRead.connect(read)
        read()
    server.newConnection.connect(accept)
    # Separate processes model production and avoid holding Python's GIL in
    # a blocking Qt socket wait while the server's Python slot needs it.
    code = f'''import sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
from PySide6 import QtCore, QtNetwork
from tests.super_snipper_support import load
snipper = load()
app = QtCore.QCoreApplication([])
assert snipper._panel_request(QtNetwork, "show")
'''
    worker = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, **snipper.hidden_subprocess_kwargs())
    deadline = time.monotonic() + 8
    while worker.poll() is None and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    out, err = worker.communicate(timeout=2)
    server.close()
    assert worker.returncode == 0, (out, err)
    assert received == [b"show"]
