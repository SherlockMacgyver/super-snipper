"""Desktop ink for recordings and still snips, owned by the Super Snipper HUD.

Qt coordinates stay local to each screen's overlay; Qt handles that screen's
device pixel ratio when painting. No physical recording coordinates are reused
as Qt coordinates. The overlays deliberately have no capture-excluded parent.
"""

import math
import logging

from . import winapi

TOOLS = ("pointer", "pen", "rectangle", "ellipse", "arrow", "eraser")
COLORS = (("Red", "#ff4058"), ("Yellow", "#ffdc40"), ("Blue", "#409cff"), ("White", "#ffffff"))
WIDTHS = (("Thin", 2), ("Medium", 4), ("Thick", 8))
ERASER_WIDTH = 20


def load_ink_tools(QtCore, QtGui):
    """Stroke geometry shared by the live-screen overlay and the still-image canvas."""
    Qt = QtCore.Qt

    def shape(tool, start, point, width, path=None):
        """Return the draft path for a drag from start to point; pen extends path."""
        if tool == "pen":
            path.lineTo(point)
            return path
        path = QtGui.QPainterPath()
        rect = QtCore.QRectF(start, point).normalized()
        if tool == "rectangle":
            path.addRect(rect)
        elif tool == "ellipse":
            path.addEllipse(rect)
        elif tool == "arrow":
            path.moveTo(start)
            path.lineTo(point)
            angle = math.atan2(point.y() - start.y(), point.x() - start.x())
            length = min(max(12, width * 4), math.hypot(
                point.x() - start.x(), point.y() - start.y()) * 0.45)
            for delta in (-0.5, 0.5):
                path.moveTo(point)
                path.lineTo(point - QtCore.QPointF(
                    length * math.cos(angle + delta), length * math.sin(angle + delta)))
        return path

    def dot(point):
        path = QtGui.QPainterPath(point)
        path.lineTo(point + QtCore.QPointF(0.01, 0))
        return path

    def erase(paths, previous, current, eraser_width=ERASER_WIDTH):
        """Strokes not touched by an eraser sweep from previous to current."""
        sweep = QtGui.QPainterPath(previous)
        sweep.lineTo(current)
        if previous == current:
            sweep.lineTo(current + QtCore.QPointF(0.01, 0))
        brush = QtGui.QPainterPathStroker()
        brush.setWidth(eraser_width)
        brush.setCapStyle(Qt.PenCapStyle.RoundCap)
        area = brush.createStroke(sweep)
        kept = []
        for path, color, width in paths:
            outline = QtGui.QPainterPathStroker()
            outline.setWidth(width)
            if not area.intersects(outline.createStroke(path)):
                kept.append((path, color, width))
        return kept

    def paint(painter, strokes):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for path, color, width in strokes:
            pen = QtGui.QPen(QtGui.QColor(color), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)

    class InkTools:
        pass

    tools = InkTools()
    tools.shape, tools.dot, tools.erase, tools.paint = shape, dot, erase, paint
    return tools


def load_markup_class(QtCore, QtGui, QtWidgets):
    """Keep importing the capture CLI independent of Qt initialization."""
    Qt = QtCore.Qt
    ink = load_ink_tools(QtCore, QtGui)

    class InkOverlay(QtWidgets.QWidget):
        def __init__(self, screen, controller):
            super().__init__()
            self.controller = controller
            self.display = screen
            self.paths = []
            self.draft = None
            self.start = None
            self.setWindowTitle("Super Snipper Markup")
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setMouseTracking(True)
            self.winId()
            self.windowHandle().setScreen(screen)
            self.setGeometry(screen.geometry())
            screen.geometryChanged.connect(self.geometry_changed)

        def geometry_changed(self, rect):
            # Old strokes no longer point at the same content after a display
            # rearrangement. Clear them instead of silently moving annotations.
            self.clear()
            self.setGeometry(rect)
            self.controller.set_tool("pointer")

        def showEvent(self, event):
            super().showEvent(event)
            if QtGui.QGuiApplication.platformName() == "windows":
                try:
                    winapi.set_window_click_through(int(self.winId()), self.controller.tool == "pointer")
                except OSError as exc:
                    self.controller.stacking_failed.emit(str(exc))

        def set_input(self, enabled):
            self.draft = None
            self.start = None
            if QtGui.QGuiApplication.platformName() == "windows":
                winapi.set_window_click_through(int(self.winId()), not enabled)
            else:
                self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not enabled)
            self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
            if not self.isVisible():
                self.show()
            self.update()

        def clear(self):
            self.paths.clear()
            self.draft = None
            self.start = None
            self.update()

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            # Windows passes clicks through fully transparent layered pixels.
            # Alpha 1 makes blank drawing space hit-testable; Pointer restores
            # complete transparency and native window-level input pass-through.
            if self.controller.tool != "pointer":
                painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 1))
            ink.paint(painter, self.paths + ([self.draft] if self.draft else []))
            painter.end()

        def erase(self, previous, current):
            self.paths = ink.erase(self.paths, previous, current)
            self.update()

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.RightButton:
                self.controller.set_tool("pointer")
                event.accept()
                return
            if event.button() != Qt.MouseButton.LeftButton or self.controller.tool == "pointer":
                return
            point = event.position()
            self.start = point
            self.previous = point
            if self.controller.tool == "eraser":
                self.erase(point, point)
            else:
                self.draft = (ink.dot(point), self.controller.color, self.controller.width)
            self.update()
            event.accept()

        def update_draft(self, point):
            tool = self.controller.tool
            if tool == "eraser":
                self.erase(self.previous, point)
            elif self.draft is not None:
                path, color, width = self.draft
                self.draft = (ink.shape(tool, self.start, point, width, path), color, width)
            self.previous = point
            self.update()

        def mouseMoveEvent(self, event):
            if self.start is not None:
                self.update_draft(event.position())

        def mouseReleaseEvent(self, event):
            if event.button() != Qt.MouseButton.LeftButton or self.start is None:
                return
            self.update_draft(event.position())
            if self.draft is not None:
                self.paths.append(self.draft)
            self.draft = None
            self.start = None
            self.update()

        def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Escape:
                self.controller.set_tool("pointer")
                event.accept()
            else:
                super().keyPressEvent(event)

        def dispose(self):
            self.display.geometryChanged.disconnect(self.geometry_changed)
            self.close()
            self.deleteLater()

    class MarkupController(QtCore.QObject):
        tool_changed = QtCore.Signal(str)
        stacking_failed = QtCore.Signal(str)

        def __init__(self, hud):
            super().__init__(hud)
            self.hud = hud
            self.tool = "pointer"
            self.color = "#ff4058"
            self.width = 4
            self.overlays = []
            self.active = False
            self._foreground_watch = None
            self._quitting = False
            self.stacking_failed.connect(self._stacking_error, Qt.ConnectionType.QueuedConnection)
            app = QtGui.QGuiApplication.instance()
            app.screenRemoved.connect(self.screen_removed)
            app.screenAdded.connect(self.screen_added)
            # QApplication closes windows on normal service exit; also dispose
            # before Qt teardown so no screen callbacks outlive the controller.
            app.aboutToQuit.connect(self._shutdown)

        def _shutdown(self):
            self._quitting = True
            self.finish()

        def begin(self):
            self.active = True

        def ensure_overlays(self):
            if not self.overlays:
                try:
                    if self._foreground_watch is not None and self._foreground_watch.disabled:
                        self._foreground_watch.close()
                        self._foreground_watch = None
                    # Offscreen previews have no Windows stack to maintain.
                    if QtGui.QGuiApplication.platformName() == "windows" and self._foreground_watch is None:
                        self._foreground_watch = winapi.ForegroundWatcher(
                            self._restore_stack, self.stacking_failed.emit)
                    # Retain each constructed window immediately so failure on
                    # another monitor still disposes the earlier windows.
                    for screen in QtGui.QGuiApplication.screens():
                        self.overlays.append(InkOverlay(screen, self))
                except Exception as exc:
                    self._stacking_error(str(exc))
                    return False
            return True

        def _restore_stack(self):
            if not self.active:
                return
            visible = [o for o in self.overlays if o.isVisible()]
            if not any(o.paths or o.draft for o in visible):
                return
            for overlay in visible:
                winapi.raise_without_activation(int(overlay.winId()))
            if self.hud.isVisible():
                winapi.raise_without_activation(int(self.hud.winId()))
            popup = QtWidgets.QApplication.activePopupWidget()
            if popup is not None and self.hud.isAncestorOf(popup):
                winapi.raise_without_activation(int(popup.winId()))

        def _stacking_error(self, message):
            logging.error("Super Snipper markup disabled: %s", message)
            cleanup_error = self.finish(report_errors=False)
            if cleanup_error:
                message += "\nCleanup: " + cleanup_error
            self._show_error("Super Snipper markup error",
                             "Unable to keep markup above other windows. Markup was closed.\n" + message)

        def _show_error(self, title, message):
            if self._quitting:
                return
            dialog = QtWidgets.QMessageBox(self.hud)
            dialog.setWindowTitle(title)
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Critical)
            dialog.setText(message)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            # No nested native event loop inside cleanup or aboutToQuit.
            dialog.open()

        def set_tool(self, tool):
            if tool not in TOOLS:
                raise ValueError(f"Unknown markup tool: {tool}")
            if tool != "pointer":
                self.active = True
            self.tool = tool
            if tool != "pointer":
                if not self.ensure_overlays():
                    return
            try:
                for overlay in self.overlays:
                    overlay.set_input(tool != "pointer")
            except OSError as exc:
                self._stacking_error(str(exc))
                return
            if tool != "pointer" and self.overlays:
                screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
                target = next((o for o in self.overlays if o.display == screen), self.overlays[0])
                target.activateWindow()
                target.setFocus()
            # The dock must remain above the ink so Stop and Pointer are reachable.
            if self.hud.isVisible():
                self.hud.raise_()
            self.tool_changed.emit(tool)

        def clear(self):
            for overlay in self.overlays:
                overlay.clear()

        def finish(self, *, report_errors=True):
            self.active = False
            self.tool = "pointer"
            cleanup_error = None
            if self._foreground_watch is not None:
                try:
                    self._foreground_watch.close()
                except OSError as exc:
                    # Keep the disabled callback alive and make cleanup failure
                    # visible; never leave input-blocking overlay windows behind.
                    logging.exception("Unable to release markup foreground hook")
                    cleanup_error = str(exc)
                else:
                    self._foreground_watch = None
            for overlay in self.overlays:
                overlay.dispose()
            self.overlays.clear()
            self.tool_changed.emit(self.tool)
            if cleanup_error and report_errors:
                self._show_error("Super Snipper markup cleanup error", cleanup_error)
            return cleanup_error

        def screen_removed(self, screen):
            self.set_tool("pointer")
            for overlay in list(self.overlays):
                if overlay.display == screen:
                    overlay.dispose()
                    self.overlays.remove(overlay)

        def screen_added(self, screen):
            if self.active and self.overlays:
                self.set_tool("pointer")
                overlay = InkOverlay(screen, self)
                self.overlays.append(overlay)
                overlay.set_input(False)

    return MarkupController
