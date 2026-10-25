"""Video recording: FFmpeg capture, loopback audio, identity-checked state and safe publication."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np
import psutil

from . import capture, config, overlays, winapi


def resolve_ffmpeg() -> Path:
    """Bundled codec when frozen; source uses explicit environment or PATH."""
    if config.FROZEN:
        bundled = config.APP_DIR / "bin" / "ffmpeg.exe"
        if not bundled.is_file():
            raise FileNotFoundError(f"Bundled FFmpeg is missing: {bundled}. Reinstall Super Snipper.")
        return bundled
    env_path = os.environ.get("SUPER_SNIPPER_FFMPEG")
    if env_path:
        path = Path(env_path)
        if not path.is_file():
            raise FileNotFoundError(f"SUPER_SNIPPER_FFMPEG does not point to a file: {path}")
        return path
    path_hit = shutil.which("ffmpeg")
    if path_hit:
        return Path(path_hit)
    raise FileNotFoundError(
        "FFmpeg is required for Super Snipper video recording. Install FFmpeg on PATH "
        "or set SUPER_SNIPPER_FFMPEG to the full path of ffmpeg.exe."
    )


def timestamped_video_path(prefix: str = "SuperSnip Recording") -> Path:
    return capture._next_capture_path(config.VIDEO_DIR, prefix, ".mp4")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    owned = False
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            owned = True
            json.dump(payload, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if owned:
            _delete_if_exists(temporary)


@contextmanager
def _recording_lock():
    """Serialize admission through final cleanup, including CLI countdowns.

    A stable byte-range lock coordinates processes and Windows sessions sharing
    this state directory. The lock file is never unlinked or used as status.
    """
    import msvcrt

    path = config.RECORDING_STATE_FILE.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Another Super Snipper capture is starting, recording or finalizing") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _recording_identity(recording_id: str) -> dict[str, Any]:
    owner = psutil.Process(os.getpid())
    return {"state_version": 2, "recording_id": recording_id, "pid": owner.pid,
            "pid_create_time": owner.create_time(), "process_cmdline": owner.cmdline()}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _delete_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _active_recording_state() -> Optional[dict[str, Any]]:
    if not config.RECORDING_STATE_FILE.exists():
        return None
    try:
        state = _read_json(config.RECORDING_STATE_FILE)
    except Exception:
        logging.exception("Recording state is unreadable: %s", config.RECORDING_STATE_FILE)
        raise

    try:
        owner = psutil.Process(int(state.get("pid", 0)))
        command = owner.cmdline()
        if state.get("state_version") != 2:
            # An actual pre-upgrade CLI worker has no creation identity. Do not
            # adopt it, or start a second capture alongside it.
            legacy_script = any(arg.lower().endswith("super_snipper.py") for arg in command)
            if legacy_script and any(arg.startswith(("--record-", "--toggle-record-")) for arg in command):
                raise RuntimeError("An older recorder is active; stop it with its original panel before restarting Super Snipper")
            return None
        recording_id = state.get("recording_id", "")
        if (not isinstance(recording_id, str) or len(recording_id) != 32
                or any(char not in "0123456789abcdef" for char in recording_id)):
            raise RuntimeError("Invalid Super Snipper recording identity")
        if (owner.create_time() != state.get("pid_create_time")
                or command != state.get("process_cmdline")):
            logging.warning("Ignoring stale recording identity for pid %s", owner.pid)
            return None
        return state
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def _recording_stop_path(recording_id: str) -> Path:
    if len(recording_id) != 32 or any(char not in "0123456789abcdef" for char in recording_id):
        raise ValueError("Invalid recording stop identity")
    return config.RECORDING_STOP_FILE.with_name(config.RECORDING_STOP_FILE.name + "." + recording_id)


def _request_recording_stop(state: dict[str, Any]) -> None:
    """Target this attempt; a delayed Stop cannot stop the next recording."""
    path = _recording_stop_path(state["recording_id"])
    if not path.exists():
        _write_json(path, {"recording_id": state["recording_id"]})


def _recording_stop_requested(recording_id: str) -> bool:
    path = _recording_stop_path(recording_id)
    if not path.exists():
        return False
    return _read_json(path).get("recording_id") == recording_id


def _clear_recording_files(recording_id: str) -> None:
    for path in (config.RECORDING_STATE_FILE, _recording_stop_path(recording_id)):
        try:
            if path.exists() and _read_json(path).get("recording_id") == recording_id:
                path.unlink()
        except (OSError, ValueError):
            # Never delete an unidentifiable file or turn an already-published
            # valid video into a failed recording because housekeeping failed.
            logging.exception("Recording metadata cleanup incomplete; preserved %s", path)


def _send_ffmpeg_quit(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if process.stdin:
        try:
            process.stdin.write(b"q\n")
            process.stdin.flush()
            return
        except Exception:
            logging.exception("Failed to send q to FFmpeg")
    process.terminate()


class LoopbackAudioRecorder:
    def __init__(
        self,
        path: Path,
        samplerate: int = config.DEFAULT_AUDIO_RATE,
        block_frames: int = config.DEFAULT_AUDIO_BLOCK,
    ) -> None:
        self.path = path
        self.samplerate = samplerate
        self.block_frames = block_frames
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.error: Optional[BaseException] = None
        self.frames_written = 0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._record, name="SuperSnipperAudio", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("Loopback audio did not stop; raw media has been preserved")
        if self.error:
            raise RuntimeError("Loopback audio recording failed") from self.error

    def _record(self) -> None:
        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            if not speaker:
                raise RuntimeError("No default speaker available for loopback audio")

            microphone = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            logging.info("Recording loopback audio from %s to %s", speaker.name, self.path)

            with wave.open(str(self.path), "wb") as wav_file:
                wav_file.setnchannels(2)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.samplerate)

                with microphone.recorder(samplerate=self.samplerate, channels=2) as recorder:
                    while not self.stop_event.is_set():
                        data = recorder.record(numframes=self.block_frames)
                        if data is None or data.size == 0:
                            continue
                        pcm = np.clip(data, -1.0, 1.0)
                        pcm = (pcm * 32767).astype("<i2", copy=False)
                        wav_file.writeframes(pcm.tobytes())
                        self.frames_written += int(pcm.shape[0])
        except BaseException as exc:
            self.error = exc
            logging.exception("Loopback audio recorder failed")


def _record_command_args(
    mode: str,
    fps: int = config.DEFAULT_VIDEO_FPS,
    countdown: int = config.DEFAULT_RECORD_COUNTDOWN_SECONDS,
    with_audio: bool = True,
    arm_click: bool = False,
    click_delay_ms: int = config.DEFAULT_ARMED_CLICK_DELAY_MS,
    show_cursor: bool = False,
) -> list[str]:
    if mode == "region":
        args = ["--toggle-record-region"]
    elif mode == "fullscreen":
        args = ["--toggle-record-fullscreen"]
    else:
        raise ValueError(f"Unknown recording mode: {mode}")

    args.extend(["--fps", str(int(fps)), "--countdown", str(max(0, int(countdown)))])
    if not with_audio:
        args.append("--no-audio")
    if show_cursor:
        args.append("--show-cursor")
    if arm_click:
        args.extend(["--arm-click", "--click-delay-ms", str(max(0, int(click_delay_ms)))])
    return args


def _fire_armed_click_after_start(
    process: subprocess.Popen,
    armed_click: Optional[Tuple[int, int]],
    click_delay_ms: int,
) -> None:
    if not armed_click:
        return
    delay = max(0, int(click_delay_ms)) / 1000.0
    if delay:
        time.sleep(delay)
    if process.poll() is not None or winapi._capture_cancelled():
        logging.warning("Skipping armed click because FFmpeg exited before the click fired")
        return
    logging.info("Firing armed click at %s,%s", armed_click[0], armed_click[1])
    winapi._click_screen_point(armed_click)


def _recording_box(region: Optional[Tuple[int, int, int, int]]) -> Tuple[int, int, int, int]:
    vx, vy, width, height = winapi.virtual_screen()
    if not region:
        return vx, vy, max(2, width - (width % 2)), max(2, height - (height % 2))

    left, top, right, bottom = region
    capture_width = max(2, right - left)
    capture_height = max(2, bottom - top)
    capture_width -= capture_width % 2
    capture_height -= capture_height % 2
    return vx + left, vy + top, capture_width, capture_height


def _ffmpeg_video_command(
    ffmpeg: Path,
    video_path: Path,
    box: Tuple[int, int, int, int],
    fps: int,
    show_cursor: bool = False,
) -> list[str]:
    x, y, width, height = box
    return [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-f",
        "gdigrab",
        "-draw_mouse",
        "1" if show_cursor else "0",
        "-framerate",
        str(fps),
        "-offset_x",
        str(x),
        "-offset_y",
        str(y),
        "-video_size",
        f"{width}x{height}",
        "-i",
        "desktop",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]


def _validate_recording_output(ffmpeg: Path, output_path: Path) -> None:
    """Require a nonempty MP4 with a decodable video frame, not just a filename."""
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Recording finished without a usable output: {output_path}")
    result = subprocess.run(
        [str(ffmpeg), "-v", "error", "-xerror", "-progress", "pipe:1", "-nostats",
         "-f", "mov", "-i", str(output_path),
         "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
        text=True, encoding="utf-8", errors="replace",
        **winapi.hidden_subprocess_kwargs(new_process_group=False))
    decoded = any(line.startswith("frame=") and int(line.partition("=")[2].strip()) > 0
                  for line in result.stdout.splitlines())
    if result.returncode != 0 or not decoded:
        raise RuntimeError(f"Recording output is not a valid MP4: {output_path}\n{result.stdout}")


def _mux_recording(ffmpeg: Path, video_path: Path, audio_path: Optional[Path], output_path: Path) -> None:
    if output_path.exists():
        raise RuntimeError(f"Refusing to replace existing output {output_path}; recoverable source media preserved")
    # FFmpeg writes only into an exclusively created, attempt-owned staging
    # file. The public destination is never its overwrite/delete target.
    temporary = output_path.with_name("." + output_path.stem + "." + uuid.uuid4().hex + ".mp4")
    command = [str(ffmpeg), "-y", "-hide_banner", "-i", str(video_path)]
    if audio_path and audio_path.exists() and audio_path.stat().st_size > 44:
        command.extend(["-i", str(audio_path), "-c:v", "copy", "-c:a", "aac", "-shortest"])
    else:
        command.extend(["-c:v", "copy"])
    command.append(str(temporary))

    logging.info("Muxing recording to %s", output_path)
    with temporary.open("xb"):
        pass
    try:
        result = subprocess.run(
            command, cwd=str(config.APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            **winapi.hidden_subprocess_kwargs(new_process_group=False))
        if result.returncode != 0:
            logging.error("FFmpeg mux failed:\n%s", result.stdout)
            raise RuntimeError(f"FFmpeg mux failed; recoverable source media preserved at {video_path.parent}")
        _validate_recording_output(ffmpeg, temporary)
        # On Windows rename is atomic and refuses an existing destination,
        # including a file created after the initial exists() check.
        if os.name != "nt":
            raise RuntimeError("Super Snipper requires Windows for safe output publication")
        temporary.rename(output_path)
    finally:
        _delete_if_exists(temporary)


def _finish_recording_process(process: subprocess.Popen) -> None:
    """Reap only our Popen child, escalating after bounded graceful shutdown."""
    if process.poll() is None:
        try:
            _send_ffmpeg_quit(process)
        except OSError:
            logging.exception("Graceful recorder stop failed; verifying child exit")
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdin:
        process.stdin.close()


def _cleanup_published_recording(work_dir: Path, paths: list[Path]) -> None:
    """Publication is complete; report locked raw leftovers without losing it."""
    for path in paths:
        try:
            path.unlink()
        except OSError:
            logging.exception("Video saved, but raw-media cleanup incomplete: %s", path)
    try:
        work_dir.rmdir()
    except OSError:
        logging.exception("Video saved; recovery directory retained: %s", work_dir)


def record_video(
    region: Optional[Tuple[int, int, int, int]],
    fps: int = config.DEFAULT_VIDEO_FPS,
    with_audio: bool = True,
    duration: Optional[float] = None,
    countdown: int = config.DEFAULT_RECORD_COUNTDOWN_SECONDS,
    armed_click: Optional[Tuple[int, int]] = None,
    click_delay_ms: int = config.DEFAULT_ARMED_CLICK_DELAY_MS,
    show_cursor: bool = False,
) -> Path:
    winapi.set_dpi_awareness()
    winapi._check_capture_cancelled()
    with _recording_lock():
        if _active_recording_state():
            raise RuntimeError("A Super Snipper recording is already active")
        ffmpeg = resolve_ffmpeg()
        output_path = timestamped_video_path()
        box = _recording_box(region)
        if countdown > 0 and not overlays.show_recording_countdown(countdown, armed_click=bool(armed_click)):
            raise config.RecordingCancelled("Super Snipper recording cancelled during countdown")
        winapi._check_capture_cancelled()

        recording_id = uuid.uuid4().hex
        # Exclusive directory ownership protects raw video/audio too, even if a
        # destination name was forced to collide by an external caller.
        work_dir = Path(tempfile.mkdtemp(prefix=".supersnipper-", dir=output_path.parent))
        video_path = work_dir / "video.mkv"
        audio_path = work_dir / "audio.wav" if with_audio else None
        process = None
        audio_recorder = None
        audio_stopped = False
        state_written = False
        published = False
        try:
            if with_audio:
                audio_recorder = LoopbackAudioRecorder(audio_path)
                audio_recorder.start()
                time.sleep(0.25)
                if audio_recorder.error:
                    raise RuntimeError(
                        "Computer-audio loopback failed. Install/repair soundcard or use --no-audio."
                    ) from audio_recorder.error
            state = {
                **_recording_identity(recording_id),
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "output_path": str(output_path), "video_path": str(video_path),
                "audio_path": str(audio_path) if audio_path else None,
                "stop_file": str(_recording_stop_path(recording_id)), "ffmpeg": str(ffmpeg),
                "box": {"x": box[0], "y": box[1], "width": box[2], "height": box[3]},
                "fps": fps, "with_audio": with_audio, "show_cursor": show_cursor,
                "armed_click": {"x": armed_click[0], "y": armed_click[1]} if armed_click else None,
                "click_delay_ms": click_delay_ms,
            }
            _write_json(config.RECORDING_STATE_FILE, state)
            state_written = True
            logging.info("Starting video recording: %s", state)
            command = _ffmpeg_video_command(ffmpeg, video_path, box, fps, show_cursor=show_cursor)
            config.FFMPEG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Keep the log open until the child is reaped, including exceptions.
            with config.FFMPEG_LOG_FILE.open("ab") as ffmpeg_log:
                try:
                    process = subprocess.Popen(
                        command, cwd=str(config.APP_DIR), stdin=subprocess.PIPE,
                        stdout=ffmpeg_log, stderr=ffmpeg_log,
                        **winapi.hidden_subprocess_kwargs(new_process_group=False))
                    _fire_armed_click_after_start(process, armed_click, click_delay_ms)
                    start = time.monotonic()
                    while process.poll() is None:
                        if _recording_stop_requested(recording_id) or winapi._capture_cancelled():
                            break
                        if duration and time.monotonic() - start >= duration:
                            break
                        time.sleep(0.25)
                finally:
                    if process is not None:
                        _finish_recording_process(process)
            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg recording failed with exit code {process.returncode}")
            if audio_recorder:
                audio_recorder.stop()
                audio_stopped = True
            if not video_path.exists() or video_path.stat().st_size == 0:
                raise RuntimeError("FFmpeg did not create a usable video file")
            _mux_recording(ffmpeg, video_path, audio_path, output_path)
            published = True
            _cleanup_published_recording(work_dir, [video_path] + ([audio_path] if audio_path else []))
            logging.info("Saved video recording: %s", output_path)
            print(output_path)
            return output_path
        finally:
            # If reaping fails, leave the state intact and propagate the error;
            # never advertise a released capture while its child is still live.
            if process is not None:
                _finish_recording_process(process)
            try:
                if audio_recorder and not audio_stopped:
                    audio_recorder.stop()
            finally:
                if state_written:
                    _clear_recording_files(recording_id)
                if not published and work_dir.exists() and not any(work_dir.iterdir()):
                    work_dir.rmdir()


def stop_recording(wait_seconds: float = 90.0) -> Optional[Path]:
    state = _active_recording_state()
    if not state:
        print("No active Super Snipper recording.")
        return None
    # Hold the OS handle before requesting Stop. A vanished JSON file is not a
    # result, and a later recorder cannot replace the identity behind this handle.
    try:
        observed = winapi.ExistingRecordingProcess(int(state["pid"]), state["pid_create_time"])
    except (OSError, psutil.NoSuchProcess) as exc:
        raise RuntimeError(
            "Recorder ended before Stop could attach; completion was not confirmed. "
            f"Check the capture log and output: {state['output_path']}"
        ) from exc
    try:
        _request_recording_stop(state)
        deadline = time.monotonic() + wait_seconds
        while observed.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for recording finalization; it may still be saving")
            time.sleep(0.1)
        if observed.returncode != 0:
            raise RuntimeError(f"Recording failed with exit code {observed.returncode}; raw media was preserved")
        output_path = Path(str(state["output_path"]))
        _validate_recording_output(Path(str(state["ffmpeg"])), output_path)
        print(output_path)
        return output_path
    finally:
        observed.close()


def toggle_recording(
    region_mode: bool,
    fps: int = config.DEFAULT_VIDEO_FPS,
    with_audio: bool = True,
    countdown: int = config.DEFAULT_RECORD_COUNTDOWN_SECONDS,
    arm_click: bool = False,
    click_delay_ms: int = config.DEFAULT_ARMED_CLICK_DELAY_MS,
    show_cursor: bool = False,
) -> int:
    if _active_recording_state():
        stop_recording()
        return 0

    armed_click = overlays.select_click_target() if arm_click else None
    region = overlays.select_video_region() if region_mode else None
    if region_mode and not region:
        logging.info("Video recording cancelled before start")
        return 0

    record_video(
        region=region,
        fps=fps,
        with_audio=with_audio,
        countdown=countdown,
        armed_click=armed_click,
        click_delay_ms=click_delay_ms,
        show_cursor=show_cursor,
    )
    return 0


def _record_video_cli(
    region: Optional[Tuple[int, int, int, int]],
    fps: int,
    with_audio: bool,
    duration: Optional[float],
    countdown: int,
    armed_click: Optional[Tuple[int, int]],
    click_delay_ms: int,
    show_cursor: bool = False,
) -> int:
    try:
        record_video(
            region=region,
            fps=fps,
            with_audio=with_audio,
            duration=duration,
            countdown=countdown,
            armed_click=armed_click,
            click_delay_ms=click_delay_ms,
            show_cursor=show_cursor,
        )
    except config.RecordingCancelled:
        return 0
    return 0
