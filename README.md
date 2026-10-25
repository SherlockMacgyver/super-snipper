# Super Snipper

**Snip, record, and draw on your Windows desktop.**

Super Snipper combines region screenshots, MP4 screen recording with optional
computer audio, and live on-screen markup in a small dockable capture panel.
Use global hotkeys or its tray menu to capture a bug, explain a workflow, or
draw attention to something while recording. Captures are saved on your PC.

This is the canonical source repository for Super Snipper. It runs independently
with no account required.

## What it does

| Feature | How it works |
|---|---|
| Region screenshots | Drag a rectangle; the snip is saved as JPG and copied to the clipboard. |
| Screen recording | Record a selected region or the full virtual desktop to MP4. |
| Computer audio | Optionally capture playback from the default output device using loopback audio. Microphone recording is not provided. |
| Live markup | Draw with Pen, Rectangle, Ellipse or Arrow; choose a color and line width, erase individual marks, or clear everything. |
| Click-through Pointer mode | Interact with the app under your drawings without removing the marks. |
| Armed recording | Choose a screen point to click automatically shortly after capture starts. |
| Dockable panel | Float the panel or dock it along the left, right, top or bottom edge. |
| Capture controls | Adjust frame rate, countdown, armed-click delay, audio and cursor visibility. |
| Storyboards | Turn a video into an ordered grid of sampled frames, or collect snips, mark up each one and stitch them in order. |
| Tray and hotkeys | Show or hide the panel, start or stop capture, reset its position, and exit. |

## Requirements

- For the Windows x64 installer: Windows 10 (1809 or later) or Windows 11.
  Python, the hotkey runtime, FFmpeg and the Python libraries are included.
  You do not install them separately or change your system Python.
- The following requirements apply when running **from source**:

- **Windows.** Tested on Windows 11 with a 4K display at 225% scaling.
- **Python 3.14**, the version used for verification, with Tk support and the
  packages in [super_snipper_requirements.txt](super_snipper_requirements.txt).
  Other Python versions have not been verified.
- **[AutoHotkey v2](https://www.autohotkey.com/)** for the tray and global hotkeys.
- **[FFmpeg](https://ffmpeg.org/download.html)** for video recording. Put
  `ffmpeg.exe` on PATH or supply its location when installing. Screenshots work
  without FFmpeg.
- Git to clone the repository, or download and extract the repository ZIP.

Python, AutoHotkey and FFmpeg are installed separately; their binaries are not
included here. Python packages are installed with pip.

## Install

For normal use, download the **Windows x64 setup executable** from
[GitHub Releases](https://github.com/SherlockMacgyver/super-snipper/releases),
run it and launch Super Snipper. It installs for the current Windows user
without administrator access. The setup offers a sign-in startup shortcut
(selected by default) and an optional desktop shortcut. No account is needed.

This release is **unsigned**. Windows may show an unknown-publisher or SmartScreen
warning, and some Windows 11 Smart App Control configurations may block it.
Where Windows offers **More info > Run anyway**, proceed only if you trust the
release and have verified the download. A bypass is not available on every PC;
do not disable your security protection just to run the app. Checksums are
published in `SHA256SUMS.txt`; they check integrity, not publisher identity.

The application ZIP contains the same program files as Setup and can be
extracted and run with `SuperSnipper.exe`. It creates no shortcuts or uninstall
registration by itself. `SuperSnipperCLI.exe` provides console output for
automation. Keep the entire folder together, including `_internal` and `bin`.

### Install from source

Open PowerShell in the directory where you want to keep the application:

```powershell
git clone https://github.com/SherlockMacgyver/super-snipper.git
cd super-snipper
python -m pip install -r super_snipper_requirements.txt

# Resolve the exact Python that has the dependencies installed.
$snipperPython = python -c "import sys; print(sys.executable)"

# Check readiness without changing settings, startup registration or the tray.
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_super_snipper.ps1 -Python $snipperPython -PreflightOnly

# Install for the current user.
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_super_snipper.ps1 -Python $snipperPython
```

These commands set the execution policy only for the installer process. Keep
its application folder in place afterward: the startup entry points to it.
If `python` opens the Microsoft Store or selects the wrong interpreter, use
its full installed path for pip and pass that path to `-Python`.

If FFmpeg is not on PATH, add `-FFmpeg` to both installer commands, for example:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_super_snipper.ps1 -Python $snipperPython -FFmpeg "C:\Tools\ffmpeg\bin\ffmpeg.exe" -PreflightOnly
```

Use the actual location on your machine. After a successful check, rerun the
same command without `-PreflightOnly` to install.

| Installer option | Meaning |
|---|---|
| `-Python <path>` | Interpreter containing the required packages; defaults to `python.exe` on PATH. |
| `-FFmpeg <path>` | FFmpeg executable to save for future recordings. Otherwise the current/saved `SUPER_SNIPPER_FFMPEG` setting or PATH is used. |
| `-AutoHotkey <path>` | AutoHotkey v2 executable; defaults to `%ProgramFiles%\AutoHotkey\v2\AutoHotkey.exe`. |
| `-PreflightOnly` | Run prerequisites, diagnostics and the running-process guard, then exit before installation changes. Diagnostics can write a log. |

The installer checks dependencies and refuses to continue while a Super Snipper
panel or recorder is running. Choose **Exit Super Snipper**, let any recording
finish saving, then rerun it. A Python process whose command line cannot be
read also causes a refusal, with its process ID shown.

Installation saves the selected interpreter and any explicitly supplied FFmpeg
path as user environment variables. It registers the `SuperSnipperHotkey`
logon task; if Task Scheduler refuses, it reports that failure and creates a
Startup-folder shortcut instead. Missing FFmpeg produces a prominent
**VIDEO RECORDING IS UNAVAILABLE** warning but still permits a screenshot-only
installation. A passing self-test alone does not mean video is available.

## Capture from the tray or keyboard

Double-click the tray icon or press **Win+Shift+X** to show the panel.

| Hotkey | Action |
|---|---|
| **Win+Shift+S** | Snip a region |
| **Win+Shift+V** | Start or stop a region recording |
| **Win+Shift+C** | Start or stop a region recording with an armed click |
| **Win+Shift+F** | Start or stop a full-screen recording |
| **Win+Shift+X** | Show the capture panel |

These shortcuts are active while the tray is running and overlap some Windows
shortcuts, including the built-in snipping shortcut. The tray menu lists them
and provides the same capture actions.

1. For a screenshot, select **Snip a region** and drag over the area to capture.
2. For a video, choose region or full-screen recording. Select the region when
   prompted and wait for the countdown.
3. Use **Stop / cancel capture**, the panel's Stop control, or the recording
   hotkey again to finish. Wait for finalization before closing the application.

The gear button opens recording settings. **Cursor is off by default**; turn it
on when viewers need to see the pointer. **Audio is on by default** and records
computer playback. If loopback capture cannot start, recording fails visibly;
turn Audio off or use `--no-audio` to record without it. Check the output device,
volume and a short sample before a demo; an empty or silent source does not
guarantee an audible track. Panel recording settings reset when its process
restarts.

The tray's **Hide panel** action hides the controls without stopping a recording.
Outside recording, the panel's X also hides it. During recording, the panel
shows Stop and Markup; its X and settings controls are hidden. **Esc with the
recording panel focused stops capture**, unlike Esc on a drawing overlay,
which returns to Pointer mode.
**Exit Super Snipper** cancels setup or stops an active recording, waits for it
to finish, then closes the panel and tray. Docking and reset-position controls
are also available from the tray menu.

## Draw on the screen

Open the panel's **Markup** menu and choose Pen, Rectangle, Ellipse or Arrow.
Drag to draw. The menu also provides Color, Width, Eraser and Clear all.

- Drawing mode receives your mouse input. Select **Pointer - click through**,
  press **Esc** while drawing, or right-click to interact with the app beneath
  the marks again.
- Draw before starting a screenshot to include the marks in it. The screenshot
  selection screen is a frozen image, not a drawing surface.
- During video capture, marks within the recorded area become part of the video.
- Starting capture returns input to Pointer mode. Snips and cancelled selections
  preserve existing marks; finishing a recording or exiting clears them.
- Marks are temporary. They are baked into the captured image or video rather
  than saved as an editable annotation layer.

<a id="video-storyboards"></a>
## Video storyboards

Click the panel's grid button and choose **From a video...** to turn a
recording or another video file into one ordered PNG collage. This opens a
separate window; it does not change the recording rate or capture area.
(**From snips...** builds a storyboard from snips instead; see
[Snip storyboards](#capture-series-storyboards).)

1. **Choose video** selects a local file that your installed FFmpeg can decode.
2. Set the sample rate in **frames per minute** or **frames per second**.
   For example, 6 frames per minute samples once every 10 seconds, starting
   at 0 seconds. The count is video duration times rate, rounded up.
3. Choose the **maximum sheet edge** (64-4096 pixels; default 2048). The sheet
   fits within that square but uses the grid's own rectangular shape. The
   numbered preview shows order, empty cells, sheet size and each tile's size.
4. Keep **Requested rate**, or explicitly choose the nearest lower or higher
   count whose best grid has no empty cells. The actual rate and interval are
   shown before export. Fewer frames mean wider time gaps; more frames can
   make individual images smaller. Your rate is never adjusted automatically.
5. Click **Create storyboard**. Progress appears as frames decode; **Cancel**
   stops the job. Closing the window cancels and waits for its worker to finish.
   The saved path is selectable; **Open saved image** opens your image viewer.

Frames read left to right, then down, with their display proportions preserved
to pixel rounding. Layout maximizes tile area first and uses fewer empty cells
as the tiebreaker. A prime count such as 13 can therefore leave blanks rather
than force a thin strip. Empty cells are black. Timing is also recorded in the
PNG's Description metadata. The sheet contains the frames, without captions.

The PNG is saved beside the source as `<video name> storyboard.png`, adding a
number if that name already exists. Nothing is copied to the clipboard, and
this version does not split a video into several sheets. At most 1000 samples
are allowed per sheet; lower the rate for longer videos. A smaller count often
helps readability more than a higher resolution. AI services may resize uploads;
the resolution field is a pixel size, not a guarantee about any model's limits.

Packaged builds use their included FFmpeg. Source builds select FFmpeg through
`SUPER_SNIPPER_FFMPEG` or PATH. Files with no
readable duration, missing video, undecodable requested frames, or unwritable
output folders fail visibly instead of silently dropping frames. A seek that
takes over 90 seconds for one frame stops the export. Duration comes from the
container's FFmpeg header; unusual files with mismatched audio/video lengths
can fail at a requested time beyond the video track. Samples select the first
decodable frame at or after their time; high rates can repeat source frames.

```powershell
# Open the window without the tray.
python super_snipper.py --storyboard
# Export directly, with the exact requested sample rate.
python super_snipper.py --storyboard "C:\Videos\demo.mp4" --sample-rate 6 --sample-unit minute --sheet-edge 2048
```

Storyboard tests exercise the real launcher, FFmpeg, saved pixels and Qt window.
Run `python -m pytest tests/test_super_snipper_storyboard.py -q`; set
`QT_QPA_PLATFORM=windows` to exercise an isolated native window instead of the
default offscreen test surface. This does not install or replace a running tray.
Window tests use simulated Qt actions with real decoding; they do not exercise
the native operating-system file picker or prove installed-tray activation.

<a id="capture-series-storyboards"></a>
## Snip storyboards

Click the panel's grid button and choose **From snips...** to build a
step-by-step storyboard from still snips. The window lists the steps in order
on the left and shows the selected step on the right, where you draw on it.

1. **Capture snip** hides the window and opens the normal snip selector. The
   snip is saved and copied to the clipboard as usual, then added as the next
   step. Press Esc in the selector to cancel; no step is added.
   **Add image...** adds an existing picture instead.
2. Draw on the selected step with **Pen**, **Arrow**, **Rectangle**,
   **Ellipse** or **Eraser** (removes whole marks), in the same colors and
   widths as live markup. **Undo** removes the step's last mark; **Clear marks**
   removes all of them. Marks scale up on large snips so they stay visible
   after the sheet is shrunk.
3. **Reuse this snip** adds a new step right after the selected one, on the
   same original picture but with no marks, so you can mark the next part of
   the same screen. Earlier marks never carry forward.
4. **Move up**, **Move down** and **Remove step** fix the order.
5. Choose the **maximum sheet edge** and click **Create storyboard**. The
   summary line shows the grid, empty cells and sheet size first.

Steps read left to right, then down, using the same layout rule as video
storyboards: the largest cells first, fewer empty cells as the tiebreaker.
Snips of different shapes share one cell shape (the average of their
proportions) and are centered on black, with their proportions kept. The PNG
is saved to `Pictures\Screenshots` as `SuperSnip Storyboard <time>.png`, and
**Open saved image** opens it. Nothing is copied to the clipboard and there are
no captions or multiple sheets in this version.

Original snips are never changed; marks exist only in this window and in the
saved sheet. Closing the window with unsaved steps asks first, because the
steps and marks are not kept. If an original is deleted or changes size before
you create the sheet, nothing is saved and the window names the file.

```powershell
# Open the snip storyboard window without the tray.
python super_snipper.py --series
```

Run `python -m pytest tests/test_super_snipper_series.py -q` (add
`QT_QPA_PLATFORM=windows` for a native window). The tests drive the real window,
capture through the real launcher with `--capture-box`, and check saved pixels:
step order, marks only on their own step, and unchanged originals. They do not
exercise the interactive snip selector, the native file picker, or the
installed tray.

## Saved files, logs and failed recordings

Screenshots go to `Pictures\Screenshots`; videos go to `Videos\Captures` under
the Windows user profile. Files use readable timestamps, with a numbered suffix
when needed. Exclusive writes prevent an existing capture from being replaced.
A video is reported saved only after FFmpeg can decode a frame from the final MP4.

| Environment setting | Purpose |
|---|---|
| `SUPER_SNIPPER_HOME` | State and log directory; defaults to `%LOCALAPPDATA%\SuperSnipper`. |
| `SUPER_SNIPPER_FFMPEG` | FFmpeg executable for recording; otherwise FFmpeg is found on PATH. |
| `SUPER_SNIPPER_PYTHONW` | Python interpreter used by the tray; otherwise `pythonw.exe` is found on PATH. |

The state folder contains `super_snipper.log` and `super_snipper_ffmpeg.log`.
If capture fails, read the error and those logs before starting recovery.

The current recorder creates an attempt-specific `.supersnipper-*` folder
beside its intended MP4. While recording, that folder contains `video.mkv`
and, when audio is enabled, `audio.wav`. A failed recording or finalization
preserves nonempty raw media rather than deleting it. Keep those originals and
work on copies if attempting recovery; a crash can leave incomplete media,
and recovery is not guaranteed. Successful finalization normally removes the
temporary files. A cleanup warning can mean raw files remain even though the
MP4 saved successfully.

Do not delete recording state or stop files while a recorder is running.
There is no automatic recovery interface in this version.

## Command line

```powershell
python super_snipper.py --help
python super_snipper.py --self-test
python super_snipper.py --panel-command show
python super_snipper.py --snip
python super_snipper.py --record-fullscreen --duration 5 --show-cursor --no-audio
python super_snipper.py --stop-recording
```

`--record-region` opens a region selector; `--record-box left,top,right,bottom`
uses physical pixels measured from the top-left of the virtual desktop.
`--fps`, `--countdown`, `--arm-click` and
`--click-delay-ms` control recording setup. `--snip-output FILE` makes `--snip`
or `--capture-box` write the saved file's path to FILE (nothing is written when
the snip is cancelled). Run `--help` for the complete list.
Direct capture commands still save files to the normal capture folders.

## Updating and uninstalling

For an installed package, choose **Exit Super Snipper**, then run the newer
Setup. Setup refuses to replace or uninstall its running application. Versioned
program folders let a newer installation update its shortcuts without treating
your capture folders as program files. Existing recorded versions remain owned
by the installer's uninstall log and are removed on uninstall.

Uninstall through Windows **Installed apps > Super Snipper > Uninstall**.
Your screenshots, recordings and `%LOCALAPPDATA%\SuperSnipper` settings/logs
are kept. No system Python, AutoHotkey installation or system environment
variables are removed. The portable ZIP is updated by extracting a new version
to a separate folder after exiting; it has no automatic updater.

For source installations:

Before updating, choose **Exit Super Snipper** and wait for any recording to
finish. Update this checkout, reinstall requirements if they changed, then rerun
the installer from the permanent application folder.

To uninstall:

1. Choose **Exit Super Snipper** from the tray.
2. Remove the `SuperSnipperHotkey` scheduled task if present, and remove
   `Super Snipper Hotkey.lnk` from the folder opened by `shell:startup` if present.
3. Optionally remove the user environment variables `SUPER_SNIPPER_PYTHONW` and
   `SUPER_SNIPPER_FFMPEG`, the application folder, and its state/log folder.
4. Keep or delete your screenshots and recordings separately; they live outside
   the application folder.

## Verification and known limits

Verified with Python 3.14 on Windows 11, including a 4K desktop at 225% scaling.
Tests cover capture setup, recorder identity, output protection, cleanup,
panel lifecycle, markup, and installer preflight. Native desktop checks have
also exercised drawing, click-through, keyboard focus and encoded ink continuity.

- Windows 10, physical multi-monitor setups and other Python versions have not
  been verified.
- High-resolution recording and many ink strokes can be CPU-intensive. Lower
  the frame rate or record a smaller region if capture cannot keep up.
- Computer-audio capture depends on the output device. Verify a short sample
  before relying on it for a long recording.
- Apps that continuously force themselves above other windows may cover marks.
- Installer and application ZIP builds are provided; there is no standalone automatic updater.

Run tests from the repository root with the requirements, pytest and FFmpeg
available. The Windows installer checks also require AutoHotkey v2:

```powershell
python -m pip install pytest
python -m pytest tests

# Include the isolated panel-service installer check.
$env:SUPER_SNIPPER_INSTALLER_TESTS = "1"
python -m pytest tests/test_super_snipper_installer.py
```

Installer tests use preflight and independently contained copies; they do not
perform a real install. Encoding tests use real FFmpeg with synthetic frames.
Passing them does not replace a real desktop capture check after changing the
capture or markup code. When reporting a bug, include Windows/Python versions,
display scaling, reproduction steps and relevant logs, removing private details.

<a id="packaged-build"></a>
## Building and verifying the Windows release

Build on Windows x64 with **CPython 3.14.3** and Inno Setup 6. The build uses an
isolated virtual environment; it does not install tools into the system Python.
The compiler, assembler, AutoHotkey and upstream source URLs/checksums are pinned
in `packaging/inputs.json`. Python build/runtime versions are pinned in
`packaging/requirements-build.txt`. The build needs internet access for those
inputs. A first FFmpeg build can take several minutes.

```powershell
C:\Python314\python.exe -m venv .build/venv
.build/venv/Scripts/python.exe -m pip install -r packaging/requirements-build.txt
.build/venv/Scripts/python.exe packaging/fetch_inputs.py
.build/venv/Scripts/python.exe packaging/build_ffmpeg.py
# Set this if Inno Setup is not under LocalAppData\Programs\Inno Setup 6.
$env:SUPER_SNIPPER_ISCC = "C:\path\to\Inno Setup 6\ISCC.exe"
.build/venv/Scripts/python.exe packaging/build_release.py
```

Production packaging requires a clean Git checkout and records the source
commit plus a digest of the included application source. `--development`
permits a work-in-progress build and marks its metadata accordingly; do not
publish those test artifacts. The recipe is repeatable with pinned inputs,
but compiler/archive timestamps mean byte-identical builds are not promised.

`dist/` contains the setup executable, application ZIP, corresponding-sources
ZIP and `SHA256SUMS.txt`. Publish all four together under the matching version
tag on GitHub Releases. FFmpeg is built from the supplied FFmpeg/x264/zlib
sources, including NASM optimization, rather than copied from another app.
The source archive also supplies exact AutoHotkey, Qt and PySide/shiboken source
versions and the build recipe. It is not an offline compiler/toolchain SDK.

`THIRD_PARTY_NOTICES.txt` in the payload explains the distinct component licenses.
Qt/PySide libraries stay as replaceable DLLs under `_internal`; source and rebuild
instructions remain available. Do not omit the sources ZIP or relicense bundled
components as MIT. No certificate purchase or paid service is part of this build.

Run the normal test suite before building. Then set `SUPER_SNIPPER_PACKAGE` to
`.build/payload/SuperSnipper` and run `tests/test_packaging.py` to check the real
binary with system Python/FFmpeg absent from PATH and validate its file manifest.
`python packaging/verify_release.py` runs the real silent installer, capture and
recording paths, running-app refusal, same-version repair and uninstall in a new
temporary directory. It refuses existing packaged-install registrations or
shortcuts rather than overwriting them. It uses this Windows account's guarded,
temporary application registration, not a separate Windows account. It preserves
its evidence and captures. `--no-desktop` checks installation and diagnostics
without opening capture windows. For a version transition, supply
`--previous-setup C:\path\to\older-setup.exe --previous-version 0.9.9`; it checks
the newer shortcut target, preserved data and removal of both versions on uninstall.
Run this test on a disposable Windows user or VM for a genuine first-install
witness; dependency-isolated runs on a developer host are a narrower check.

The initial packaged release was exercised on the Windows 11 development host:
real setup, screenshot, H.264/AAC recording, video storyboard, running-app refusal,
same-version repair and uninstall. A synthetic earlier-version installer also
verified the version-directory transition, shortcut update and two-version removal.
Python and FFmpeg were absent from the test process's PATH. The packaged snip
editor launched its selector child and recovered from cancellation; successful
interactive selection, installed tray/hotkeys and sign-in startup still need
separate witnesses. Source tests cover drawing and both storyboard workflows;
they are not a clean Windows installation test.

### Application archive contract for SC

The versioned ZIP has one `SuperSnipper/` root containing `SuperSnipper.exe`
(GUI, defaults to tray), `SuperSnipperCLI.exe` (console), `bin/ffmpeg.exe`,
`bin/AutoHotkey64.exe`, the hotkey script, `_internal/`, notices and source.
Both executables accept the existing capture/storyboard/panel arguments.
`release-info.json` identifies version, source and payload schema (1);
`file-manifest.json` records hashes for payload files other than itself.
The manifest is an integration/build check, not a runtime restriction on
replacing LGPL libraries. `--diagnostics FILE` writes runtime details to JSON.

SC can unpack this archive without running the public installer. Its own update
flow owns the chosen version, registration and startup. This repository does
not modify SC's current source-archive pin; changing SC to consume this binary
layout is a separate integration change. Source installs and their existing
PowerShell setup remain available.

## License

Super Snipper's source is released under the [MIT License](LICENSE).
Copyright (c) 2026 Marc Stinebaugh. Bundled and separately installed dependencies
retain their own licenses; see the packaged third-party notices and source archive.
