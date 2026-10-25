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

<a id="project/gaps"></a>
## Open Gaps

**Root summary:** Installed-tray activation, broad codec and variable-rate input coverage, and physical multi-monitor UI behavior still need witnesses.

This change does not switch the running tray to this repository. Broad codec,
variable-frame-rate and physical multi-monitor behavior remain unverified.
FFmpeg's container duration can exceed the video stream in unusual files;
an unavailable sample fails the export rather than shortening it silently.
Clipboard and multi-sheet output were discussed but are not part of this implementation.

<a id="project/reference"></a>
## Reference

<!-- Not written yet. -->
