# Super Snipper

<!-- This tree's current intent, decisions, and state. Keep stable anchor IDs
     when renaming headings. Empty bodies mean not written yet; add verified
     content beneath the relevant heading. Comments are authoring hints only.
     Unresolved questions, risks and open work go under Open Gaps: one living
     document per tree, not a separate gaps file.
     Give each Current State or Reference entry its own anchored ### heading
     (project/<slug>, or project/reference/<slug> under Reference) instead of
     a bullet in the parent section: a changed section is re-delivered whole,
     so one entry per heading keeps a one-entry change a one-entry delivery.
     Root seats read each entry's "**Root summary:**" paragraph (30 words at
     most), or the whole entry when it is that short, and only the heading of
     a Reference entry; the rest is read on demand. -->

<a id="project/overview"></a>
## Overview

<!-- Not written yet. -->

<a id="project/status"></a>
## Current State

<!-- Not written yet. -->

<a id="project/video-storyboards"></a>
### Video storyboards

**Root summary:** Video files become ordered PNG sheets through the panel or CLI, including packaged CLI export. Installed-tray activation and broad codec coverage remain unverified.

The standalone implementation is `supersnipper/storyboard.py`; the Qt window
is `supersnipper/storyboard_ui.py`. Both the CLI and window use the same immutable
plan for rate, count and grid. Layout maximizes tile pixel area, then minimizes
empty cells. Optional lower/higher counts are explicit choices, never automatic
rate changes. FFmpeg is resolved by the existing recording module.

Exports decode one tile at a time in a background worker, preserve frame order
and display aspect ratio, and save beside the source using exclusive filenames.
Cancellation and errors remove owned partial output. The sheet edge is bounded
at 4096 pixels and count at 1000 frames. Packaged releases include FFmpeg; source
installs supply it separately. There is no clipboard copy, automatic recording
hook, or multi-sheet output.

`tests/test_super_snipper_storyboard.py` covers the real CLI, FFmpeg pixel order,
collision protection, cancellation, rotated display dimensions, invalid inputs,
Qt window lifecycle and HUD registration. Offscreen tests are integration
evidence; native runs are isolated checkout evidence, never installed activation.
Qt actions are simulated; the native operating-system file picker is unverified.
Run the focused file with `QT_QPA_PLATFORM=windows` after changing this flow.
Source: video storyboard claim 7de88a2839354fee98480c8ed116d074.

<a id="project/capture-series-storyboards"></a>
### Snip storyboards

**Root summary:** Snip storyboards collect and annotate ordered pictures, reuse unmarked originals and stitch one PNG. Packaged selector launch and cancellation work; successful interactive selection remains unverified.

`supersnipper/series.py` holds the step model (an untouched original path plus
marks in image pixels) and Qt rendering; `supersnipper/series_ui.py` is the
window, opened by `--series` or the panel's storyboard menu (**From snips...**).
Drawing reuses the live markup's tool geometry, colors and widths through
`markup.load_ink_tools`, so both surfaces draw identical shapes. Capture runs the
normal snip flow through the launcher with `--snip-output`, which reports the
saved path; no output means a cancelled snip. Layout imports
`storyboard.choose_grid`, owned by the video storyboard claim; mixed snip shapes
share one cell shape (geometric mean of aspects) and are letterboxed on black.

Steps live only in the open window; there is no saved series file, captions,
clipboard copy or multi-sheet output. Stitching runs on the GUI thread. A
missing or resized original fails the sheet with nothing written.
`tests/test_super_snipper_series.py` drives the real window, real launcher
capture via `--capture-box`, saved pixels, reuse isolation and unchanged
originals; three mutations (marks copied on reuse, reversed order, marks not
rendered) each turned it red. Packaged selector launch and cancellation recovery
were witnessed; successful interactive selection, native file picker and installed
tray remain unverified.
Source: capture-series storyboard claim 52d9795543b34fda897e80bee1159227.

<a id="project/packaging"></a>
### Windows installer and application archive

**Root summary:** Installer and archive bundle dependencies. Host installation, recording, export, repair and removal passed; clean-OS, tray/hotkeys and sign-in remain unverified. Windows may warn or block unsigned builds.

`packaging/version.json` owns the release version. `inputs.json` pins downloaded
source/tool inputs; `requirements-build.txt` pins Python packages. PyInstaller
creates GUI/console executables with replaceable Qt DLLs. `config.launch_command`
owns child relaunch; frozen FFmpeg resolves only `bin/ffmpeg.exe`. Source behavior
and command-line interfaces remain supported.

`build_ffmpeg.py` compiles FFmpeg with x264 and zlib from published source, using
the pinned portable compiler and NASM. `build_release.py` assembles one payload,
source/notices bundle, file hashes, application ZIP and Inno Setup package.
Production builds require a clean tree; development builds identify their state.
The public installer uses per-user versioned folders, optional sign-in/desktop
shortcuts and refuses modification while its application is running. Uninstall
keeps captures and application settings/logs.

SC's current source-archive pin is untouched. The binary archive contract is in
README, for the separate SC integration follow-up. Source: installer claim
e36698eeac42420aa2f4de9fad353d59. Release verification is recorded in that checklist;
source tests and developer-host isolation are not clean-OS acceptance.

Actual host setup, screenshot, H.264/AAC recording, video storyboard, running-app
refusal, repair and uninstall passed with restricted PATH. A synthetic older
version verified directory transition, shortcut target and removal of both
versions while retaining captures/settings. The packaged series window launched
its selector child and recovered from cancellation; successful interactive
selection was not witnessed. Desktop interaction stopped at the owner's request.

<a id="project/gaps"></a>
## Open Gaps

**Root summary:** Clean-OS installer acceptance, sign-in startup, broad codec and variable-rate coverage, and physical multi-monitor behavior need separate witnesses. Builds are unsigned; Windows may warn or block.

This change does not switch the running tray to this repository. Broad codec,
variable-frame-rate and physical multi-monitor behavior remain unverified.
FFmpeg's container duration can exceed the video stream in unusual files;
an unavailable sample fails the export rather than shortening it silently.
Clipboard and multi-sheet output were discussed but are not part of this implementation.
Snip storyboards keep steps only while their window is open, stitch on the GUI
thread. Successful interactive snip selection and the native file picker still
need witnesses; packaged selector launch and cancellation recovery were observed.
The installer build host has no Windows Sandbox or WSL. A dependency-isolated
host run cannot establish fresh-VM behavior. Paid signing is out of scope by
Marc's direction; no claim is made that checksums bypass Windows trust controls.

<a id="project/reference"></a>
## Reference

<!-- Not written yet. -->
