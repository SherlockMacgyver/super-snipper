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
