"""Command-line parsing and the launcher entry point (run) for every Super Snipper action."""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import subprocess
import sys
from pathlib import Path

from PIL import ImageGrab

from . import capture, config, hud_widget, overlays, panel, recording, winapi


def self_test() -> int:
    winapi.set_dpi_awareness()
    x, y, width, height = winapi.virtual_screen()
    screenshot = ImageGrab.grab(all_screens=True)
    try:
        ffmpeg = recording.resolve_ffmpeg()
    except FileNotFoundError as exc:
        ffmpeg = None
        print(f"ffmpeg_error={exc}")
    print(f"dpi_aware=ok")
    print(f"virtual_screen={x},{y},{width},{height}")
    print(f"grab_size={screenshot.width}x{screenshot.height}")
    print(f"screenshot_dir={config.SCREENSHOT_DIR}")
    print(f"video_dir={config.VIDEO_DIR}")
    print(f"ffmpeg={ffmpeg}")
    print(f"log_file={config.LOG_FILE}")
    return 0


def _report_snip(output, path) -> None:
    """Tell a calling window which file a snip saved; a cancelled snip writes nothing."""
    if output and path:
        Path(output).write_text(str(path), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="DPI-aware region snipper")
    parser.add_argument("--snip", action="store_true", help="open the region snipper")
    parser.add_argument("--tray", action="store_true", help="start bundled tray/hotkeys and show the panel")
    parser.add_argument("--record-region", action="store_true", help="select a region and record MP4 video")
    parser.add_argument("--record-fullscreen", action="store_true", help="record the full virtual desktop")
    parser.add_argument("--record-box", help="record left,top,right,bottom physical pixels")
    parser.add_argument("--toggle-record-region", action="store_true", help="start/stop region video recording")
    parser.add_argument("--toggle-record-fullscreen", action="store_true", help="start/stop full-screen video recording")
    parser.add_argument("--control-panel", action="store_true", help="open the Super Snipper capture HUD")
    parser.add_argument("--panel-command", choices=config.PANEL_COMMANDS, help="control the single capture panel")
    parser.add_argument("--panel-service", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cancel-event", help=argparse.SUPPRESS)
    parser.add_argument("--render-hud", help="render the HUD to a PNG for review and exit")
    parser.add_argument("--hud-orientation", choices=("horizontal", "vertical"), default="horizontal", help="orientation for --render-hud")
    parser.add_argument("--hud-settings", action="store_true", help="render --render-hud with the settings row expanded")
    parser.add_argument("--stop-recording", action="store_true", help="stop the active video recording")
    parser.add_argument("--self-test", action="store_true", help="print capture diagnostics")
    parser.add_argument("--diagnostics", metavar="FILE", help="write runtime/dependency diagnostics as JSON")
    parser.add_argument("--storyboard", nargs="?", const="", metavar="VIDEO", help="open video storyboard window, or export VIDEO with the options below")
    parser.add_argument("--sample-rate", type=float, default=6, help="storyboard samples per --sample-unit (default 6 per minute)")
    parser.add_argument("--sample-unit", choices=("second", "minute"), default="minute")
    parser.add_argument("--sheet-edge", type=int, default=2048, help="maximum storyboard edge in pixels (64-4096)")
    parser.add_argument("--series", action="store_true", help="open the snip storyboard window: capture, mark up and stitch snips in order")
    parser.add_argument("--snip-output", metavar="FILE", help="after --snip or --capture-box saves, write the saved path to FILE")
    parser.add_argument("--capture-box", help="capture left,top,right,bottom physical pixels")
    parser.add_argument("--no-clipboard", action="store_true", help="skip clipboard copy")
    parser.add_argument("--no-audio", action="store_true", help="record video without computer audio")
    parser.add_argument("--show-cursor", action="store_true", help="draw the mouse pointer in recorded video")
    parser.add_argument("--fps", type=int, default=config.DEFAULT_VIDEO_FPS, help="video recording frame rate")
    parser.add_argument("--duration", type=float, help="optional recording duration in seconds")
    parser.add_argument("--countdown", type=int, default=config.DEFAULT_RECORD_COUNTDOWN_SECONDS, help="seconds to count down before recording starts")
    parser.add_argument("--arm-click", action="store_true", help="pick a screen point to click just after recording starts")
    parser.add_argument(
        "--click-delay-ms",
        type=int,
        default=config.DEFAULT_ARMED_CLICK_DELAY_MS,
        help="milliseconds to wait after recording starts before firing --arm-click",
    )
    args = parser.parse_args()

    config.setup_logging()
    if args.diagnostics:
        import psutil
        import soundcard
        from PySide6 import QtCore
        ffmpeg = recording.resolve_ffmpeg()
        payload = {"frozen": config.FROZEN, "executable": sys.executable,
                   "app_dir": str(config.APP_DIR), "ffmpeg": str(ffmpeg),
                   "qt": QtCore.qVersion(), "python": sys.version,
                   "child_command": config.launch_command(["--self-test"]),
                   "loaded_files": sorted({entry.path for entry in psutil.Process().memory_maps()}),
                   "audio_backend": soundcard.__name__}
        Path(args.diagnostics).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 0
    if args.tray:
        if not config.FROZEN:
            raise RuntimeError("--tray requires the packaged app; source users run the AutoHotkey script.")
        interpreter = config.APP_DIR / "bin" / "AutoHotkey64.exe"
        script = config.APP_DIR / "super_snipper_hotkey.ahk"
        if not interpreter.is_file() or not script.is_file():
            raise FileNotFoundError("Bundled hotkey support is missing. Reinstall Super Snipper.")
        subprocess.Popen([str(interpreter), str(script)], cwd=config.APP_DIR,
                         **winapi.hidden_subprocess_kwargs(new_process_group=False))
        panel.show_control_panel("show")
        return 0
    if args.storyboard is not None:
        if not args.storyboard:
            from .storyboard_ui import run_storyboard_window
            return run_storyboard_window()
        from .storyboard import inspect_video, make_plan, export_sheet
        info = inspect_video(Path(args.storyboard))
        plan = make_plan(info, args.sample_rate / (60 if args.sample_unit == "minute" else 1), args.sheet_edge)
        output = export_sheet(plan)
        print(f"storyboard={output}")
        print(f"frames={plan.grid.count} grid={plan.grid.columns}x{plan.grid.rows} rate_per_second={plan.rate:.9g}")
        return 0
    if args.series:
        from .series_ui import run_series_window
        return run_series_window()
    if args.cancel_event:
        winapi._CANCEL_EVENT = winapi._capture_event(args.cancel_event)
        winapi._check_capture_cancelled()

    if args.self_test:
        return self_test()
    if args.render_hud:
        out = hud_widget.render_hud_preview(
            Path(args.render_hud),
            orientation=args.hud_orientation,
            settings_open=args.hud_settings,
        )
        print(f"hud_preview={out}")
        return 0
    if args.panel_service:
        panel.run_panel_service()
        return 0
    if args.control_panel or args.panel_command:
        panel.show_control_panel(args.panel_command or "show")
        return 0
    if args.capture_box:
        _report_snip(args.snip_output, capture.capture_box(
            args.capture_box, copy=not args.no_clipboard, prefix="SuperSnip Test"))
        return 0
    if args.stop_recording:
        recording.stop_recording()
        return 0
    if args.toggle_record_region:
        try:
            return recording.toggle_recording(
                region_mode=True,
                fps=args.fps,
                with_audio=not args.no_audio,
                countdown=args.countdown,
                arm_click=args.arm_click,
                click_delay_ms=args.click_delay_ms,
                show_cursor=args.show_cursor,
            )
        except config.RecordingCancelled:
            return 0
    if args.toggle_record_fullscreen:
        try:
            return recording.toggle_recording(
                region_mode=False,
                fps=args.fps,
                with_audio=not args.no_audio,
                countdown=args.countdown,
                arm_click=args.arm_click,
                click_delay_ms=args.click_delay_ms,
                show_cursor=args.show_cursor,
            )
        except config.RecordingCancelled:
            return 0
    if args.record_region:
        armed_click = overlays.select_click_target() if args.arm_click else None
        region = overlays.select_video_region()
        if not region:
            logging.info("Video recording cancelled before start")
            return 0
        return recording._record_video_cli(
            region,
            args.fps,
            not args.no_audio,
            args.duration,
            args.countdown,
            armed_click,
            args.click_delay_ms,
            args.show_cursor,
        )
    if args.record_box:
        region = capture.parse_box(args.record_box, "--record-box")
        armed_click = overlays.select_click_target() if args.arm_click else None
        return recording._record_video_cli(
            region,
            args.fps,
            not args.no_audio,
            args.duration,
            args.countdown,
            armed_click,
            args.click_delay_ms,
            args.show_cursor,
        )
    if args.record_fullscreen:
        armed_click = overlays.select_click_target() if args.arm_click else None
        return recording._record_video_cli(
            None,
            args.fps,
            not args.no_audio,
            args.duration,
            args.countdown,
            armed_click,
            args.click_delay_ms,
            args.show_cursor,
        )

    try:
        _report_snip(args.snip_output, capture.capture_snip())
        return 0
    except Exception:
        logging.exception("Super Snipper failed")
        raise


def run() -> int:
    """Entry point used by the launcher: report failures loudly and visibly."""
    try:
        return main()
    except config.RecordingCancelled:
        logging.info("Capture cancelled")
        return 0
    except Exception as exc:
        logging.exception("Super Snipper failed")
        ctypes.windll.user32.MessageBoxW(None, f"{exc}\n\nLog: {config.LOG_FILE}", "Super Snipper error", 0x10)
        return 1
