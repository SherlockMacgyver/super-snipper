"""Standalone storyboard window; decoding stays off the GUI thread."""

from pathlib import Path
import threading

from . import storyboard


def load_storyboard_dialog(QtCore, QtGui, QtWidgets):
    class Worker(QtCore.QThread):
        result = QtCore.Signal(object)
        failed = QtCore.Signal(str)
        progress = QtCore.Signal(int, int)

        def __init__(self, operation, parent):
            super().__init__(parent)
            self.operation = operation
            self.cancel = threading.Event()

        def run(self):
            try:
                self.result.emit(self.operation(self.cancel, self.progress.emit))
            except Exception as exc:
                self.failed.emit(str(exc))

    class StoryboardDialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Super Snipper - Video storyboard")
            self.resize(680, 640)
            self.info = None
            self.worker = None
            self._closing = False
            self.output = None
            self.setStyleSheet("QDialog {background:#202632; color:#edf1f9;} "
                               "QLabel {color:#edf1f9;} QComboBox,QDoubleSpinBox,QSpinBox "
                               "{padding:5px;} QPushButton {padding:7px;}")
            layout = QtWidgets.QVBoxLayout(self)
            self.browse = QtWidgets.QPushButton("Choose video...")
            self.browse.clicked.connect(self.choose_video)
            layout.addWidget(self.browse)
            self.source = QtWidgets.QLabel("Select a recording or another video file.")
            self.source.setWordWrap(True)
            layout.addWidget(self.source)
            form = QtWidgets.QFormLayout()
            row = QtWidgets.QHBoxLayout()
            self.rate = QtWidgets.QDoubleSpinBox()
            self.rate.setRange(0.001, 60000)
            self.rate.setDecimals(3)
            self.rate.setValue(6)
            self.units = QtWidgets.QComboBox()
            self.units.addItems(["frames per minute", "frames per second"])
            row.addWidget(self.rate)
            row.addWidget(self.units)
            form.addRow("Sample rate", row)
            self.edge = QtWidgets.QSpinBox()
            self.edge.setRange(64, storyboard.MAX_EDGE)
            self.edge.setValue(2048)
            self.edge.setSingleStep(128)
            self.edge.setSuffix(" px")
            form.addRow("Maximum sheet edge", self.edge)
            self.choices = QtWidgets.QComboBox()
            form.addRow("Frame count", self.choices)
            layout.addLayout(form)
            self.summary = QtWidgets.QLabel("Frames stay in order, left to right, then down.")
            self.summary.setWordWrap(True)
            self.summary.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                       QtWidgets.QSizePolicy.Policy.Minimum)
            layout.addWidget(self.summary)
            self.preview = QtWidgets.QLabel()
            self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.preview.setMinimumHeight(180)
            layout.addWidget(self.preview, 1)
            self.status = QtWidgets.QLabel("One PNG is saved beside the video. Existing files are kept.")
            self.status.setWordWrap(True)
            self.status.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self.status)
            self.bar = QtWidgets.QProgressBar()
            self.bar.hide()
            layout.addWidget(self.bar)
            buttons = QtWidgets.QHBoxLayout()
            self.save = QtWidgets.QPushButton("Create storyboard")
            self.save.setEnabled(False)
            self.save.clicked.connect(self.create_sheet)
            self.cancel = QtWidgets.QPushButton("Cancel")
            self.cancel.setEnabled(False)
            self.cancel.clicked.connect(self.cancel_work)
            self.open_image = QtWidgets.QPushButton("Open saved image")
            self.open_image.setEnabled(False)
            self.open_image.clicked.connect(self.show_saved)
            for button in (self.save, self.cancel, self.open_image):
                buttons.addWidget(button)
            layout.addLayout(buttons)
            self.rate.valueChanged.connect(self.replan)
            self.units.currentIndexChanged.connect(self.replan)
            self.edge.valueChanged.connect(self.replan)
            self.choices.currentIndexChanged.connect(self.show_plan)

        def choose_video(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose video", "",
                "Videos (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv);;All files (*)")
            if path:
                self.load_video(Path(path))

        def load_video(self, path):
            self.info = None
            self.output = None
            self.open_image.setEnabled(False)
            self.choices.clear()
            self.preview.clear()
            self.summary.setText("Reading video...")
            self.source.setText(str(path))
            self.start_work(lambda cancel, progress: storyboard.inspect_video(path, cancel), self.video_ready)

        def start_work(self, operation, callback):
            if self.worker is not None:
                raise RuntimeError("A storyboard operation is already running.")
            for widget in (self.browse, self.rate, self.units, self.edge, self.choices, self.save):
                widget.setEnabled(False)
            self.cancel.setEnabled(True)
            self.bar.setRange(0, 0)
            self.bar.show()
            self.status.setText("Working...")
            self.worker = Worker(operation, self)
            self.worker.result.connect(callback)
            self.worker.failed.connect(self.status.setText)
            self.worker.progress.connect(self.update_progress)
            self.worker.finished.connect(self.work_finished)
            self.worker.start()

        def update_progress(self, done, total):
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.status.setText(f"Decoded {done} of {total} frames...")

        def work_finished(self):
            self.worker.deleteLater()
            self.worker = None
            self.bar.hide()
            self.cancel.setEnabled(False)
            for widget in (self.browse, self.rate, self.units, self.edge, self.choices):
                widget.setEnabled(True)
            self.save.setEnabled(self.choices.currentData() is not None)
            if self._closing:
                self.close()

        def video_ready(self, info):
            self.info = info
            self.source.setText(f"{info.path.name} | {info.duration:g} seconds | {info.width} x {info.height}")
            self.status.setText("Choose your rate and resolution, then create the sheet.")
            self.replan()

        def replan(self):
            self.choices.clear()
            self.preview.clear()
            self.save.setEnabled(False)
            if self.info is None:
                return
            try:
                rate = self.rate.value() / (60 if self.units.currentIndex() == 0 else 1)
                exact = storyboard.make_plan(self.info, rate, self.edge.value())
                self.choices.addItem(f"Requested rate: {exact.grid.count} frames", exact)
                for alternative in storyboard.nearby_plans(exact):
                    relation = "Fewer frames, wider time gaps" if alternative.grid.count < exact.grid.count else "More frames, smaller or equal tiles"
                    self.choices.addItem(f"{relation}: {alternative.grid.count}", alternative)
                self.save.setEnabled(self.worker is None)
                self.show_plan()
            except ValueError as exc:
                self.summary.setText(str(exc))

        def show_plan(self):
            plan = self.choices.currentData()
            if plan is None:
                return
            g = plan.grid
            self.summary.setText(f"{g.count} frames | {g.columns} columns x {g.rows} rows | "
                f"{g.empty} empty cells\nSheet {g.size[0]} x {g.size[1]} px; each frame "
                f"{g.tile_width} x {g.tile_height} px\nActual rate: {plan.rate:.6g} frames/second "
                f"({plan.rate * 60:.6g}/minute); one frame every {1 / plan.rate:.6g} seconds. "
                "First sample: 0 seconds. AI apps may resize uploads.")
            self.summary.setMinimumHeight(self.summary.heightForWidth(max(240, self.width() - 40)))
            scale = min(560 / g.size[0], 190 / g.size[1])
            pixmap = QtGui.QPixmap(max(1, round(g.size[0] * scale)), max(1, round(g.size[1] * scale)))
            pixmap.fill(QtGui.QColor("#10141d"))
            painter = QtGui.QPainter(pixmap)
            for index in range(g.count):
                rect = QtCore.QRectF((index % g.columns) * g.tile_width * scale,
                    (index // g.columns) * g.tile_height * scale, g.tile_width * scale, g.tile_height * scale)
                painter.fillRect(rect.adjusted(1, 1, -1, -1), QtGui.QColor("#365077"))
                if rect.width() > 18 and rect.height() > 15:
                    painter.setPen(QtGui.QColor("white"))
                    painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, str(index + 1))
            painter.end()
            self.preview.setPixmap(pixmap)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.summary.setMinimumHeight(self.summary.heightForWidth(max(240, self.width() - 40)))

        def create_sheet(self):
            plan = self.choices.currentData()
            if plan is not None:
                self.start_work(lambda cancel, progress: storyboard.export_sheet(plan, cancel, progress), self.sheet_ready)

        def sheet_ready(self, output):
            self.output = output
            self.status.setText(f"Saved: {output}")
            self.open_image.setEnabled(True)

        def show_saved(self):
            if self.output and not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.output))):
                self.status.setText(f"Could not open image viewer. Saved: {self.output}")

        def cancel_work(self):
            if self.worker:
                self.worker.cancel.set()
                self.status.setText("Cancelling...")

        def reject(self):
            self.close()

        def closeEvent(self, event):
            if self.worker is not None:
                self._closing = True
                self.cancel_work()
                event.ignore()
            else:
                event.accept()

    return StoryboardDialog


def run_storyboard_window():
    from PySide6 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = load_storyboard_dialog(QtCore, QtGui, QtWidgets)()
    dialog.show()
    return app.exec()
