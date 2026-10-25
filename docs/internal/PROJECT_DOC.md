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

**Root summary:** Video files become ordered PNG sheets through the panel or CLI, with explicit sampling and layout choices. Installed-tray activation and broad codec coverage remain unverified.

The standalone implementation is `supersnipper/storyboard.py`; the Qt window
is `supersnipper/storyboard_ui.py`. Both the CLI and window use the same immutable
plan for rate, count and grid. Layout maximizes tile pixel area, then minimizes
empty cells. Optional lower/higher counts are explicit choices, never automatic
rate changes. FFmpeg is resolved by the existing recording module.

Exports decode one tile at a time in a background worker, preserve frame order
and display aspect ratio, and save beside the source using exclusive filenames.
Cancellation and errors remove owned partial output. The sheet edge is bounded
at 4096 pixels and count at 1000 frames. There is no extra bundled binary,
clipboard copy, automatic recording hook, or multi-sheet output.

`tests/test_super_snipper_storyboard.py` covers the real CLI, FFmpeg pixel order,
collision protection, cancellation, rotated display dimensions, invalid inputs,
Qt window lifecycle and HUD registration. Offscreen tests are integration
evidence; native runs are isolated checkout evidence, never installed activation.
Qt actions are simulated; the native operating-system file picker is unverified.
Run the focused file with `QT_QPA_PLATFORM=windows` after changing this flow.
Source: video storyboard claim 7de88a2839354fee98480c8ed116d074.

<a id="project/capture-series-storyboards"></a>
### Snip storyboards

**Root summary:** A separate window collects snips as ordered steps, marks each still, reuses an original without earlier marks, and stitches one PNG with the video storyboard layout rule.

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
rendered) each turned it red. The interactive snip selector, native file picker
and installed tray are untested.
Source: capture-series storyboard claim 52d9795543b34fda897e80bee1159227.

<a id="project/gaps"></a>
## Open Gaps

**Root summary:** Installed-tray activation, broad codec and variable-rate input coverage, physical multi-monitor UI behavior and the interactive snip selector in snip storyboards still need witnesses.

This change does not switch the running tray to this repository. Broad codec,
variable-frame-rate and physical multi-monitor behavior remain unverified.
FFmpeg's container duration can exceed the video stream in unusual files;
an unavailable sample fails the export rather than shortening it silently.
Clipboard and multi-sheet output were discussed but are not part of this implementation.
Snip storyboards keep steps only while their window is open, stitch on the GUI
thread, and have no witness yet for the interactive snip selector or native file picker.

<a id="project/reference"></a>
## Reference

<!-- Not written yet. -->
