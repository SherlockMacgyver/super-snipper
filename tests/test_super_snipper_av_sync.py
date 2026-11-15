"""Audio/video start alignment: the saved MP4 must play sound at the moment it was heard.

The recorder starts loopback audio before FFmpeg's first frame, so the raw WAV
leads the video by however long FFmpeg took to start (about 0.3-0.4 s at 4K).
These tests use real FFmpeg on synthetic media plus the recorder's own wiring;
they capture no desktop and no audio device.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from tests.super_snipper_support import load

SR = 48000
FLASH_AT = 1.0
GDIGRAB_DUMP = (
    "[in#0/gdigrab @ 000002a6bf161740] Stream #0: not enough frames to estimate rate\r\n"
    "Input #0, gdigrab, from 'desktop':\r\n"
    "  Duration: N/A, start: {start}, bitrate: 7962636 kb/s\r\n"
    "  Stream #0:0: Video: bmp, bgra, 3840x2160, 7962636 kb/s, 30 fps\r\n"
)


@pytest.fixture
def snipper(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPER_SNIPPER_HOME", str(tmp_path / "home"))
    return load()


def _ffmpeg(snipper):
    try:
        return snipper.resolve_ffmpeg()
    except FileNotFoundError:
        pytest.skip("FFmpeg is not available")


def _flash_video(ffmpeg, path):
    """3 s black video with a white flash starting exactly at FLASH_AT."""
    subprocess.run([str(ffmpeg), "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x36:r=30:d=3",
                    "-vf", f"drawbox=c=white:t=fill:enable='between(t,{FLASH_AT},{FLASH_AT + 0.3})'",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _tone_wav(path, tone_at, length=4.0):
    samples = np.zeros(int(SR * length), dtype=np.float32)
    start = int(SR * tone_at)
    t = np.arange(int(SR * 0.15)) / SR
    samples[start:start + len(t)] = 0.5 * np.sin(2 * np.pi * 1000 * t)
    pcm = (np.stack([samples, samples], axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes(pcm.tobytes())


def _event_times(ffmpeg, mp4):
    frames = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(mp4), "-map", "0:v:0", "-vf", "scale=4:4,format=gray",
                             "-fps_mode", "passthrough", "-f", "rawvideo", "-"], check=True, capture_output=True).stdout
    luma = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 16).mean(axis=1)
    flash = int(np.argmax(luma > 128)) / 30.0
    pcm = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(mp4), "-map", "0:a:0", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], check=True, capture_output=True).stdout
    audio = np.abs(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768)
    tone = int(np.argmax(audio > 0.25)) / SR
    return flash, tone


@pytest.mark.parametrize("audio_offset", [-0.4, 0.25])
def test_mux_aligns_audio_that_started_before_or_after_video(snipper, tmp_path, audio_offset):
    # The sound and the flash happened at the same instant. In the raw WAV that
    # instant sits at FLASH_AT - audio_offset, because the WAV's first sample was
    # taken audio_offset seconds after (negative: before) the first frame.
    ffmpeg = _ffmpeg(snipper)
    video, audio, output = tmp_path / "video.mkv", tmp_path / "audio.wav", tmp_path / "out.mp4"
    _flash_video(ffmpeg, video)
    _tone_wav(audio, FLASH_AT - audio_offset)

    snipper._mux_recording(ffmpeg, video, audio, output, audio_offset)

    flash, tone = _event_times(ffmpeg, output)
    assert abs(flash - FLASH_AT) < 0.04
    assert abs(tone - flash) < 0.04, f"sound {1000 * (tone - flash):+.0f} ms from its picture"


def test_mux_refuses_unknown_or_implausible_offsets(snipper, tmp_path):
    video, audio = tmp_path / "video.mkv", tmp_path / "audio.wav"
    video.write_bytes(b"video")
    _tone_wav(audio, 0.5, length=1.0)
    with patch.object(snipper.subprocess, "run") as run:
        with pytest.raises(RuntimeError, match="offset is unknown"):
            snipper._mux_recording(Path("ffmpeg.exe"), video, audio, tmp_path / "a.mp4")
        with pytest.raises(RuntimeError, match="beyond"):
            snipper._mux_recording(Path("ffmpeg.exe"), video, audio, tmp_path / "b.mp4", 42.0)
    run.assert_not_called()
    assert video.exists() and audio.exists()


def test_video_start_time_is_read_from_the_recordings_own_ffmpeg_log(snipper):
    assert snipper._gdigrab_start_time(GDIGRAB_DUMP.format(start="1794775441.634824")) == 1794775441.634824
    with pytest.raises(RuntimeError, match="0 matches"):
        snipper._gdigrab_start_time("ffmpeg version 9.0.2\r\n")
    with pytest.raises(RuntimeError, match="2 matches"):
        snipper._gdigrab_start_time(GDIGRAB_DUMP.format(start="1.0") + GDIGRAB_DUMP.format(start="2.0"))


def test_record_video_passes_measured_start_offset_to_mux(snipper, tmp_path):
    """Wiring: audio start (recorder) minus video start (this run's FFmpeg log) reaches the mux."""
    log = tmp_path / "ffmpeg.log"
    log.write_bytes(GDIGRAB_DUMP.format(start="5.0").encode())  # an earlier recording's dump
    output = tmp_path / "capture.mp4"
    recorder = Mock(error=None, started_at=1000.0)

    def fake_audio(path):
        Path(path).write_bytes(b"RIFF" + b"\0" * 100)
        return recorder

    process = Mock(returncode=0)
    process.poll.return_value = 0

    def fake_ffmpeg(command, **kwargs):
        kwargs["stderr"].write(GDIGRAB_DUMP.format(start="1000.375").encode())
        Path(command[-1]).write_bytes(b"video")
        return process

    with patch.object(snipper, "set_dpi_awareness"), \
            patch.object(snipper, "_active_recording_state", return_value=None), \
            patch.object(snipper, "resolve_ffmpeg", return_value=Path("ffmpeg.exe")), \
            patch.object(snipper, "timestamped_video_path", return_value=output), \
            patch.object(snipper, "_recording_box", return_value=(0, 0, 100, 100)), \
            patch.object(snipper, "FFMPEG_LOG_FILE", log), \
            patch.object(snipper, "RECORDING_STATE_FILE", tmp_path / "recording.json"), \
            patch.object(snipper, "RECORDING_STOP_FILE", tmp_path / "recording.stop"), \
            patch.object(snipper, "LoopbackAudioRecorder", side_effect=fake_audio), \
            patch.object(snipper, "_mux_recording") as mux, \
            patch.object(snipper.subprocess, "Popen", side_effect=fake_ffmpeg):
        snipper.record_video(region=(0, 0, 100, 100), with_audio=True, countdown=0)

    audio_offset = mux.call_args.args[4]
    assert audio_offset == pytest.approx(-0.375)
    recorder.stop.assert_called_once()


class _FakeCapture:
    """Scripted WASAPI stand-in on the real clock: audio 'arrives' at SR frames per second.

    lose_at/lose: that many seconds of captured audio vanish (a real loss).
    stall_at/stall: one read blocks that long while audio keeps queueing (no loss).
    """

    def __init__(self, lose_at=None, lose=0.0, stall_at=None, stall=0.0):
        import time as _time
        self.time = _time
        self.lose_at, self.lose, self.stall_at, self.stall = lose_at, lose, stall_at, stall
        self.start = None
        self.delivered = 0
        self.lost = 0

    def __enter__(self):
        self.start = self.time.perf_counter()
        return self

    def __exit__(self, *exc):
        return False

    def _arrived(self):
        return int((self.time.perf_counter() - self.start) * SR)

    def record(self, numframes):
        now = self.time.perf_counter() - self.start
        if self.stall_at is not None and now >= self.stall_at:
            self.stall_at = None
            self.time.sleep(self.stall)
        if self.lose_at is not None and now >= self.lose_at:
            self.lose_at = None
            self.lost = int(self.lose * SR)
            self.delivered += self.lost
        while self._arrived() - self.delivered < numframes:
            self.time.sleep(0.002)
        self.delivered += numframes
        return np.full((numframes, 2), 0.5, dtype=np.float32)


def _run_recorder(snipper, tmp_path, capture, seconds=1.4):
    import sys
    import time
    import types
    fake = types.ModuleType("soundcard")
    speaker = types.SimpleNamespace(name="fake speaker")
    fake.default_speaker = lambda: speaker
    fake.get_microphone = lambda id, include_loopback: types.SimpleNamespace(recorder=lambda samplerate, channels: capture)
    path = tmp_path / "audio.wav"
    with patch.dict(sys.modules, {"soundcard": fake}):
        recorder = snipper.LoopbackAudioRecorder(path)
        recorder.start()
        time.sleep(seconds)
        recorder.stop()
    elapsed = time.perf_counter() - capture.start
    with wave.open(str(path), "rb") as wav:
        frames = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").reshape(-1, 2)
    return recorder, frames, elapsed


def test_lost_capture_audio_is_padded_back_onto_the_clock(snipper, tmp_path):
    capture = _FakeCapture(lose_at=0.5, lose=0.3)
    recorder, frames, elapsed = _run_recorder(snipper, tmp_path, capture)
    silent = int((frames[:, 0] == 0).sum())
    # Without padding the WAV would be 0.3 s short and later sound 0.3 s early.
    assert abs(len(frames) / SR - elapsed) < 0.08, (len(frames) / SR, elapsed)
    assert abs(silent / SR - 0.3) < 0.06
    assert recorder.padded_frames == silent


def test_buffered_audio_after_a_stall_is_not_padded(snipper, tmp_path):
    # A read blocks 0.3 s but nothing is lost: the queued samples must follow
    # unchanged. Padding here would push all later sound late.
    capture = _FakeCapture(stall_at=0.5, stall=0.3)
    recorder, frames, elapsed = _run_recorder(snipper, tmp_path, capture)
    assert recorder.padded_frames == 0
    assert int((frames[:, 0] == 0).sum()) == 0
    assert abs(len(frames) / SR - elapsed) < 0.08
