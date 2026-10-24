"""Windows API helpers: DPI awareness, clipboard, handles, named events and hidden subprocesses."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import logging
import subprocess
import sys
import time
from typing import Any, Optional, Tuple

import psutil
from PIL import Image

from . import config


# Named event set by the panel to cancel a capture worker; see cli.main().
_CANCEL_EVENT = None

# A failed unregister must not let ctypes free a callback still held by Windows.
# Such a listener is disabled and retained until a successful retry or exit.
_FAILED_FOREGROUND_HOOKS = set()


class ForegroundWatcher:
    """UI-thread foreground events only; never react to our own Z-order changes.

    WINEVENT_OUTOFCONTEXT delivers on the installing thread's message loop.
    close() must run on that same thread, before the controller is disposed.
    """

    def __init__(self, changed, failed):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD,
                                          wintypes.HWND, wintypes.LONG, wintypes.LONG,
                                          wintypes.DWORD, wintypes.DWORD)
        self.changed = changed
        self.failed = failed
        self.disabled = False
        self.handle = None
        self.callback = callback_type(self._dispatch)
        self.user32.SetWinEventHook.argtypes = (
            wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, callback_type,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
        self.user32.SetWinEventHook.restype = wintypes.HANDLE
        self.user32.UnhookWinEvent.argtypes = (wintypes.HANDLE,)
        self.user32.UnhookWinEvent.restype = wintypes.BOOL
        self.handle = self.user32.SetWinEventHook(3, 3, None, self.callback, 0, 0, 0)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def _dispatch(self, hook, event, hwnd, object_id, child_id, thread_id, timestamp):
        if self.disabled or not self.handle or event != 3 or not hwnd:
            return
        try:
            self.changed()
        except Exception as exc:
            self.disabled = True
            logging.exception("Unable to restore Super Snipper ink order")
            # The Qt consumer queues its error dialog outside the native callback.
            self.failed(str(exc))

    def close(self):
        self.disabled = True
        if self.handle:
            if not self.user32.UnhookWinEvent(self.handle):
                _FAILED_FOREGROUND_HOOKS.add(self)
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None
        _FAILED_FOREGROUND_HOOKS.discard(self)
        self.callback = None


def raise_without_activation(hwnd: int) -> None:
    """Put an existing window atop the topmost band, retaining keyboard focus."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.SetWindowPos.restype = wintypes.BOOL
    # HWND_TOPMOST; SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE.
    if not user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013):
        raise ctypes.WinError(ctypes.get_last_error())


def set_window_click_through(hwnd: int, enabled: bool) -> None:
    """Change input transparency in place; Qt flag changes hide a visible widget."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
    get_style = getattr(user32, "GetWindowLong" + suffix)
    set_style = getattr(user32, "SetWindowLong" + suffix)
    get_style.argtypes = (wintypes.HWND, ctypes.c_int)
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    set_style.restype = ctypes.c_ssize_t
    ctypes.set_last_error(0)
    style = get_style(hwnd, -20)  # GWL_EXSTYLE
    if not style and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    updated = style | 0x20 if enabled else style & ~0x20  # WS_EX_TRANSPARENT
    if updated == style:
        return
    ctypes.set_last_error(0)
    if not set_style(hwnd, -20, updated) and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.SetWindowPos.restype = wintypes.BOOL
    # FRAMECHANGED | NOACTIVATE | NOZORDER | NOSIZE | NOMOVE.
    if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037):
        raise ctypes.WinError(ctypes.get_last_error())


def hidden_subprocess_kwargs(*, new_process_group: bool = True) -> dict[str, Any]:
    """Return subprocess kwargs that suppress console windows on Windows."""
    if sys.platform != "win32":
        return {}
    flags = subprocess.CREATE_NO_WINDOW
    if new_process_group:
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {"creationflags": flags, "startupinfo": startupinfo}


def _acquire_control_panel_mutex() -> Optional[int]:
    """Own the one allowed Super Snipper control-panel process."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = create_mutex(None, False, config.CONTROL_PANEL_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        return None
    return int(handle)


def _close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    if not close_handle(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def _capture_event(name: str, *, create: bool = False) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if create:
        fn = kernel.CreateEventW
        fn.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        fn.restype = wintypes.HANDLE
        handle = fn(None, True, False, name)
    else:
        fn = kernel.OpenEventW
        fn.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        fn.restype = wintypes.HANDLE
        handle = fn(0x00100000, False, name)  # SYNCHRONIZE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def _capture_cancelled() -> bool:
    if _CANCEL_EVENT is None:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = kernel.WaitForSingleObject
    fn.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    fn.restype = wintypes.DWORD
    result = fn(_CANCEL_EVENT, 0)
    if result == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    return result == 0


def _check_capture_cancelled() -> None:
    if _capture_cancelled():
        raise config.RecordingCancelled("Capture cancelled from Super Snipper controls")


def _watch_capture_cancel(root, cancel) -> None:
    def poll():
        if _capture_cancelled():
            cancel()
        else:
            root.after(100, poll)
    if _CANCEL_EVENT is not None:
        root.after(100, poll)


class ExistingRecordingProcess:
    """Retain the OS process handle when adopting a CLI-started recorder."""

    def __init__(self, pid: int, expected_started: Optional[float] = None) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.returncode = None
        if expected_started is not None:
            try:
                if psutil.Process(pid).create_time() != expected_started:
                    raise RuntimeError("Recorder process identity changed before attachment")
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        if self.handle is not None:
            _close_handle(self.handle)
            self.handle = None

    def poll(self) -> Optional[int]:
        if self.handle is None:
            return self.returncode
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value == 259:  # STILL_ACTIVE
            return None
        self.returncode = code.value
        self.close()
        return self.returncode


def set_dpi_awareness() -> None:
    """Request per-monitor DPI awareness before any UI or capture work."""
    user32 = ctypes.windll.user32
    try:
        # PER_MONITOR_AWARE_V2. Negative constants need c_void_p wrapping.
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass

    try:
        user32.SetProcessDPIAware()
    except Exception:
        logging.exception("Failed to set DPI awareness")
        raise


def virtual_screen() -> Tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(config.SM_XVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(config.SM_YVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(config.SM_CXVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(config.SM_CYVIRTUALSCREEN)),
    )


def copy_image_to_clipboard(image: Image.Image) -> None:
    """Copy a PIL image to the Windows clipboard as CF_DIB."""
    rgb = image.convert("RGB")
    stream = io.BytesIO()
    rgb.save(stream, "BMP")
    dib = stream.getvalue()[14:]  # strip BITMAPFILEHEADER for CF_DIB

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p

    handle = kernel32.GlobalAlloc(config.GMEM_MOVEABLE, len(dib))
    if not handle:
        raise ctypes.WinError()

    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError()

    ctypes.memmove(locked, dib, len(dib))
    kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise ctypes.WinError()

    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError()
        if not user32.SetClipboardData(config.CF_DIB, handle):
            raise ctypes.WinError()
        # Clipboard owns the handle after SetClipboardData succeeds.
        handle = None
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def _click_screen_point(point: Tuple[int, int], restore_cursor: bool = True) -> None:
    set_dpi_awareness()
    user32 = ctypes.windll.user32
    old_point = wintypes.POINT()
    has_old_point = bool(user32.GetCursorPos(ctypes.byref(old_point))) if restore_cursor else False
    x, y = int(point[0]), int(point[1])
    if not user32.SetCursorPos(x, y):
        raise RuntimeError(f"Unable to move cursor to armed click target {x},{y}")
    user32.mouse_event(config.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(config.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if has_old_point:
        user32.SetCursorPos(old_point.x, old_point.y)
