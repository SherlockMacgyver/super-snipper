"""Native hook contracts and Qt cleanup."""
import gc
from types import SimpleNamespace
from unittest.mock import Mock
import weakref

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from tests.super_snipper_support import load


@pytest.fixture
def snipper(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPER_SNIPPER_HOME", str(tmp_path / "home"))
    return load()


@pytest.fixture
def controller(snipper):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hud = QtWidgets.QWidget()
    hud.show()
    cls = snipper.markup.load_markup_class(QtCore, QtGui, QtWidgets)
    controller = cls(hud)
    yield controller
    controller.finish()
    hud.close()
    hud.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def native_api(snipper, monkeypatch):
    api = SimpleNamespace(SetWinEventHook=Mock(return_value=12),
                          UnhookWinEvent=Mock(return_value=True),
                          SetWindowPos=Mock(return_value=True))
    monkeypatch.setattr(snipper.winapi.ctypes, "WinDLL", lambda *a, **k: api)
    return api


def test_foreground_only_hook_owns_callback_until_unregistered(snipper, monkeypatch):
    api = native_api(snipper, monkeypatch)
    changed = Mock()
    watch = snipper.ForegroundWatcher(changed, Mock())
    args = api.SetWinEventHook.call_args.args
    assert args[:3] == (3, 3, None)
    assert args[3] is watch.callback
    assert args[4:] == (0, 0, 0)
    callback = watch.callback
    gc.collect()
    callback(12, 3, 50, 0, 0, 1, 0)
    callback(12, 0x8004, 50, 0, 0, 1, 0)  # Z-order event is never handled.
    assert changed.call_count == 1
    watch.close()
    api.UnhookWinEvent.assert_called_once_with(12)
    assert watch.handle is None and watch.callback is None
    callback(12, 3, 50, 0, 0, 1, 0)
    assert changed.call_count == 1


def test_callback_failure_is_reported_once_and_disabled(snipper, monkeypatch):
    native_api(snipper, monkeypatch)
    failed = Mock()
    watch = snipper.ForegroundWatcher(Mock(side_effect=OSError("stack failed")), failed)
    for _ in range(2):
        watch.callback(12, 3, 50, 0, 0, 1, 0)
    failed.assert_called_once_with("stack failed")
    assert watch.disabled
    watch.close()


def test_failed_unhook_retains_callback_until_successful_retry(snipper, monkeypatch):
    api = native_api(snipper, monkeypatch)
    api.UnhookWinEvent.side_effect = [False, True]
    watch = snipper.ForegroundWatcher(Mock(), Mock())
    ref = weakref.ref(watch)
    with pytest.raises(OSError):
        watch.close()
    assert watch.disabled and watch.callback is not None
    del watch
    gc.collect()
    assert ref() is not None
    ref().close()
    assert not snipper.winapi._FAILED_FOREGROUND_HOOKS


def test_native_raise_cannot_activate_or_move_window(snipper, monkeypatch):
    api = native_api(snipper, monkeypatch)
    snipper.raise_without_activation(50)
    api.SetWindowPos.assert_called_once_with(50, -1, 0, 0, 0, 0, 0x0013)
    api.SetWindowPos.return_value = False
    with pytest.raises(OSError):
        snipper.raise_without_activation(50)


@pytest.mark.parametrize("enabled,style,expected", [(True, 0x80008, 0x80028), (False, 0x80028, 0x80008)])
def test_click_through_preserves_layered_and_other_styles(snipper, monkeypatch, enabled, style, expected):
    api = native_api(snipper, monkeypatch)
    api.GetWindowLongPtrW = api.GetWindowLongW = Mock(return_value=style)
    api.SetWindowLongPtrW = api.SetWindowLongW = Mock(return_value=style)
    snipper.set_window_click_through(50, enabled)
    api.SetWindowLongPtrW.assert_called_once_with(50, -20, expected)
    api.SetWindowPos.assert_called_once_with(50, None, 0, 0, 0, 0, 0x0037)


def test_ink_then_hud_order_and_no_ink_no_work(controller, snipper, monkeypatch):
    controller.active = True
    ink = SimpleNamespace(isVisible=lambda: True, paths=[1], draft=None, winId=lambda: 50)
    controller.overlays = [ink]
    raised = Mock()
    monkeypatch.setattr(snipper.winapi, "raise_without_activation", raised)
    try:
        controller._restore_stack()
        assert [c.args[0] for c in raised.call_args_list] == [50, int(controller.hud.winId())]
        raised.reset_mock()
        ink.paths.clear()
        controller._restore_stack()
        raised.assert_not_called()
    finally:
        controller.overlays = []


def test_failed_unhook_cleans_windows_before_error_and_refuses_dead_listener(controller, monkeypatch):
    controller.set_tool("pen")
    closed = []
    class FailedWatcher:
        disabled = False
        def close(self):
            self.disabled = True
            closed.append(1)
            raise OSError("unhook failed")
    controller._foreground_watch = FailedWatcher()
    errors = []
    monkeypatch.setattr(controller, "_show_error", lambda *a: errors.append(len(controller.overlays)))
    try:
        controller.finish()
        assert errors == [0]
        controller.set_tool("pen")
        assert controller.tool == "pointer" and not controller.overlays
        assert len(closed) >= 2
    finally:
        controller._foreground_watch = None


def test_shutdown_removes_hook_and_disposes_ink_without_dialog(controller, monkeypatch):
    controller.set_tool("pen")
    watch = SimpleNamespace(close=Mock(), disabled=False)
    controller._foreground_watch = watch
    dialog = Mock()
    monkeypatch.setattr(QtWidgets.QMessageBox, "open", dialog)
    controller._shutdown()
    watch.close.assert_called_once()
    assert not controller.overlays and controller._foreground_watch is None
    dialog.assert_not_called()


def test_partial_screen_construction_is_disposed_on_failure(controller, monkeypatch):
    screen = QtGui.QGuiApplication.primaryScreen()
    monkeypatch.setattr(QtGui.QGuiApplication, "screens", lambda: [screen, object()])
    errors = []
    monkeypatch.setattr(controller, "_show_error", lambda *a: errors.append(len(controller.overlays)))
    controller.set_tool("pen")
    assert errors == [0]
    assert not controller.overlays and controller.tool == "pointer"
