"""The PySide6 capture panel (HUD): buttons, settings, recording lifecycle and docking UI."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional, Tuple


from . import config, docking, recording, winapi


def hud_launch_command(args: list[str]) -> list[str]:
    """Command that re-runs the Super Snipper launcher with the given capture args."""
    return [sys.executable, str(config.ENTRY_SCRIPT), *args]


def _load_hud_class(QtCore, QtGui, QtWidgets):
    """Build the PySide6 capture-HUD widget class from imported Qt modules.

    Defined lazily (Qt is imported by the caller) so importing this module
    does not require Qt, while both the live launcher and the offscreen
    preview renderer share one widget definition.
    """
    from .markup import load_markup_class

    MarkupController = load_markup_class(QtCore, QtGui, QtWidgets)
    Qt = QtCore.Qt
    FG = QtGui.QColor(233, 237, 247)
    MUTED = QtGui.QColor(139, 148, 170)
    ACCENT = QtGui.QColor(89, 148, 255)
    REC = QtGui.QColor(255, 86, 112)
    HUD_BTN = 24
    HUD_PAD = 3
    HUD_GAP = 1
    HUD_SHADOW_MARGIN = 4
    HUD_EDGE_INSET = -HUD_SHADOW_MARGIN

    def paint_icon(painter, box, name: str) -> None:
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        inset = box.width() * 0.30
        r = box.adjusted(inset, inset, -inset, -inset)
        stroke = max(1.4, box.width() * 0.055)

        def make_pen(color):
            pen = QtGui.QPen(color, stroke)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            return pen

        painter.setPen(make_pen(FG))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        def rec_dot() -> None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(REC)
            d = r.width() * 0.32
            painter.drawEllipse(QtCore.QRectF(r.right() - d, r.bottom() - d, d, d))

        def crosshair() -> None:
            painter.setPen(make_pen(ACCENT))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            cx, cy = r.center().x(), r.center().y()
            rad = r.width() * 0.20
            painter.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
            tick = r.width() * 0.16
            painter.drawLine(QtCore.QPointF(cx, r.top()), QtCore.QPointF(cx, r.top() + tick))
            painter.drawLine(QtCore.QPointF(cx, r.bottom()), QtCore.QPointF(cx, r.bottom() - tick))
            painter.drawLine(QtCore.QPointF(r.left(), cy), QtCore.QPointF(r.left() + tick, cy))
            painter.drawLine(QtCore.QPointF(r.right(), cy), QtCore.QPointF(r.right() - tick, cy))

        if name == "snip":
            c = r.width() * 0.34
            corners = [
                [(r.left(), r.top() + c), (r.left(), r.top()), (r.left() + c, r.top())],
                [(r.right() - c, r.top()), (r.right(), r.top()), (r.right(), r.top() + c)],
                [(r.right(), r.bottom() - c), (r.right(), r.bottom()), (r.right() - c, r.bottom())],
                [(r.left() + c, r.bottom()), (r.left(), r.bottom()), (r.left(), r.bottom() - c)],
            ]
            for pts in corners:
                painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(px, py) for px, py in pts]))
        elif name == "region":
            painter.drawRoundedRect(r, 3, 3)
            rec_dot()
        elif name == "arm-region":
            painter.drawRoundedRect(r, 3, 3)
            crosshair()
        elif name in ("full", "arm-full"):
            screen = r.adjusted(0, r.height() * 0.06, 0, -r.height() * 0.24)
            painter.drawRoundedRect(screen, 3, 3)
            painter.drawLine(
                QtCore.QPointF(r.center().x() - r.width() * 0.16, r.bottom()),
                QtCore.QPointF(r.center().x() + r.width() * 0.16, r.bottom()),
            )
            if name == "full":
                rec_dot()
            else:
                crosshair()
        elif name == "stop":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(REC)
            painter.drawRoundedRect(
                r.adjusted(r.width() * 0.08, r.height() * 0.08, -r.width() * 0.08, -r.height() * 0.08),
                3, 3,
            )
        elif name == "markup":
            painter.drawLine(r.bottomLeft(), r.topRight())
            painter.drawLine(r.bottomLeft(), QtCore.QPointF(r.left() + 4, r.bottom()))
            painter.drawPolyline(QtGui.QPolygonF([
                QtCore.QPointF(r.right() - 5, r.bottom() - 3),
                QtCore.QPointF(r.right() - 2, r.bottom()),
                QtCore.QPointF(r.right() + 1, r.bottom() - 3),
            ]))
        elif name == "gear":
            for i, frac in enumerate((0.22, 0.5, 0.78)):
                y = r.top() + r.height() * frac
                painter.setPen(make_pen(MUTED))
                painter.drawLine(QtCore.QPointF(r.left(), y), QtCore.QPointF(r.right(), y))
                knob = r.width() * 0.14
                kx = r.left() + r.width() * (0.68 if i % 2 == 0 else 0.32)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(FG)
                painter.drawEllipse(QtCore.QPointF(kx, y), knob, knob)
        elif name == "close":
            painter.setPen(make_pen(MUTED))
            painter.drawLine(r.topLeft(), r.bottomRight())
            painter.drawLine(r.topRight(), r.bottomLeft())
        elif name == "grip":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(MUTED)
            d = r.width() * 0.15
            for gx in (r.left() + r.width() * 0.32, r.left() + r.width() * 0.68):
                for gy in (r.top() + r.height() * 0.18, r.center().y(), r.bottom() - r.height() * 0.18):
                    painter.drawEllipse(QtCore.QPointF(gx, gy), d, d)
        painter.restore()

    class IconButton(QtWidgets.QAbstractButton):
        def __init__(self, name: str, tip: str, danger: bool = False) -> None:
            super().__init__()
            self._name = name
            self._danger = danger
            self.setFixedSize(HUD_BTN, HUD_BTN)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(tip)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        def paintEvent(self, _event) -> None:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            box = QtCore.QRectF(self.rect())
            if self.underMouse() or self.isDown():
                p.setPen(Qt.PenStyle.NoPen)
                hover = QtGui.QColor(255, 90, 112, 46) if self._danger else QtGui.QColor(255, 255, 255, 26)
                p.setBrush(hover)
                p.drawRoundedRect(box.adjusted(2, 2, -2, -2), 7, 7)
            paint_icon(p, box, self._name)
            p.end()

        def enterEvent(self, _event) -> None:
            self.update()

        def leaveEvent(self, _event) -> None:
            self.update()

    class GripHandle(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setFixedSize(int(HUD_BTN * 0.6), HUD_BTN)
            # Transparent to the mouse so a press here becomes a window drag.
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        def paintEvent(self, _event) -> None:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            paint_icon(p, QtCore.QRectF(self.rect()), "grip")
            p.end()

    class Stepper(QtWidgets.QWidget):
        def __init__(self, caption: str, value: int, step: int, minimum: int, maximum: int) -> None:
            super().__init__()
            self._value = value
            self._step = step
            self._min = minimum
            self._max = maximum
            self.setProperty("cls", "cell")
            lay = QtWidgets.QHBoxLayout(self)
            lay.setContentsMargins(4, 1, 4, 1)
            lay.setSpacing(2)
            cap = QtWidgets.QLabel(caption)
            cap.setProperty("cls", "cap")
            minus = QtWidgets.QPushButton("-")
            plus = QtWidgets.QPushButton("+")
            for b in (minus, plus):
                b.setProperty("cls", "step")
                b.setFixedSize(13, 15)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._label = QtWidgets.QLabel(str(value))
            self._label.setProperty("cls", "val")
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._label.setFixedWidth(25)
            minus.clicked.connect(lambda: self._bump(-self._step))
            plus.clicked.connect(lambda: self._bump(self._step))
            for w in (cap, minus, self._label, plus):
                lay.addWidget(w)

        def _bump(self, delta: int) -> None:
            self._value = min(self._max, max(self._min, self._value + delta))
            self._label.setText(str(self._value))

        def value(self) -> int:
            return self._value

    class SettingToggle(QtWidgets.QPushButton):
        def __init__(self, label: str, checked: bool) -> None:
            super().__init__()
            self._label = label
            self.setCheckable(True)
            self.setChecked(checked)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setProperty("cls", "audio")
            self._sync()
            self.toggled.connect(lambda _c: self._sync())

        def _sync(self) -> None:
            self.setText(f"{self._label} {'on' if self.isChecked() else 'off'}")
            self.setProperty("on", "1" if self.isChecked() else "0")
            self.style().unpolish(self)
            self.style().polish(self)

    class CaptureHud(QtWidgets.QWidget):
        QSS = (
            "#card{background:rgba(17,19,27,240);border:1px solid rgba(78,92,132,150);"
            "border-radius:8px;}"
            "QLabel[cls=\"cap\"]{color:#8b94aa;font:600 8pt 'Segoe UI';}"
            "QLabel[cls=\"val\"]{color:#e9edf7;font:700 9pt 'Segoe UI';}"
            "QWidget[cls=\"cell\"]{background:rgba(255,255,255,14);border-radius:7px;}"
            "QPushButton[cls=\"step\"]{background:rgba(255,255,255,20);color:#c7cde0;border:none;"
            "border-radius:5px;font:700 9pt 'Segoe UI';}"
            "QPushButton[cls=\"step\"]:hover{background:rgba(89,148,255,120);color:#ffffff;}"
            "QPushButton[cls=\"audio\"]{color:#dfe7ff;border:none;border-radius:7px;padding:4px 9px;"
            "font:600 8pt 'Segoe UI';background:rgba(255,255,255,16);}"
            "QPushButton[cls=\"audio\"][on=\"1\"]{background:rgba(60,196,132,150);color:#062b18;}"
            "QLabel[cls=\"recording\"]{color:#ff566d;font:800 9pt 'Segoe UI';padding:0 3px;}"
            "QToolTip{background:#12141b;color:#e9edf7;border:1px solid #3a445c;padding:3px 6px;}"
        )

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Super Snipper")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setStyleSheet(self.QSS)
            self._drag = docking.HudDragController(1, 1, snapped_edge="top", edge_inset=HUD_EDGE_INSET)
            self._orientation = "horizontal"
            self._settings_open = False
            self._record_process: Optional[subprocess.Popen | winapi.ExistingRecordingProcess] = None
            self._recording_active = False
            self._capture_pending = False
            self._cancel_pending = False
            self._cancel_handle = None
            self._exit_requested = False
            self._exit_callback = None
            self._user_hidden = True
            self._positioned = False

            self.grip = GripHandle()
            self.rec_label = QtWidgets.QLabel("REC")
            self.rec_label.setProperty("cls", "recording")
            self.btn_snip = IconButton("snip", "Snip a region")
            self.btn_region = IconButton("region", "Record a region")
            self.btn_arm = IconButton("arm-region", "Record region + armed click")
            self.btn_full = IconButton("full", "Record full screen")
            self.btn_arm_full = IconButton("arm-full", "Record full screen + armed click")
            self.btn_stop = IconButton("stop", "Stop recording", danger=True)
            self.btn_markup = IconButton("markup", "Markup tools for snips and recordings")
            self.btn_gear = IconButton("gear", "Capture settings")
            self.btn_close = IconButton("close", "Hide panel (reopen from the tray or Win+Shift+X)")

            self.btn_snip.clicked.connect(lambda: self._launch(["--snip"]))
            self.btn_region.clicked.connect(lambda: self._record("region", False))
            self.btn_arm.clicked.connect(lambda: self._record("region", True))
            self.btn_full.clicked.connect(lambda: self._record("fullscreen", False))
            self.btn_arm_full.clicked.connect(lambda: self._record("fullscreen", True))
            self.btn_stop.clicked.connect(self._request_stop)
            self.btn_markup.clicked.connect(self._show_markup_menu)
            self.btn_gear.clicked.connect(self._toggle_settings)
            self.btn_close.clicked.connect(self.close)

            self.fps = Stepper("FPS", config.DEFAULT_VIDEO_FPS, 5, 1, 240)
            self.countdown = Stepper("Count", config.DEFAULT_RECORD_COUNTDOWN_SECONDS, 1, 0, 60)
            self.click_delay = Stepper("Click", config.DEFAULT_ARMED_CLICK_DELAY_MS, 50, 0, 2000)
            self.audio = SettingToggle("Audio", True)
            self.cursor_toggle = SettingToggle("Cursor", False)
            self.cursor_toggle.setToolTip("Show the mouse pointer in recorded video")

            self._normal_controls = [
                self.btn_snip, self.btn_region, self.btn_arm,
                self.btn_full, self.btn_arm_full, self.btn_stop, self.btn_gear, self.btn_close,
            ]
            self._icons = [self.grip, self.rec_label, self.btn_markup, *self._normal_controls]
            self._settings = [self.fps, self.countdown, self.click_delay, self.audio, self.cursor_toggle]

            self.card = QtWidgets.QFrame(self)
            self.card.setObjectName("card")
            self.outer = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.Direction.TopToBottom, self.card)
            self.outer.setContentsMargins(HUD_PAD, HUD_PAD, HUD_PAD, HUD_PAD)
            self.outer.setSpacing(HUD_GAP)
            self.icon_row = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.Direction.LeftToRight)
            self.icon_row.setSpacing(HUD_GAP)
            self.settings_row = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.Direction.LeftToRight)
            self.settings_row.setSpacing(HUD_GAP)
            self.settings_host = QtWidgets.QWidget()
            self.settings_host.setLayout(self.settings_row)
            self.settings_host.setVisible(False)
            for w in self._icons:
                self.icon_row.addWidget(w)
            for w in self._settings:
                self.settings_row.addWidget(w)
            self.outer.addLayout(self.icon_row)
            self.outer.addWidget(self.settings_host)

            page = QtWidgets.QVBoxLayout(self)
            page.setContentsMargins(
                HUD_SHADOW_MARGIN,
                HUD_SHADOW_MARGIN,
                HUD_SHADOW_MARGIN,
                HUD_SHADOW_MARGIN,
            )
            page.addWidget(self.card)

            shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(12)
            shadow.setOffset(0, 2)
            shadow.setColor(QtGui.QColor(0, 0, 0, 150))
            self.card.setGraphicsEffect(shadow)

            self.rec_label.setVisible(False)
            self.btn_stop.setVisible(False)
            self.btn_markup.setVisible(True)
            self.markup = MarkupController(self)
            self.markup_menu = QtWidgets.QMenu(self)
            self.markup_actions = {}
            group = QtGui.QActionGroup(self.markup_menu)
            for tool, title in (("pointer", "Pointer - click through"), ("pen", "Pen"),
                                ("rectangle", "Rectangle"), ("ellipse", "Ellipse"),
                                ("arrow", "Arrow"), ("eraser", "Eraser - whole stroke")):
                action = self.markup_menu.addAction(title)
                action.setCheckable(True)
                group.addAction(action)
                action.triggered.connect(lambda checked=False, selected=tool: self.markup.set_tool(selected))
                self.markup_actions[tool] = action
            self.markup_actions["pointer"].setChecked(True)
            self.markup.tool_changed.connect(self._markup_tool_changed)
            self.markup_menu.addSeparator()
            self.markup_menu.addAction("Clear all", self.markup.clear)
            colors = self.markup_menu.addMenu("Color")
            color_group = QtGui.QActionGroup(colors)
            for title, color in (("Red", "#ff4058"), ("Yellow", "#ffdc40"),
                                 ("Blue", "#409cff"), ("White", "#ffffff")):
                action = colors.addAction(title)
                action.setCheckable(True)
                action.setChecked(color == self.markup.color)
                color_group.addAction(action)
                action.triggered.connect(lambda checked=False, value=color: setattr(self.markup, "color", value))
            widths = self.markup_menu.addMenu("Width")
            width_group = QtGui.QActionGroup(widths)
            for title, width in (("Thin", 2), ("Medium", 4), ("Thick", 8)):
                action = widths.addAction(title)
                action.setCheckable(True)
                action.setChecked(width == self.markup.width)
                width_group.addAction(action)
                action.triggered.connect(lambda checked=False, value=width: setattr(self.markup, "width", value))
            self._record_timer = QtCore.QTimer(self)
            self._record_timer.setInterval(150)
            self._record_timer.timeout.connect(self._poll_recording_lifecycle)

        def _poll_recording_lifecycle(self) -> None:
            try:
                self._sync_recording_lifecycle()
            except Exception:
                self._record_timer.stop()
                self.markup.set_tool("pointer")
                self._signal_capture_cancel()
                logging.exception("Unable to verify capture state")
                QtWidgets.QMessageBox.critical(
                    self, "Super Snipper state error",
                    f"Capture state could not be verified. Stop/recovery details are in {config.LOG_FILE}.")

        def monitor_existing_recording(self) -> None:
            state = recording._active_recording_state()
            if state:
                self._record_process = winapi.ExistingRecordingProcess(int(state["pid"]), state["pid_create_time"])
                self._set_recording_mode(True)
                self._record_timer.start()

        def show_panel(self, reset: bool = False) -> None:
            self._user_hidden = False
            screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos()) or self.screen()
            if screen is not None:
                self.winId()
                self.windowHandle().setScreen(screen)
            self.show()
            self._reflow()
            vx, vy, width, height = self._screen_bounds()
            if reset:
                self._drag.snapped_edge = None
                self._set_orientation("horizontal")
                self.move(vx + (width - self.width()) // 2, vy + (height - self.height()) // 2)
            else:
                self.move(max(vx, min(self.x(), vx + width - self.width())),
                          max(vy, min(self.y(), vy + height - self.height())))
            self.raise_()
            self.activateWindow()

        def hide_panel(self) -> None:
            self.markup.set_tool("pointer")
            self.markup_menu.hide()
            self._user_hidden = True
            self.hide()

        def dock_panel(self, edge: Optional[str]) -> None:
            self.show_panel()
            self._drag.snapped_edge = edge
            self._set_orientation(docking.hud_orientation_for_edge(edge))
            if edge:
                self._move_to_geometry(docking.panel_dock_geometry(
                    edge, self.width(), self.height(), self._screen_bounds(), HUD_EDGE_INSET))
            else:
                self.show_panel(reset=True)

        def _signal_capture_cancel(self) -> None:
            if self._capture_pending:
                self._cancel_pending = True
            if self._cancel_handle is not None:
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.SetEvent.argtypes = (wintypes.HANDLE,)
                kernel.SetEvent.restype = wintypes.BOOL
                if not kernel.SetEvent(self._cancel_handle):
                    raise ctypes.WinError(ctypes.get_last_error())

        def request_exit(self, callback) -> None:
            if self._exit_requested:
                raise RuntimeError("Super Snipper is already finishing its capture and exiting")
            self._exit_requested = True
            self.markup.set_tool("pointer")
            self._exit_callback = callback
            self._signal_capture_cancel()
            self._record_timer.start()
            self._request_stop()

        def handle_command(self, command: str, done) -> None:
            if self._exit_requested:
                raise RuntimeError("Super Snipper is finishing its capture before exiting")
            if command == "exit":
                self.request_exit(done)
                return
            if command == "show":
                self.show_panel()
            elif command == "hide":
                self.hide_panel()
            elif command in ("reset", "undock"):
                self.dock_panel(None)
            elif command.startswith("dock-"):
                self.dock_panel(command[5:])
            elif command == "snip":
                self._launch(["--snip"])
            elif command == "stop":
                self._signal_capture_cancel()
                self._request_stop()
            elif command in ("region", "arm-region", "full"):
                if self._record_process is not None or recording._active_recording_state():
                    self._signal_capture_cancel()
                    self._request_stop()
                else:
                    self._record("fullscreen" if command == "full" else "region", command == "arm-region")
            else:
                raise ValueError(f"Unknown panel command: {command}")
            done(None)

        def _set_recording_mode(self, active: bool, stopping: bool = False) -> None:
            if active and not self.markup.active:
                self.markup.begin()
            elif not active and self._recording_active:
                self.markup.finish()
                self.markup_menu.hide()
            if stopping:
                self.markup.set_tool("pointer")
                self.markup_menu.hide()
            self.btn_markup.setVisible(True)
            self.btn_markup.setEnabled(not stopping and not self._exit_requested)
            self._recording_active = active
            self.rec_label.setText("STOPPING" if stopping else "REC")
            self.rec_label.setVisible(active)
            for control in self._normal_controls:
                control.setVisible(not active)
            self.btn_stop.setVisible(active)
            self.btn_stop.setEnabled(active and not stopping)
            if active:
                self.settings_host.setVisible(False)
            else:
                self._sync_settings_visibility()
            self._reflow()

        def _start_recording_process(self, command: list[str]) -> None:
            self._capture_pending = False
            try:
                if self._exit_requested or self._cancel_pending:
                    self._cancel_pending = False
                    self._sync_recording_lifecycle()
                    return
                event_name = "Local\\SuperSnipper.Capture." + uuid.uuid4().hex
                self._cancel_handle = winapi._capture_event(event_name, create=True)
                self._record_process = subprocess.Popen(
                    command + ["--cancel-event", event_name], cwd=str(config.APP_DIR),
                    **winapi.hidden_subprocess_kwargs(new_process_group=False))
                self._record_timer.start()
            except Exception:
                if self._cancel_handle is not None:
                    winapi._close_handle(self._cancel_handle)
                    self._cancel_handle = None
                logging.exception("Unable to start Super Snipper recording process")
                self._record_timer.stop()
                self._set_recording_mode(False)
                self.show()
                self.raise_()
                QtWidgets.QMessageBox.critical(
                    self,
                    "Super Snipper recording failed",
                    f"The recording process could not start. See {config.LOG_FILE} for details.",
                )

        def _sync_recording_lifecycle(self) -> None:
            state = recording._active_recording_state()
            if state:
                if self._record_process is None:
                    self._record_process = winapi.ExistingRecordingProcess(int(state["pid"]), state["pid_create_time"])
                if not self._recording_active:
                    self._set_recording_mode(True)
                    if not self._user_hidden:
                        self.show_panel()
                if self._exit_requested:
                    recording._request_recording_stop(state)
                return

            exit_code = self._record_process.poll() if self._record_process is not None else None
            child_alive = self._record_process is not None and exit_code is None
            if child_alive or self._capture_pending:
                return

            self._record_process = None
            if self._cancel_handle is not None:
                winapi._close_handle(self._cancel_handle)
                self._cancel_handle = None
            self._record_timer.stop()
            if self._exit_requested:
                self._set_recording_mode(False)
                self.markup.finish()
                callback = self._exit_callback
                if callback is None:
                    return
                self._exit_callback = None
                if exit_code not in (None, 0):
                    self._exit_requested = False
                    self._set_recording_mode(False)
                    callback("Capture failed while exiting; raw media was preserved. Check the Super Snipper log.")
                    self.show_panel()
                    return
                try:
                    callback(None)
                finally:
                    QtCore.QTimer.singleShot(100, lambda: QtWidgets.QApplication.instance().exit(0))
                return
            restore_panel = self._recording_active or not self.isVisible()
            self._set_recording_mode(False)
            if restore_panel:
                if not self._user_hidden:
                    self.show_panel()
                if exit_code not in (None, 0):
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Super Snipper recording failed",
                        "The recording could not be completed. Any recoverable raw media was "
                        f"preserved. See {config.LOG_FILE} and {config.FFMPEG_LOG_FILE} for details.",
                    )

        def _request_stop(self) -> None:
            self._signal_capture_cancel()
            state = recording._active_recording_state()
            if not state:
                self._sync_recording_lifecycle()
                return
            if self._record_process is None:
                self._record_process = winapi.ExistingRecordingProcess(int(state["pid"]), state["pid_create_time"])
            recording._request_recording_stop(state)
            self._set_recording_mode(True, stopping=True)

        def _toggle_settings(self) -> None:
            self._settings_open = not self._settings_open
            self._sync_settings_visibility()
            self._reflow()

        def _markup_tool_changed(self, tool: str) -> None:
            self.markup_actions[tool].setChecked(True)
            self.btn_markup.setToolTip(f"Markup: {tool}. Esc or right-click returns to Pointer.")

        def _show_markup_menu(self) -> None:
            if self.btn_markup.isEnabled():
                self.markup_menu.popup(self.btn_markup.mapToGlobal(
                    QtCore.QPoint(0, self.btn_markup.height())))

        def _sync_settings_visibility(self) -> None:
            # Side-docked mode must stay a skinny rail. Preserve the user's
            # settings-open state, but do not let settings widen the vertical
            # HUD into a block.
            self.settings_host.setVisible(self._settings_open and self._orientation != "vertical")

        def _activate_layouts(self) -> None:
            layouts = [self.icon_row, self.settings_row, self.outer, self.layout()]
            for layout in layouts:
                if layout is not None:
                    layout.invalidate()
            self.settings_host.updateGeometry()
            self.card.updateGeometry()
            self.updateGeometry()
            for layout in layouts:
                if layout is not None:
                    layout.activate()

        def _screen_bounds(self) -> Tuple[int, int, int, int]:
            # Qt logical (DPI-scaled) geometry -- the coordinate space move()
            # uses. availableGeometry keeps the HUD clear of the taskbar.
            screen = self.screen() or QtWidgets.QApplication.primaryScreen()
            geo = screen.availableGeometry()
            return (geo.x(), geo.y(), geo.width(), geo.height())

        def _reflow(self) -> None:
            vertical = self._orientation == "vertical"
            self.outer.setDirection(
                QtWidgets.QBoxLayout.Direction.LeftToRight if vertical
                else QtWidgets.QBoxLayout.Direction.TopToBottom
            )
            self.icon_row.setDirection(
                QtWidgets.QBoxLayout.Direction.TopToBottom if vertical
                else QtWidgets.QBoxLayout.Direction.LeftToRight
            )
            self.settings_row.setDirection(
                QtWidgets.QBoxLayout.Direction.TopToBottom if vertical
                else QtWidgets.QBoxLayout.Direction.LeftToRight
            )
            self._activate_layouts()
            self.card.adjustSize()
            self.adjustSize()
            self._activate_layouts()
            self.adjustSize()
            bounds = self._screen_bounds()
            self._drag.resize(self.width(), self.height())
            self._drag.set_screen(bounds)
            self._drag.set_edge_inset(HUD_EDGE_INSET)
            if self._drag.snapped_edge:
                # Keep the HUD where it was slid along the edge after a resize or
                # orientation flip, rather than snapping it back to edge-center.
                x, y = docking.slide_panel_on_edge(
                    self._drag.snapped_edge, self.x(), self.y(),
                    self.width(), self.height(), screen=bounds, edge_inset=HUD_EDGE_INSET,
                )
                self.move(x, y)

        def _set_orientation(self, orientation: str) -> None:
            if orientation != self._orientation:
                self._orientation = orientation
                self._sync_settings_visibility()
                self._reflow()

        def _move_to_geometry(self, geometry: str) -> None:
            xs, ys = geometry[1:].split("+", 1)
            self.move(int(xs), int(ys))

        def showEvent(self, event) -> None:
            super().showEvent(event)
            self.adjustSize()
            bounds = self._screen_bounds()
            self._drag.resize(self.width(), self.height())
            self._drag.set_screen(bounds)
            self._drag.set_edge_inset(HUD_EDGE_INSET)
            if not self._positioned:
                self._move_to_geometry(docking.panel_dock_geometry(
                    "top", self.width(), self.height(), bounds, HUD_EDGE_INSET))
                self._positioned = True
            self._exclude_from_capture()

        def _exclude_from_capture(self) -> None:
            try:
                hwnd = int(self.winId())
                ok = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, config.WDA_EXCLUDEFROMCAPTURE)
                if not ok:
                    logging.warning("SetWindowDisplayAffinity failed for Super Snipper HUD")
            except Exception:
                logging.exception("Unable to exclude Super Snipper HUD from capture")

        def _launch(self, args: list[str]) -> None:
            if self._capture_pending or self._record_process is not None or recording._active_recording_state():
                raise RuntimeError("Finish or stop the current capture before starting another")
            command = hud_launch_command(args)
            self.markup.set_tool("pointer")
            self.markup_menu.hide()
            self.btn_markup.setEnabled(False)
            self._capture_pending = True
            self.hide()
            QtCore.QTimer.singleShot(config.PANEL_LAUNCH_DELAY_MS, lambda: self._start_recording_process(command))

        def _record(self, mode: str, arm_click: bool) -> None:
            if self._capture_pending or self._record_process is not None or self._recording_active:
                return
            command = hud_launch_command(
                recording._record_command_args(
                    mode,
                    fps=self.fps.value(),
                    countdown=self.countdown.value(),
                    with_audio=self.audio.isChecked(),
                    arm_click=arm_click,
                    click_delay_ms=self.click_delay.value(),
                    show_cursor=self.cursor_toggle.isChecked(),
                )
            )
            self._capture_pending = True
            self.markup.set_tool("pointer")
            self.markup_menu.hide()
            self.btn_markup.setEnabled(False)
            self.hide()
            QtCore.QTimer.singleShot(
                config.PANEL_LAUNCH_DELAY_MS, lambda: self._start_recording_process(command)
            )

        def keyPressEvent(self, event) -> None:
            if event.key() == Qt.Key.Key_Escape:
                if self._recording_active:
                    self._request_stop()
                    return
                self.close()
                return
            super().keyPressEvent(event)

        def closeEvent(self, event) -> None:
            if self._exit_requested and self._exit_callback is None:
                event.accept()
                return
            self.hide_panel()
            event.ignore()

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.globalPosition().toPoint()
                self._drag.resize(self.width(), self.height())
                self._drag.set_screen(self._screen_bounds())
                self._drag.set_edge_inset(HUD_EDGE_INSET)
                self._drag.begin(pos.x(), pos.y(), self.x(), self.y())
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                pos = event.globalPosition().toPoint()
                x, y = self._drag.move_to(pos.x(), pos.y())
                self.move(x, y)
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                x, y, _edge, orientation = self._drag.release(self.x(), self.y())
                self.move(x, y)
                self._set_orientation(orientation)
            super().mouseReleaseEvent(event)

    return CaptureHud


def render_hud_preview(out_path: Path, orientation: str = "horizontal", settings_open: bool = False) -> Path:
    """Render the HUD offscreen to a PNG so its look can be reviewed without a
    live desktop session. Returns the written path."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtWidgets

    CaptureHud = _load_hud_class(QtCore, QtGui, QtWidgets)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hud = CaptureHud()
    if settings_open:
        hud._toggle_settings()
    if orientation == "vertical":
        hud._set_orientation("vertical")
    hud.show()
    app.processEvents()
    hud.adjustSize()
    app.processEvents()
    pixmap = hud.grab()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(out_path), "PNG")
    return out_path
