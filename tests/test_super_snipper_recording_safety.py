"""Recording-safety regression tests for super_snipper.py.

These exercise existing functions only. Every file the code touches is
redirected into tmp_path through monkeypatched module globals. No desktop
capture, audio, GUI or real recording happens. The only process spawned by the
record_video test is a tiny sleeping Python child owned by that test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest


ROOT = Path(__file__).resolve().parents[1]
from tests.super_snipper_support import load

# Captured before any test patches subprocess.Popen.
ORIGINAL_POPEN = subprocess.Popen

# Tiny stand-in for FFmpeg. Exits when it reads a 'q' line on stdin (like
# FFmpeg) or when terminated/killed. It ignores stdin EOF on purpose so it
# cannot exit early just because stdin was not a pipe. Hard upper bound 60s.
CHILD_CODE = (
    "import os, sys, threading, time\n"
    "def watch():\n"
    "    try:\n"
    "        while True:\n"
    "            line = sys.stdin.buffer.readline()\n"
    "            if not line:\n"
    "                return\n"
    "            if line.strip().lower() == b'q':\n"
    "                os._exit(0)\n"
    "    except Exception:\n"
    "        return\n"
    "threading.Thread(target=watch, daemon=True).start()\n"
    "time.sleep(60)\n"
)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, 3, 4, 5)


class InjectedFault(RuntimeError):
    pass


@pytest.fixture
def snipper(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPER_SNIPPER_HOME", str(tmp_path / "home"))
    module = load()

    monkeypatch.setattr(module, "VIDEO_DIR", tmp_path / "videos")
    monkeypatch.setattr(module, "SCREENSHOT_DIR", tmp_path / "shots")
    monkeypatch.setattr(module, "FFMPEG_LOG_FILE", tmp_path / "logs" / "ffmpeg.log")
    monkeypatch.setattr(module, "RECORDING_STATE_FILE", tmp_path / "logs" / "recording.json")
    monkeypatch.setattr(module, "RECORDING_STOP_FILE", tmp_path / "logs" / "recording.stop")
    monkeypatch.setattr(module, "_CANCEL_EVENT", None)
    return module


def _chain_contains(exc: BaseException, kind: type) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, kind):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


# ---------------------------------------------------------------------------
# 1. Unique same-second output names
# ---------------------------------------------------------------------------

PATH_FUNCS = [
    ("timestamped_video_path", ".mp4"),
    ("timestamped_path", ".jpg"),
]


@pytest.mark.parametrize("func_name,suffix", PATH_FUNCS)
def test_same_second_calls_return_distinct_paths(snipper, monkeypatch, func_name, suffix):
    monkeypatch.setattr(snipper.capture, "datetime", FrozenDatetime)
    func = getattr(snipper, func_name)

    paths = [func() for _ in range(5)]

    keys = [str(p).lower() for p in paths]
    assert len(set(keys)) == len(keys), f"same-second names collided: {paths}"
    for p in paths:
        assert p.suffix == suffix
    assert paths[1].stem == paths[0].stem + " (2)"
    assert paths[4].stem == paths[0].stem + " (5)"


@pytest.mark.parametrize("func_name,suffix", PATH_FUNCS)
def test_same_second_path_is_not_reused_after_a_file_is_written(
    snipper, monkeypatch, func_name, suffix
):
    monkeypatch.setattr(snipper.capture, "datetime", FrozenDatetime)
    func = getattr(snipper, func_name)
    sentinel = b"EXISTING USER CAPTURE\x00\x01"

    first = func()
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(sentinel)

    second = func()

    assert str(second).lower() != str(first).lower()
    assert first.read_bytes() == sentinel
    assert second.suffix == suffix
    # A fresh process has no in-memory counter; existing files still win.
    snipper._CAPTURE_NAME_COUNTS.clear()
    assert func() == second


# ---------------------------------------------------------------------------
# 2. Failed mux must not alter a pre-existing output
# ---------------------------------------------------------------------------

PRE_EXISTING = b"PRE-EXISTING OUTPUT BYTES\x00\xff" * 8


def test_failed_mux_with_real_ffmpeg_keeps_preexisting_output_byte_for_byte(snipper, tmp_path):
    try:
        ffmpeg = snipper.resolve_ffmpeg()
    except FileNotFoundError:
        pytest.skip("FFmpeg is not available")

    video = tmp_path / "in.video.mkv"
    output = tmp_path / "out.mp4"
    video_bytes = b"this is definitely not a media file"
    video.write_bytes(video_bytes)
    output.write_bytes(PRE_EXISTING)

    with pytest.raises(RuntimeError):
        snipper._mux_recording(ffmpeg, video, None, output)

    assert output.exists(), "failed mux deleted the pre-existing output"
    assert output.read_bytes() == PRE_EXISTING
    assert video.read_bytes() == video_bytes


def test_failed_mux_with_partial_write_keeps_preexisting_output_byte_for_byte(
    snipper, monkeypatch, tmp_path
):
    video = tmp_path / "in.video.mkv"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    output.write_bytes(PRE_EXISTING)

    def fake_run(command, **kwargs):
        # Like FFmpeg with -y: the last argument is the file being written.
        Path(command[-1]).write_bytes(b"PARTIAL")
        return SimpleNamespace(returncode=1, stdout="simulated mux failure")

    monkeypatch.setattr(snipper.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        snipper._mux_recording(Path("ffmpeg.exe"), video, None, output)

    assert output.exists(), "failed mux deleted the pre-existing output"
    assert output.read_bytes() == PRE_EXISTING
    assert video.read_bytes() == b"video"


# ---------------------------------------------------------------------------
# 3. Unrelated live PID must not count as an active recording
# ---------------------------------------------------------------------------

def test_active_recording_state_rejects_unrelated_live_pid(snipper, tmp_path):
    pid = os.getpid()  # the pytest process: alive, but not a recorder
    assert psutil.pid_exists(pid)
    state = {
        "pid": pid,
        "started_at": "2000-01-01T00:00:00",
        "output_path": str(tmp_path / "never.mp4"),
        "video_path": str(tmp_path / "never.video.mkv"),
        "audio_path": None,
    }
    snipper.RECORDING_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    snipper.RECORDING_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    assert snipper._active_recording_state() is None


# ---------------------------------------------------------------------------
# 4. stop_recording must not return a missing/empty output
# ---------------------------------------------------------------------------

def _install_vanishing_state(snipper, monkeypatch, output: Path):
    state = {"pid": os.getpid(), "output_path": str(output),
             "recording_id": "a" * 32, "pid_create_time": psutil.Process().create_time(),
             "ffmpeg": str(snipper.resolve_ffmpeg())}
    snipper.RECORDING_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Isolate completion handling from process discovery. The owner has exited
    # successfully, but the media still has to pass independent validation.
    observer = Mock()
    observer.poll.return_value = 0
    observer.returncode = 0
    monkeypatch.setattr(snipper, "ExistingRecordingProcess", lambda *a, **k: observer)
    calls = {"n": 0}

    def reader():
        calls["n"] += 1
        return state if calls["n"] == 1 else None

    monkeypatch.setattr(snipper, "_active_recording_state", reader)
    # The state file does not exist, so the recorder looks finished.
    assert not snipper.RECORDING_STATE_FILE.exists()


@pytest.mark.parametrize("kind", ["missing", "empty", "garbage"])
def test_stop_recording_raises_when_state_vanishes_without_usable_output(
    snipper, monkeypatch, tmp_path, kind
):
    output = tmp_path / "out.mp4"
    if kind == "empty":
        output.write_bytes(b"")
    elif kind == "garbage":
        output.write_bytes(b"not an MP4")
    _install_vanishing_state(snipper, monkeypatch, output)

    with pytest.raises(Exception):
        snipper.stop_recording(wait_seconds=1.0)


def test_stop_recording_returns_usable_output_when_state_vanishes(snipper, monkeypatch, tmp_path):
    output = tmp_path / "out.mp4"
    subprocess.run([str(snipper.resolve_ffmpeg()), "-v", "error", "-f", "lavfi",
                    "-i", "color=size=16x16:rate=5", "-t", "0.4", "-c:v", "libx264",
                    str(output)], check=True, **snipper.hidden_subprocess_kwargs())
    _install_vanishing_state(snipper, monkeypatch, output)

    assert Path(snipper.stop_recording(wait_seconds=1.0)) == output


# ---------------------------------------------------------------------------
# 5. record_video must stop AND wait for its child when the click fault fires
# ---------------------------------------------------------------------------

def test_record_video_stops_and_waits_for_spawned_child_after_armed_click_fault(
    snipper, monkeypatch, tmp_path
):
    spawned: list = []

    class OwnedChildPopen(ORIGINAL_POPEN):
        """Spawn a tiny sleeping Python child instead of FFmpeg."""

        def __init__(self, command, *args, **kwargs):
            self.wait_count = 0
            super().__init__([sys.executable, "-I", "-c", CHILD_CODE], *args, **kwargs)
            spawned.append(self)

        def wait(self, *args, **kwargs):
            self.wait_count += 1
            return super().wait(*args, **kwargs)

    def boom(*args, **kwargs):
        raise InjectedFault("injected armed click fault")

    monkeypatch.setattr(snipper, "set_dpi_awareness", lambda: None)
    monkeypatch.setattr(snipper, "virtual_screen", lambda: (0, 0, 200, 100))
    monkeypatch.setattr(snipper, "resolve_ffmpeg", lambda: tmp_path / "fake-ffmpeg.exe")
    monkeypatch.setattr(snipper, "_fire_armed_click_after_start", boom)
    monkeypatch.setattr(snipper.subprocess, "Popen", OwnedChildPopen)

    leaked = []
    raised = None
    statuses = []
    try:
        try:
            snipper.record_video(
                region=None,
                fps=5,
                with_audio=False,
                countdown=0,
                armed_click=(1, 1),
                click_delay_ms=0,
            )
        except Exception as exc:  # noqa: BLE001 - inspected below
            raised = exc
        statuses = [child.poll() for child in spawned]
        wait_counts = [child.wait_count for child in spawned]
    finally:
        # Clean up only the children this test spawned.
        for child in spawned:
            if child.poll() is None:
                leaked.append(child.pid)
                child.kill()
            try:
                child.wait(timeout=10)
            finally:
                if child.stdin:
                    try:
                        child.stdin.close()
                    except Exception:
                        pass

    assert raised is not None, "record_video swallowed the injected fault"
    assert _chain_contains(raised, InjectedFault), f"unexpected exception: {raised!r}"
    assert len(spawned) == 1, "expected exactly one spawned child"
    assert not leaked, f"record_video leaked its child process(es): {leaked}"
    assert all(status is not None for status in statuses)
    assert all(count > 0 for count in wait_counts)


def test_recording_identity_binds_creation_time_and_command(snipper, monkeypatch):
    state = snipper._recording_identity("b" * 32)
    snipper._write_json(snipper.RECORDING_STATE_FILE, state)
    assert snipper._active_recording_state()["recording_id"] == "b" * 32
    state["pid_create_time"] -= 1
    snipper._write_json(snipper.RECORDING_STATE_FILE, state)
    assert snipper._active_recording_state() is None
    state = snipper._recording_identity("b" * 32)
    state["process_cmdline"] = ["unrelated-process.exe"]
    snipper._write_json(snipper.RECORDING_STATE_FILE, state)
    assert snipper._active_recording_state() is None


def test_state_replace_failure_keeps_previous_complete_json(snipper, monkeypatch):
    path = snipper.RECORDING_STATE_FILE
    snipper._write_json(path, {"old": True})
    monkeypatch.setattr(snipper.os, "replace", Mock(side_effect=PermissionError("injected")))
    with pytest.raises(PermissionError):
        snipper._write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}
    assert list(path.parent.iterdir()) == [path]


def test_stale_stop_and_cleanup_cannot_touch_another_attempt(snipper):
    snipper._write_json(snipper.RECORDING_STATE_FILE, {"recording_id": "a" * 32})
    snipper._request_recording_stop({"recording_id": "a" * 32})
    assert not snipper._recording_stop_requested("b" * 32)
    snipper._clear_recording_files("b" * 32)
    assert snipper.RECORDING_STATE_FILE.exists()
    assert snipper._recording_stop_path("a" * 32).exists()
    snipper._request_recording_stop({"recording_id": "b" * 32})
    # Even a delayed old request cannot overwrite the new owner's stop request.
    snipper._request_recording_stop({"recording_id": "a" * 32})
    assert snipper._recording_stop_requested("b" * 32)


def test_repeated_stop_does_not_replace_the_existing_request(snipper, monkeypatch):
    state = {"recording_id": "a" * 32}
    snipper._request_recording_stop(state)
    monkeypatch.setattr(snipper, "_write_json", Mock(side_effect=AssertionError("stop rewritten")))
    snipper._request_recording_stop(state)
    assert snipper._recording_stop_requested(state["recording_id"])


def test_destination_created_during_mux_is_never_replaced(snipper, monkeypatch, tmp_path):
    output = tmp_path / "out.mp4"
    video = tmp_path / "video.mkv"
    video.write_bytes(b"raw")
    def competing_publication(command, **kwargs):
        Path(command[-1]).write_bytes(b"owned staging bytes")
        output.write_bytes(PRE_EXISTING)
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(snipper.subprocess, "run", competing_publication)
    monkeypatch.setattr(snipper, "_validate_recording_output", lambda *args: None)
    with pytest.raises(FileExistsError):
        snipper._mux_recording(Path("ffmpeg.exe"), video, None, output)
    assert output.read_bytes() == PRE_EXISTING
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["out.mp4", "video.mkv"]


def test_locked_raw_file_after_publish_is_reported_without_losing_output(snipper, monkeypatch, tmp_path, caplog):
    work = tmp_path / "work"
    work.mkdir()
    raw = work / "video.mkv"
    raw.write_bytes(b"raw")
    output = tmp_path / "out.mp4"
    output.write_bytes(b"already published")
    unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == raw:
            raise PermissionError("simulated external file lock")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", locked)
    snipper._cleanup_published_recording(work, [raw])
    assert output.read_bytes() == b"already published"
    assert raw.exists()
    assert "cleanup incomplete" in caplog.text


def test_stop_cannot_claim_success_after_owner_handle_is_unavailable(snipper, monkeypatch, tmp_path):
    _install_vanishing_state(snipper, monkeypatch, tmp_path / "out.mp4")
    monkeypatch.setattr(snipper, "ExistingRecordingProcess", Mock(side_effect=OSError("gone")))
    with pytest.raises(RuntimeError, match="completion was not confirmed"):
        snipper.stop_recording()


WORKER = '''
import sys,traceback
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tests.super_snipper_support import load
s=load()
base=Path(sys.argv[2]);base.mkdir(exist_ok=True)
s.RECORDING_STATE_FILE=base/"state.json";s.RECORDING_STOP_FILE=base/"stop.json"
s.FFMPEG_LOG_FILE=base/"ffmpeg.log";s.LOG_FILE=base/"worker.log";s.VIDEO_DIR=base/"videos"
original=s._ffmpeg_video_command
def synthetic_input(ffmpeg,path,box,fps,**kwargs):
    return [str(ffmpeg),"-y","-hide_banner","-progress","pipe:2","-nostats",
            "-re","-f","lavfi","-i","color=size=32x32:rate=5","-t","20",
            "-c:v","libx264",str(path)]
s._ffmpeg_video_command=synthetic_input
if sys.argv[3]=="fail":
    def fail(*args):raise RuntimeError("injected finalization failure")
    s._mux_recording=fail
if sys.argv[3]=="cleanup":
    original_rmdir=Path.rmdir
    def locked_rmdir(path):
        if path.name.startswith(".supersnipper-"):
            raise PermissionError("injected directory cleanup lock")
        return original_rmdir(path)
    Path.rmdir=locked_rmdir
sys.argv=[sys.argv[1],"--record-box","0,0,32,32","--no-audio","--countdown","0","--duration","20"]
try:raise SystemExit(s.main())
except Exception:
    traceback.print_exc();raise SystemExit(1)
'''


@pytest.mark.parametrize("mode", ["ok", "fail", "cleanup"])
def test_real_cli_worker_lock_and_stop_outcome(snipper, tmp_path, mode):
    """Real CLI/encoder/process handle; only desktop input is a synthetic source."""
    base = tmp_path / "worker"
    base.mkdir()
    snipper.RECORDING_STATE_FILE = base / "state.json"
    snipper.RECORDING_STOP_FILE = base / "stop.json"
    out = (base / "stdout").open("wb")
    err = (base / "stderr").open("wb")
    worker = ORIGINAL_POPEN([sys.executable, "-c", WORKER, str(ROOT), str(base),
                             mode], stdout=out, stderr=err,
                            **snipper.hidden_subprocess_kwargs())
    try:
        deadline = time.monotonic() + 20
        while True:
            assert worker.poll() is None, (base / "stderr").read_text()
            state = snipper._active_recording_state()
            progress = (base / "ffmpeg.log").read_text(errors="replace") if (base / "ffmpeg.log").exists() else ""
            if state and "frame=1" in progress:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        # Admission failure must leave the first owner's state byte-for-byte intact.
        before = snipper.RECORDING_STATE_FILE.read_bytes()
        competitor = subprocess.run([sys.executable, "-c", WORKER, str(ROOT), str(base), "ok"],
                                    capture_output=True, timeout=10, **snipper.hidden_subprocess_kwargs())
        assert competitor.returncode != 0
        assert b"Another Super Snipper capture" in competitor.stderr
        assert snipper.RECORDING_STATE_FILE.read_bytes() == before
        if mode == "fail":
            with pytest.raises(RuntimeError, match="Recording failed with exit code"):
                snipper.stop_recording(wait_seconds=20)
            assert list((base / "videos").glob(".supersnipper-*/video.mkv"))
        else:
            result = snipper.stop_recording(wait_seconds=20)
            assert result.is_file() and result.stat().st_size > 0
            leftovers = list((base / "videos").glob(".supersnipper-*"))
            assert bool(leftovers) == (mode == "cleanup")
            if mode == "cleanup":
                assert all(not list(path.iterdir()) for path in leftovers)
        worker.wait(timeout=5)
        assert worker.returncode == (1 if mode == "fail" else 0)
        assert not snipper.RECORDING_STATE_FILE.exists()
    finally:
        # Only the process this test launched; production identities are never queried.
        if worker.poll() is None:
            state = snipper._active_recording_state()
            if state:
                snipper._request_recording_stop(state)
            try:
                worker.wait(timeout=25)
            except subprocess.TimeoutExpired:
                worker.kill();worker.wait(timeout=5)
        out.close();err.close()
