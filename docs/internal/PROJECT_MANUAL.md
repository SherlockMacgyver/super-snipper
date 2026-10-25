# Super Snipper -- Project Manual

<!-- The user-facing guide to what this project DELIVERS: what it does, and how
     the person who ends up using it operates it. Three documents are easy to
     confuse, so: this is the deliverable's manual; PROJECT_DOC.md beside it is
     the engineering record of intent, decisions and state; and product_manual/
     in the Structured Chaos repo is SC's OWN manual, not yours. If this project
     has no conventional UI, this is the operating guide for whatever was built.
     Keep stable anchor IDs when renaming headings. Empty bodies mean not written
     yet; they are reported as empty rather than as maintained. Comments are
     authoring hints only. -->

<a id="manual/overview"></a>
## What this is

<!-- Not written yet. -->

<a id="manual/getting-started"></a>
## Getting started

<!-- Not written yet. -->

<a id="manual/using-it"></a>
## Using it

For video storyboards, click the panel's grid button, choose **From a video...**, pick a local video,
then set frames per second or minute and the maximum sheet edge in pixels.
The preview reports frame count, order, tile size, blank cells and actual rate.
Keep the requested rate or explicitly select a lower/higher full-grid option.
Create saves one PNG beside the video, with a numbered suffix if needed.
Cancel or close stops work; Open saved image opens the completed sheet.

The complete usage, command-line examples, limits and failure behavior are in
[Video storyboards](../../README.md#video-storyboards). FFmpeg must be installed
separately; selecting a storyboard rate does not change video recording settings.

For snip storyboards, choose **From snips...** instead. Capture each step with
**Capture snip** (or add an existing picture), draw on the selected step, and use
**Reuse this snip** to mark the same screen again without the earlier marks.
Reorder or remove steps, then **Create storyboard** saves one ordered PNG to
`Pictures\Screenshots`. Originals are never changed; closing with unsaved steps
asks first. Full steps and limits are in
[Snip storyboards](../../README.md#capture-series-storyboards).

<a id="manual/reference"></a>
## Reference

<!-- Not written yet. -->
