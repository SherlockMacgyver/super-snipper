"""Single-panel command channel: a session-scoped QLocalServer the tray and hotkeys talk to."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
import subprocess
import sys
import time

import psutil

from . import config, hud_widget, recording, winapi


def _panel_server_name() -> str:
    # Match the Windows-session scope of the Local mutex; never contact another
    # user's desktop. No PID file or cached visibility is used for decisions.
    session = wintypes.DWORD()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError()
    return f"StructuredChaos.SuperSnipper.{session.value}" + ("." + config.PANEL_INSTANCE if config.PANEL_INSTANCE else "")


def _panel_request(QtNetwork, command: str, *, timeout_ms: int = 5000) -> bool:
    socket = QtNetwork.QLocalSocket()
    socket.connectToServer(_panel_server_name())
    if not socket.waitForConnected(200):
        return False
    try:
        socket.write((command + "\n").encode("ascii"))
        socket.flush()
        if socket.bytesToWrite():
            socket.waitForBytesWritten(1000)
            if socket.bytesToWrite():
                raise RuntimeError("Could not send Super Snipper command: " + socket.errorString())
        deadline = time.monotonic() + timeout_ms / 1000
        data = b""
        while b"\n" not in data:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining > 0 and not socket.bytesAvailable():
                socket.waitForReadyRead(remaining)
            if remaining <= 0 or not socket.bytesAvailable():
                raise RuntimeError("Super Snipper did not acknowledge " + command + ". The tray remains available.")
            data += bytes(socket.readAll())
        result = json.loads(data.split(b"\n", 1)[0])
        if result.get("error"):
            raise RuntimeError(result["error"])
        if command == "exit":
            try:
                psutil.Process(result["pid"]).wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        return True
    finally:
        socket.abort()


def show_control_panel(command: str = "show") -> None:
    from PySide6 import QtCore, QtNetwork
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication(sys.argv)
    timeout = 90000 if command == "exit" else 5000
    if _panel_request(QtNetwork, command, timeout_ms=timeout):
        return
    # The OS mutex serializes startup. Losing clients wait for the same server.
    mutex = winapi._acquire_control_panel_mutex()
    if mutex is not None:
        winapi._close_handle(mutex)
        if command in ("hide", "stop", "exit"):
            # A standalone CLI recording can outlive a missing panel. Bring up
            # its controller to stop/finalize it before acknowledging Exit.
            if not recording._active_recording_state():
                return
        subprocess.Popen(hud_widget.hud_launch_command(["--panel-service"]), cwd=str(config.APP_DIR),
                         **winapi.hidden_subprocess_kwargs(new_process_group=False))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _panel_request(QtNetwork, command, timeout_ms=timeout):
            return
        time.sleep(0.05)
    raise RuntimeError(
        "Super Snipper's panel process is not responding. An older running version may need "
        "to be closed before this update can open. See " + str(config.LOG_FILE))


def run_panel_service() -> None:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets, QtNetwork
    except ImportError as exc:  # loud failure, never a silent Tk fallback
        raise RuntimeError(
            "Super Snipper HUD requires PySide6. Install it with: "
            "pip install PySide6-Essentials"
        ) from exc

    mutex = winapi._acquire_control_panel_mutex()
    if mutex is None:
        logging.info("Another Super Snipper panel owns startup")
        return
    try:
        CaptureHud = hud_widget._load_hud_class(QtCore, QtGui, QtWidgets)
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        # Qt.Tool windows do not reliably trigger lastWindowClosed. Exit is an
        # explicit command; hiding or completing a capture never owns shutdown.
        app.setQuitOnLastWindowClosed(False)
        hud = CaptureHud()
        hud.monitor_existing_recording()
        server = QtNetwork.QLocalServer(app)
        server.setSocketOptions(QtNetwork.QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(_panel_server_name()):
            raise RuntimeError("Super Snipper command server failed: " + server.errorString())

        def accept():
            while server.hasPendingConnections():
                socket = server.nextPendingConnection()
                socket.setParent(server)
                socket.disconnected.connect(socket.deleteLater)
                timer = QtCore.QTimer(socket)
                timer.setSingleShot(True)
                timer.timeout.connect(socket.abort)
                timer.start(95000)

                def receive(sock=socket):
                    if not sock.canReadLine():
                        if sock.bytesAvailable() > 128:
                            sock.abort()
                        return
                    raw = bytes(sock.readLine(129))
                    def done(error, client=sock):
                        from shiboken6 import isValid
                        if not isValid(client) or client.state() == QtNetwork.QLocalSocket.LocalSocketState.UnconnectedState:
                            logging.warning("Super Snipper command client disconnected before acknowledgment")
                            return
                        client.write((json.dumps({"error": error, "pid": os.getpid()}) + "\n").encode())
                        client.disconnectFromServer()
                    try:
                        command = raw.decode("ascii").strip()
                        if command not in config.PANEL_COMMANDS:
                            raise ValueError("Invalid Super Snipper command")
                        hud.handle_command(command, done)
                    except Exception as exc:
                        logging.exception("Super Snipper command failed")
                        done(str(exc))
                socket.readyRead.connect(receive)
                receive()
        server.newConnection.connect(accept)
        app.exec()
        server.close()
    finally:
        winapi._close_handle(mutex)
