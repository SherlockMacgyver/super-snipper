import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from tests.super_snipper_support import LAUNCHER as MODULE_PATH, load

ROOT = Path(__file__).resolve().parents[1]


class SuperSnipperTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        # A private panel namespace keeps these tests off a live Snipper's mutex.
        env = patch.dict(os.environ, {"SUPER_SNIPPER_HOME": home.name,
                                      "SUPER_SNIPPER_INSTANCE": uuid.uuid4().hex})
        env.start()
        self.addCleanup(env.stop)
        self.snipper = load()

    def test_recording_box_offsets_region_by_virtual_screen_origin(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(-1920, 0, 3840, 2160)):
            self.assertEqual(
                self.snipper._recording_box((100, 200, 700, 500)),
                (-1820, 200, 600, 300),
            )

    def test_recording_box_rounds_odd_dimensions_inward_for_h264(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(-1920, 0, 3841, 2161)):
            self.assertEqual(self.snipper._recording_box(None), (-1920, 0, 3840, 2160))
            self.assertEqual(
                self.snipper._recording_box((100, 200, 2061, 1989)),
                (-1820, 200, 1960, 1788),
            )

    def test_mux_failure_removes_partial_output_and_preserves_raw_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "capture.video.mkv"
            audio = root / "capture.audio.wav"
            output = root / "capture.mp4"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio" * 20)
            failed = Mock(returncode=1, stdout="mux exploded")
            def fail_mux(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial")
                return failed

            hidden_kwargs = {"creationflags": 12345, "startupinfo": object()}
            with patch.object(
                self.snipper, "hidden_subprocess_kwargs", return_value=hidden_kwargs
            ) as hidden, patch.object(
                self.snipper.subprocess, "run", side_effect=fail_mux
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "recoverable source media preserved"):
                    self.snipper._mux_recording(Path("ffmpeg.exe"), video, audio, output)

            hidden.assert_called_once_with(new_process_group=False)
            self.assertEqual(run.call_args.kwargs["creationflags"], 12345)
            self.assertIs(run.call_args.kwargs["startupinfo"], hidden_kwargs["startupinfo"])

            self.assertTrue(video.exists())
            self.assertTrue(audio.exists())
            self.assertFalse(output.exists())
            self.assertEqual(sorted(path.name for path in root.iterdir()),
                             ["capture.audio.wav", "capture.video.mkv"])

    def test_mux_success_without_output_fails_loud_and_preserves_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "capture.video.mkv"
            output = root / "capture.mp4"
            video.write_bytes(b"video")
            succeeded = Mock(returncode=0, stdout="")

            with patch.object(self.snipper.subprocess, "run", return_value=succeeded):
                with self.assertRaisesRegex(RuntimeError, "without a usable output"):
                    self.snipper._mux_recording(Path("ffmpeg.exe"), video, None, output)

            self.assertTrue(video.exists())
            self.assertFalse(output.exists())

    def test_record_video_cancelled_countdown_stops_before_audio_or_ffmpeg(self):
        audio_cls = Mock()
        with patch.object(self.snipper, "_active_recording_state", return_value=None), \
            patch.object(self.snipper, "resolve_ffmpeg", return_value=Path("C:/ffmpeg/bin/ffmpeg.exe")), \
            patch.object(self.snipper, "timestamped_video_path", return_value=Path("C:/tmp/out.mp4")), \
            patch.object(self.snipper, "_delete_if_exists"), \
            patch.object(self.snipper, "show_recording_countdown", return_value=False) as countdown, \
            patch.object(self.snipper, "LoopbackAudioRecorder", audio_cls), \
            patch.object(self.snipper.subprocess, "Popen") as popen:
            with self.assertRaises(self.snipper.RecordingCancelled):
                self.snipper.record_video(region=(0, 0, 100, 100), countdown=3)

        countdown.assert_called_once_with(3, armed_click=False)
        audio_cls.assert_not_called()
        popen.assert_not_called()

    def test_record_video_hides_ffmpeg_console_without_changing_capture_pipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "capture.mp4"
            video = root / "capture.video.mkv"
            video.write_bytes(b"video")
            process = Mock(returncode=0)
            process.poll.return_value = 0
            def start_fake_capture(command, **kwargs):
                Path(command[-1]).write_bytes(b"video")
                return process
            hidden_kwargs = {"creationflags": 12345, "startupinfo": object()}

            with patch.object(self.snipper, "set_dpi_awareness"), \
                patch.object(self.snipper, "_active_recording_state", return_value=None), \
                patch.object(self.snipper, "resolve_ffmpeg", return_value=Path("ffmpeg.exe")), \
                patch.object(self.snipper, "timestamped_video_path", return_value=output), \
                patch.object(self.snipper, "_recording_box", return_value=(0, 0, 100, 100)), \
                patch.object(self.snipper, "FFMPEG_LOG_FILE", root / "ffmpeg.log"), \
                patch.object(self.snipper, "RECORDING_STATE_FILE", root / "recording.json"), \
                patch.object(self.snipper, "RECORDING_STOP_FILE", root / "recording.stop"), \
                patch.object(self.snipper, "_mux_recording") as mux, \
                patch.object(
                    self.snipper, "hidden_subprocess_kwargs", return_value=hidden_kwargs
                ) as hidden, \
                patch.object(self.snipper.subprocess, "Popen", side_effect=start_fake_capture) as popen:
                result = self.snipper.record_video(
                    region=(0, 0, 100, 100),
                    with_audio=False,
                    countdown=0,
                )

            self.assertEqual(result, output)
            hidden.assert_called_once_with(new_process_group=False)
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs["creationflags"], 12345)
            self.assertIs(kwargs["startupinfo"], hidden_kwargs["startupinfo"])
            self.assertIs(kwargs["stdin"], self.snipper.subprocess.PIPE)
            self.assertIsNotNone(kwargs["stdout"])
            self.assertIs(kwargs["stderr"], kwargs["stdout"])
            mux.assert_called_once_with(Path("ffmpeg.exe"), Path(popen.call_args.args[0][-1]), None, output)

    def test_ffmpeg_video_command_hides_mouse_cursor(self):
        command = self.snipper._ffmpeg_video_command(
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/tmp/out.video.mkv"),
            (10, 20, 640, 360),
            30,
        )

        draw_mouse_index = command.index("-draw_mouse")
        self.assertEqual(command[draw_mouse_index + 1], "0")

    def test_ffmpeg_video_command_shows_mouse_cursor_when_requested(self):
        command = self.snipper._ffmpeg_video_command(
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/tmp/out.video.mkv"),
            (10, 20, 640, 360),
            30,
            show_cursor=True,
        )

        draw_mouse_index = command.index("-draw_mouse")
        self.assertEqual(command[draw_mouse_index + 1], "1")

    def test_record_command_args_passes_show_cursor(self):
        self.assertEqual(
            self.snipper._record_command_args("region", fps=30, countdown=0, show_cursor=True),
            ["--toggle-record-region", "--fps", "30", "--countdown", "0", "--show-cursor"],
        )

    def test_fire_armed_click_waits_then_clicks_if_process_is_alive(self):
        process = Mock()
        process.poll.return_value = None

        with patch.object(self.snipper.time, "sleep") as sleep, \
            patch.object(self.snipper, "_click_screen_point") as click:
            self.snipper._fire_armed_click_after_start(process, (123, 456), 300)

        sleep.assert_called_once_with(0.3)
        click.assert_called_once_with((123, 456))

    def test_fire_armed_click_skips_if_ffmpeg_already_exited(self):
        process = Mock()
        process.poll.return_value = 1

        with patch.object(self.snipper.time, "sleep"), \
            patch.object(self.snipper, "_click_screen_point") as click:
            self.snipper._fire_armed_click_after_start(process, (123, 456), 300)

        click.assert_not_called()

    def test_toggle_recording_passes_countdown_to_record_video(self):
        with patch.object(self.snipper, "_active_recording_state", return_value=None), \
            patch.object(self.snipper, "select_video_region", return_value=(1, 2, 101, 202)), \
            patch.object(self.snipper, "record_video") as record_video:
            result = self.snipper.toggle_recording(
                region_mode=True,
                fps=24,
                with_audio=False,
                countdown=5,
            )

        self.assertEqual(result, 0)
        record_video.assert_called_once_with(
            region=(1, 2, 101, 202),
            fps=24,
            with_audio=False,
            countdown=5,
            armed_click=None,
            click_delay_ms=self.snipper.DEFAULT_ARMED_CLICK_DELAY_MS,
            show_cursor=False,
        )

    def test_toggle_recording_arms_click_before_region_selection(self):
        calls = []

        def select_click():
            calls.append("click")
            return (30, 40)

        def select_region():
            calls.append("region")
            return (1, 2, 101, 202)

        with patch.object(self.snipper, "_active_recording_state", return_value=None), \
            patch.object(self.snipper, "select_click_target", side_effect=select_click), \
            patch.object(self.snipper, "select_video_region", side_effect=select_region), \
            patch.object(self.snipper, "record_video") as record_video:
            result = self.snipper.toggle_recording(
                region_mode=True,
                fps=24,
                with_audio=False,
                countdown=5,
                arm_click=True,
                click_delay_ms=125,
            )

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["click", "region"])
        record_video.assert_called_once_with(
            region=(1, 2, 101, 202),
            fps=24,
            with_audio=False,
            countdown=5,
            armed_click=(30, 40),
            click_delay_ms=125,
            show_cursor=False,
        )

    def test_record_command_args_builds_basic_region_recording(self):
        self.assertEqual(
            self.snipper._record_command_args(
                "region",
                fps=24,
                countdown=5,
                with_audio=True,
                arm_click=False,
                click_delay_ms=125,
            ),
            ["--toggle-record-region", "--fps", "24", "--countdown", "5"],
        )

    def test_record_command_args_builds_armed_fullscreen_without_audio(self):
        self.assertEqual(
            self.snipper._record_command_args(
                "fullscreen",
                fps=60,
                countdown=2,
                with_audio=False,
                arm_click=True,
                click_delay_ms=500,
            ),
            [
                "--toggle-record-fullscreen",
                "--fps",
                "60",
                "--countdown",
                "2",
                "--no-audio",
                "--arm-click",
                "--click-delay-ms",
                "500",
            ],
        )

    def test_record_command_args_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            self.snipper._record_command_args("sideways")

    def test_hud_launch_command_targets_this_script(self):
        command = self.snipper.hud_launch_command(["--snip"])
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("super_snipper.py"))
        self.assertEqual(command[-1], "--snip")

    def test_control_panel_mutex_allows_only_one_owner(self):
        first = self.snipper._acquire_control_panel_mutex()
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(self.snipper._acquire_control_panel_mutex())
        finally:
            self.snipper._close_handle(first)

        next_owner = self.snipper._acquire_control_panel_mutex()
        self.assertIsNotNone(next_owner)
        self.snipper._close_handle(next_owner)

    def test_hud_repeated_recording_mode_cycles_keep_one_stop_pill(self):
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError:
            self.skipTest("PySide6 not installed; HUD lifecycle probe unavailable")

        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        CaptureHud = self.snipper._load_hud_class(QtCore, QtGui, QtWidgets)
        hud = CaptureHud()
        hud.show()
        app.processEvents()
        try:
            for _ in range(3):
                hud._set_recording_mode(True)
                app.processEvents()
                self.assertTrue(hud.rec_label.isVisible())
                self.assertTrue(hud.btn_stop.isVisible())
                self.assertFalse(hud.btn_region.isVisible())
                self.assertFalse(hud.btn_close.isVisible())

                hud._set_recording_mode(False)
                app.processEvents()
                self.assertFalse(hud.rec_label.isVisible())
                self.assertFalse(hud.btn_stop.isVisible())
                self.assertTrue(hud.btn_region.isVisible())
                self.assertTrue(hud.btn_close.isVisible())
        finally:
            hud.close()
            app.processEvents()

    def test_hud_orientation_for_edge_maps_sides_to_vertical(self):
        self.assertEqual(self.snipper.hud_orientation_for_edge("left"), "vertical")
        self.assertEqual(self.snipper.hud_orientation_for_edge("right"), "vertical")
        self.assertEqual(self.snipper.hud_orientation_for_edge("top"), "horizontal")
        self.assertEqual(self.snipper.hud_orientation_for_edge("bottom"), "horizontal")
        self.assertEqual(self.snipper.hud_orientation_for_edge(None), "horizontal")

    def test_hud_drag_controller_free_drag_tracks_cursor_delta(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(-1920, 0, 3840, 2160)):
            drag = self.snipper.HudDragController(200, 100, snapped_edge=None)
            drag.begin(500, 400, 700, 300)
            self.assertEqual(drag.move_to(515, 393), (715, 293))

    def test_hud_drag_sticks_to_edge_when_cursor_stays_near(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(-1920, 0, 3840, 2160)):
            drag = self.snipper.HudDragController(200, 100, snapped_edge="left")
            drag.begin(-1900, 400, -1908, 300)
            # Cursor stays within the snap threshold of the left edge: the HUD
            # stays glued to min_x while its Y follows the cursor.
            x, y = drag.move_to(-1895, 360)
            self.assertEqual((x, y), (-1920, 260))
            self.assertEqual(drag.snapped_edge, "left")

    def test_hud_drag_floats_when_pulled_off_edge(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(-1920, 0, 3840, 2160)):
            drag = self.snipper.HudDragController(200, 100, snapped_edge="left")
            drag.begin(-1900, 400, -1908, 300)
            # Cursor pulled well past the snap threshold: the HUD un-sticks and
            # follows the cursor freely instead of being locked to the edge.
            x, y = drag.move_to(-1400, 360)
            self.assertEqual((x, y), (-1408, 260))
            self.assertIsNone(drag.snapped_edge)

    def test_hud_drag_controller_release_snaps_and_reports_orientation(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(-1920, 0, 3840, 2160)):
            drag = self.snipper.HudDragController(200, 100, snapped_edge=None)
            x, y, edge, orientation = drag.release(-1900, 500)
            self.assertEqual(edge, "left")
            self.assertEqual(orientation, "vertical")
            self.assertEqual(drag.snapped_edge, "left")
            self.assertEqual(x, -1920)

    def test_snap_helpers_honor_explicit_screen_bounds(self):
        # virtual_screen() is physical (huge under DPI scaling); an explicit
        # logical screen must override it so a DPI-aware caller places and snaps
        # within the logical bounds instead of flying off the visible screen.
        with patch.object(self.snipper, "virtual_screen", return_value=(0, 0, 3840, 2160)):
            logical = (0, 0, 1536, 816)
            self.assertEqual(
                self.snipper.panel_dock_geometry("top", 360, 78, screen=logical), "+588+0"
            )
            x, y, edge = self.snipper.magnetic_panel_position(1526, 300, 360, 78, screen=logical)
            self.assertEqual((x, edge), (1176, "right"))
            self.assertEqual(
                self.snipper.slide_panel_on_edge("right", 900, 400, 360, 78, screen=logical),
                (1176, 400),
            )

    def test_hud_drag_controller_uses_explicit_screen(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(0, 0, 3840, 2160)):
            drag = self.snipper.HudDragController(
                360, 78, snapped_edge=None, screen=(0, 0, 1536, 816)
            )
            x, y, edge, orientation = drag.release(1526, 300)
            self.assertEqual((x, edge, orientation), (1176, "right", "vertical"))

    def test_snap_helpers_can_offset_shadow_box_for_flush_card_docking(self):
        logical = (0, 0, 1536, 816)
        self.assertEqual(
            self.snipper.panel_dock_geometry("left", 230, 40, screen=logical, edge_inset=-4),
            "+-4+388",
        )
        self.assertEqual(
            self.snipper.panel_dock_geometry("right", 230, 40, screen=logical, edge_inset=-4),
            "+1310+388",
        )
        self.assertEqual(
            self.snipper.slide_panel_on_edge("left", 100, 300, 230, 40, screen=logical, edge_inset=-4),
            (-4, 300),
        )

    def test_panel_dock_geometry_uses_virtual_screen_edges(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(-1920, 0, 3840, 2160)):
            self.assertEqual(self.snipper.panel_dock_geometry("left", 200, 100), "+-1920+1030")
            self.assertEqual(self.snipper.panel_dock_geometry("top", 200, 100), "+-100+0")
            self.assertEqual(self.snipper.panel_dock_geometry("right", 200, 100), "+1720+1030")
            self.assertEqual(self.snipper.panel_dock_geometry("bottom", 200, 100), "+-100+2060")

    def test_render_hud_preview_writes_png(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed; HUD render preview unavailable")

        import os
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "hud.png"
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--render-hud", str(out)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 500)

            vertical_out = Path(tmp) / "hud_vertical_settings.png"
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--render-hud",
                    str(vertical_out),
                    "--hud-orientation",
                    "vertical",
                    "--hud-settings",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(vertical_out.exists())
            from PIL import Image

            width, height = Image.open(vertical_out).size
            self.assertLessEqual(width, 70)
            self.assertGreaterEqual(height, width * 4)

            probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from PySide6 import QtCore, QtGui, QtWidgets
from tests.super_snipper_support import load
snipper = load()
QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
    QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
CaptureHud = snipper._load_hud_class(QtCore, QtGui, QtWidgets)
hud = CaptureHud()
hud.show()
app.processEvents()
before = (hud.width(), hud.height())
hud._set_orientation('vertical')
app.processEvents()
after = (hud.width(), hud.height())
print('before', before, 'after', after)
if after[0] > 70 or after[1] < after[0] * 4:
    raise SystemExit(f'live flip did not produce a rail: {{before}} -> {{after}}')
"""
            env = os.environ.copy()
            env["QT_QPA_PLATFORM"] = "offscreen"
            result = subprocess.run(
                [sys.executable, "-c", probe],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_magnetic_panel_position_snaps_near_edges(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(0, 0, 1920, 1080)):
            self.assertEqual(
                self.snipper.magnetic_panel_position(15, 300, 200, 100),
                (0, 300, "left"),
            )
            self.assertEqual(
                self.snipper.magnetic_panel_position(900, 1040, 200, 100),
                (900, 980, "bottom"),
            )

    def test_magnetic_panel_position_does_not_snap_in_middle(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(0, 0, 1920, 1080)):
            self.assertEqual(
                self.snipper.magnetic_panel_position(700, 400, 200, 100),
                (700, 400, None),
            )

    def test_slide_panel_on_edge_keeps_panel_attached(self):
        with patch.object(self.snipper, "virtual_screen", return_value=(0, 0, 1920, 1080)):
            self.assertEqual(self.snipper.slide_panel_on_edge("left", 200, 300, 200, 100), (0, 300))
            self.assertEqual(self.snipper.slide_panel_on_edge("top", 900, 300, 200, 100), (900, 0))


if __name__ == "__main__":
    unittest.main()
