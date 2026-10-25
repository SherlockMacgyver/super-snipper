"""Snip storyboard window: collect snips in order, mark up each still, stitch one sheet."""

import os
from pathlib import Path
import tempfile

from . import config, markup, series
from .hud_widget import hud_launch_command
from .storyboard import MAX_EDGE


def load_series_dialog(QtCore, QtGui, QtWidgets):
    Qt = QtCore.Qt
    tools = series.load_series_tools(QtCore, QtGui)
    ink = tools.ink

    class StillCanvas(QtWidgets.QWidget):
        """Draw on a still image with the live markup's tools; marks live in image pixels."""
        changed = QtCore.Signal()

        def __init__(self):
            super().__init__()
            self.step = None
            self.image = None
            self.tool = "pen"
            self.color = markup.COLORS[0][1]
            self.pen_width = 4
            self.draft = None
            self.start = None
            self.previous = None
            self.setMinimumSize(360, 220)
            self.setCursor(Qt.CursorShape.CrossCursor)

        def set_step(self, step, image=None):
            self.step = step
            self.image = None if step is None else (image or tools.load_image(step.original))
            self.draft = None
            self.start = None
            self.update()

        def _fit(self):
            scale = min(self.width() / self.image.width(), self.height() / self.image.height())
            return (scale, (self.width() - self.image.width() * scale) / 2,
                    (self.height() - self.image.height() * scale) / 2)

        def to_image(self, position):
            scale, left, top = self._fit()
            return QtCore.QPointF((position.x() - left) / scale, (position.y() - top) / scale)

        def stroke_width(self):
            return self.pen_width * series.mark_scale(self.step.width, self.step.height)

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor("#10141d"))
            if self.image is not None:
                scale, left, top = self._fit()
                painter.translate(left, top)
                painter.scale(scale, scale)
                painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(0, 0, self.image)
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
                ink.paint(painter, self.step.strokes + ([self.draft] if self.draft else []))
            painter.end()

        def _erase(self, previous, current):
            scale = self._fit()[0]
            self.step.strokes[:] = ink.erase(self.step.strokes, previous, current,
                                             markup.ERASER_WIDTH / scale)

        def mousePressEvent(self, event):
            if event.button() != Qt.MouseButton.LeftButton or self.image is None:
                return
            point = self.to_image(event.position())
            self.start = self.previous = point
            if self.tool == "eraser":
                self._erase(point, point)
                self.changed.emit()
            else:
                self.draft = (ink.dot(point), self.color, self.stroke_width())
            self.update()
            event.accept()

        def mouseMoveEvent(self, event):
            if self.start is None:
                return
            point = self.to_image(event.position())
            if self.tool == "eraser":
                self._erase(self.previous, point)
                self.changed.emit()
            elif self.draft is not None:
                path, color, width = self.draft
                self.draft = (ink.shape(self.tool, self.start, point, width, path), color, width)
            self.previous = point
            self.update()

        def mouseReleaseEvent(self, event):
            if event.button() != Qt.MouseButton.LeftButton or self.start is None:
                return
            self.mouseMoveEvent(event)
            if self.draft is not None:
                self.step.strokes.append(self.draft)
                self.changed.emit()
            self.draft = None
            self.start = None
            self.update()

        def undo(self):
            if self.step is not None and self.step.strokes:
                self.step.strokes.pop()
                self.changed.emit()
                self.update()

        def clear_marks(self):
            if self.step is not None and self.step.strokes:
                self.step.strokes.clear()
                self.changed.emit()
                self.update()

    class SeriesDialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Super Snipper - Snip storyboard")
            self.resize(980, 680)
            self.steps = []
            self.dirty = False
            self.output = None
            self.process = None
            self._result_file = None
            self._icons = {}
            # Tests replace this with --capture-box; production opens the normal snip overlay.
            self.capture_args = ["--snip"]
            self.confirm_discard = self._ask_discard
            self.setStyleSheet("QDialog {background:#202632; color:#edf1f9;} "
                               "QLabel {color:#edf1f9;} QPushButton,QToolButton {padding:6px;} "
                               "QListWidget {background:#10141d; color:#edf1f9;}")
            outer = QtWidgets.QVBoxLayout(self)
            body = QtWidgets.QHBoxLayout()
            outer.addLayout(body, 1)

            left = QtWidgets.QVBoxLayout()
            self.list = QtWidgets.QListWidget()
            self.list.setIconSize(QtCore.QSize(112, 72))
            self.list.setMinimumWidth(240)
            self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
            self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.list.currentRowChanged.connect(self.select_step)
            left.addWidget(self.list, 1)
            grid = QtWidgets.QGridLayout()
            self.btn_capture = QtWidgets.QPushButton("Capture snip")
            self.btn_reuse = QtWidgets.QPushButton("Reuse this snip")
            self.btn_reuse.setToolTip("Add a new step on the same original, without this step's marks")
            self.btn_add = QtWidgets.QPushButton("Add image...")
            self.btn_up = QtWidgets.QPushButton("Move up")
            self.btn_down = QtWidgets.QPushButton("Move down")
            self.btn_remove = QtWidgets.QPushButton("Remove step")
            for index, (button, handler) in enumerate((
                    (self.btn_capture, self.capture_snip), (self.btn_reuse, self.reuse_step),
                    (self.btn_add, self.choose_image), (self.btn_remove, self.remove_step),
                    (self.btn_up, lambda: self.move_step(-1)), (self.btn_down, lambda: self.move_step(1)))):
                button.clicked.connect(handler)
                grid.addWidget(button, index // 2, index % 2)
            left.addLayout(grid)
            body.addLayout(left)

            right = QtWidgets.QVBoxLayout()
            toolbar = QtWidgets.QHBoxLayout()
            self.tool_buttons = {}
            group = QtWidgets.QButtonGroup(self)
            for tool, title in (("pen", "Pen"), ("arrow", "Arrow"), ("rectangle", "Rectangle"),
                                ("ellipse", "Ellipse"), ("eraser", "Eraser")):
                button = QtWidgets.QToolButton()
                button.setText(title)
                button.setCheckable(True)
                group.addButton(button)
                button.clicked.connect(lambda checked=False, selected=tool: setattr(self.canvas, "tool", selected))
                toolbar.addWidget(button)
                self.tool_buttons[tool] = button
            self.color = QtWidgets.QComboBox()
            for title, color in markup.COLORS:
                self.color.addItem(title, color)
            self.color.currentIndexChanged.connect(
                lambda: setattr(self.canvas, "color", self.color.currentData()))
            self.width_choice = QtWidgets.QComboBox()
            for title, width in markup.WIDTHS:
                self.width_choice.addItem(title, width)
            self.width_choice.currentIndexChanged.connect(
                lambda: setattr(self.canvas, "pen_width", self.width_choice.currentData()))
            toolbar.addWidget(self.color)
            toolbar.addWidget(self.width_choice)
            self.btn_undo = QtWidgets.QPushButton("Undo")
            self.btn_clear = QtWidgets.QPushButton("Clear marks")
            toolbar.addWidget(self.btn_undo)
            toolbar.addWidget(self.btn_clear)
            toolbar.addStretch(1)
            right.addLayout(toolbar)
            self.canvas = StillCanvas()
            self.canvas.changed.connect(self.marks_changed)
            self.btn_undo.clicked.connect(self.canvas.undo)
            self.btn_clear.clicked.connect(self.canvas.clear_marks)
            self.tool_buttons["pen"].setChecked(True)
            self.width_choice.setCurrentIndex([w for _, w in markup.WIDTHS].index(self.canvas.pen_width))
            right.addWidget(self.canvas, 1)
            body.addLayout(right, 1)

            footer = QtWidgets.QHBoxLayout()
            footer.addWidget(QtWidgets.QLabel("Maximum sheet edge"))
            self.edge = QtWidgets.QSpinBox()
            self.edge.setRange(64, MAX_EDGE)
            self.edge.setValue(2048)
            self.edge.setSingleStep(128)
            self.edge.setSuffix(" px")
            self.edge.valueChanged.connect(self.show_summary)
            footer.addWidget(self.edge)
            self.summary = QtWidgets.QLabel()
            footer.addWidget(self.summary, 1)
            self.btn_create = QtWidgets.QPushButton("Create storyboard")
            self.btn_create.clicked.connect(self.create_sheet)
            self.btn_open = QtWidgets.QPushButton("Open saved image")
            self.btn_open.setEnabled(False)
            self.btn_open.clicked.connect(self.show_saved)
            footer.addWidget(self.btn_create)
            footer.addWidget(self.btn_open)
            outer.addLayout(footer)
            self.status = QtWidgets.QLabel("Capture a snip to start. Each step keeps its own marks; "
                                           "originals are never changed.")
            self.status.setWordWrap(True)
            self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            outer.addWidget(self.status)
            self.refresh()

        # Steps -------------------------------------------------------------
        def current(self):
            row = self.list.currentRow()
            return self.steps[row] if 0 <= row < len(self.steps) else None

        def insert_step(self, step):
            """Add a step only after its original renders; a failure leaves the list unchanged."""
            icon = QtGui.QIcon(QtGui.QPixmap.fromImage(tools.thumbnail(step, None, 112)))
            row = self.list.currentRow() + 1 if self.current() else len(self.steps)
            self.steps.insert(row, step)
            self._icons[step] = icon
            self.dirty = True
            self.refresh(row)

        def add_image(self, path):
            path = Path(path).resolve(strict=True)
            image = tools.load_image(path)
            self.insert_step(series.Step(path, image.width(), image.height()))

        def reuse_step(self):
            step = self.current()
            if step is not None:
                try:
                    self.insert_step(series.Step(step.original, step.width, step.height))
                except ValueError as exc:
                    self.status.setText(f"Could not reuse this snip: {exc}")

        def remove_step(self):
            row = self.list.currentRow()
            if self.current() is not None:
                self._icons.pop(self.steps.pop(row), None)
                self.dirty = True
                self.refresh(min(row, len(self.steps) - 1))

        def move_step(self, offset):
            row = self.list.currentRow()
            target = row + offset
            if self.current() is not None and 0 <= target < len(self.steps):
                self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
                self.dirty = True
                self.refresh(target)

        def refresh(self, select=None):
            self.list.blockSignals(True)
            try:
                self.list.clear()
                # Steps enter only through insert_step with a cached icon, so refresh never
                # reads the disk and the list cannot drift from self.steps.
                for index, step in enumerate(self.steps):
                    item = QtWidgets.QListWidgetItem(f"Step {index + 1}\n{step.original.name}")
                    item.setIcon(self._icons[step])
                    self.list.addItem(item)
            finally:
                self.list.blockSignals(False)
            if select is not None and 0 <= select < len(self.steps):
                self.list.setCurrentRow(select)
            self.select_step(self.list.currentRow())
            has = self.current() is not None
            for button in (self.btn_reuse, self.btn_remove, self.btn_up, self.btn_down,
                           self.btn_undo, self.btn_clear):
                button.setEnabled(has)
            self.btn_create.setEnabled(bool(self.steps))
            self.show_summary()

        def select_step(self, row):
            step = self.steps[row] if 0 <= row < len(self.steps) else None
            try:
                self.canvas.set_step(step)
            except ValueError as exc:
                self.canvas.set_step(None)
                self.status.setText(str(exc))

        def marks_changed(self):
            self.dirty = True
            item = self.list.currentItem()
            if item is not None and self.canvas.step is not None:
                self._icons[self.canvas.step] = QtGui.QIcon(QtGui.QPixmap.fromImage(
                    tools.thumbnail(self.canvas.step, self.canvas.image, 112)))
                item.setIcon(self._icons[self.canvas.step])

        def show_summary(self):
            if not self.steps:
                self.summary.setText("No steps yet.")
                return
            try:
                g = series.plan_grid(self.steps, self.edge.value())
            except ValueError as exc:
                self.summary.setText(str(exc))
                return
            self.summary.setText(f"{g.count} steps | {g.columns} x {g.rows} | {g.empty} empty | "
                                 f"sheet {g.size[0]} x {g.size[1]} px, cells {g.tile_width} x {g.tile_height} px")

        # Capture -----------------------------------------------------------
        def choose_image(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add image", str(config.SCREENSHOT_DIR),
                "Images (*.jpg *.jpeg *.png *.bmp *.webp);;All files (*)")
            if path:
                try:
                    self.add_image(path)
                except (OSError, ValueError) as exc:
                    self.status.setText(f"Could not add image: {exc}")

        def capture_snip(self):
            if self.process is not None:
                return
            handle, name = tempfile.mkstemp(prefix="supersnipper-series-", suffix=".txt")
            os.close(handle)
            self._result_file = Path(name)
            self.btn_capture.setEnabled(False)
            self.hide()  # keep this window out of the snip
            QtCore.QTimer.singleShot(config.PANEL_LAUNCH_DELAY_MS, self._start_capture)

        def _start_capture(self):
            command = hud_launch_command([*self.capture_args, "--snip-output", str(self._result_file)])
            self.process = QtCore.QProcess(self)
            self.process.setProgram(command[0])
            self.process.setArguments(command[1:])
            self.process.finished.connect(self._capture_finished)
            self.process.errorOccurred.connect(self._capture_error)
            self.process.start()

        def _restore_after_capture(self):
            if self.process is not None:
                self.process.deleteLater()
                self.process = None
            self.btn_capture.setEnabled(True)
            self.show()
            self.raise_()
            self.activateWindow()
            text = ""
            if self._result_file is not None:
                text = self._result_file.read_text(encoding="utf-8").strip()
                self._result_file.unlink(missing_ok=True)
                self._result_file = None
            return text

        def _capture_error(self, error):
            if error == QtCore.QProcess.ProcessError.FailedToStart:
                self._restore_after_capture()
                self.status.setText("Snip could not start; no step added.")

        def _capture_finished(self, code, exit_status):
            text = self._restore_after_capture()
            if code != 0 or exit_status != QtCore.QProcess.ExitStatus.NormalExit:
                self.status.setText(f"Snip failed (exit code {code}); no step added. Log: {config.LOG_FILE}")
            elif not text:
                self.status.setText("Snip cancelled; no step added.")
            else:
                try:
                    self.add_image(text)
                    self.status.setText(f"Added step {self.list.currentRow() + 1}: {Path(text).name}")
                except (OSError, ValueError) as exc:
                    self.status.setText(f"Snip saved but could not be added: {exc}")

        # Output ------------------------------------------------------------
        def create_sheet(self):
            QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.output, grid = tools.stitch(self.steps, self.edge.value())
            except (OSError, ValueError, RuntimeError) as exc:
                self.status.setText(f"Storyboard not saved: {exc}")
                return
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.dirty = False
            self.btn_open.setEnabled(True)
            self.status.setText(f"Saved: {self.output}")

        def show_saved(self):
            if self.output and not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.output))):
                self.status.setText(f"Could not open image viewer. Saved: {self.output}")

        def _ask_discard(self):
            return QtWidgets.QMessageBox.question(
                self, "Discard storyboard?",
                f"Close without saving the {len(self.steps)}-step storyboard? Marks are not kept.",
            ) == QtWidgets.QMessageBox.StandardButton.Yes

        def reject(self):
            self.close()

        def closeEvent(self, event):
            if self.process is not None:
                self.status.setText("Finish or cancel the snip first.")
                event.ignore()
            elif self.dirty and self.steps and not self.confirm_discard():
                event.ignore()
            else:
                event.accept()

    return SeriesDialog


def run_series_window():
    from PySide6 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = load_series_dialog(QtCore, QtGui, QtWidgets)()
    dialog.show()
    return app.exec()
