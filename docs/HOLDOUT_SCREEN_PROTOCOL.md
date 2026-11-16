# Screen holdout protocol

This set contains three complete published recordings from COSCUP 2020,
EnthusiastiCon, and Wikimania. None is in the ten-video 39C3 development set.
The recordings were selected for an explicit reusable license, a direct public
file, a bounded full-talk duration, and a mix of Chinese material, source code,
and research slides. Selection does not use ClipMind predictions.

All three source files are CC BY-SA 4.0. The corpus manifest links each file's
license and attribution. Video files and derived review images belong in release
assets, with checksums, rather than Git. The code remains under its own license.

## Source preparation

Keep the complete published recording, including its opening, closing, questions,
and camera-only sections. Normalize to a 1280 by 720 letterboxed H.264 input at
CRF 20 with AAC audio at 128 kbit/s. Do not crop time or remove difficult scenes.
Record the original file's publisher SHA-1, byte count, and locally computed
SHA-256, plus the input SHA-256. Container language tags are not treated as proof
of spoken language; use the publisher's description and source audio.

## Source labels

Annotations use source frames only. Inspect every one-second contact-sheet cell
across each full input, then inspect full-size frames around changes and code.
Labels are AI-assisted; human review is pending. They are fallible reference
annotations, not human ground truth.

A primary change is the first source-visible occurrence of persistent new
information in a slide, diagram, code listing, terminal, or document. Every
incremental informational build remains a separate change, as in the original
39C3 benchmark. The first visible page at the beginning is context, not a change.
A change hidden by a speaker-only shot is timed when the new information first
becomes visible, rather than guessed from the unseen slide deck.

Exclude speaker motion, camera cuts, picture-in-picture layout changes, cursor
motion, and animation interpolation without new persistent information. Record
these negative events separately. A return to an unchanged page after a camera-only or layout interval is not
a new information change. Returning after a different content page is a new
content transition, even when that page appeared earlier. Persistent opening
titles and closing credit pages count; only the initial frame is excluded. Describe genuinely continuous demonstrations explicitly;
do not silently award them perfect recall or precision.

Also group incremental builds by page and record the final complete source frame.
These groups support a separate completeness check; they do not replace primary
change labels or reduce the primary recall denominator.

Code references are transcribed from full-size source frames without OCR. Include
punctuation, capitalization, and line order; normalized rectangles identify the
exact visible block. Select references across the time range of each code-bearing
talk, including small text and background interference when present. Do not
select regions after seeing model output. Record absent code as not applicable.

## Scores and frozen use

The targets are change recall of at least 85%, boundary precision of at least
50%, and character error rate of at most 15% for code regions. Primary boundary
matching is one-to-one within one second. Repeated predictions cannot inflate
recall. Report the raw matched, missed, and extra counts alongside percentages.
Report code substitutions, insertions, deletions, and reference character counts.
A missing retained frame counts as missing text rather than an omitted reference.

Keep the original 39C3 development metrics and definition visible. Secondary
page-group completeness and excluded-layout diagnostics must be labeled as
secondary; do not compare them with the original 60-change denominator.

Freeze the manifest, all labels, preparation commands, and source checksums before
changing extraction code. Run the holdout once, after development decisions are
finished. Preserve the result and errors even when targets are missed. Do not
change parameters or replace examples after reading the result. A platform matrix
is one frozen campaign: each declared backend gets one attempt against the same
inputs and labels. Retain failed attempts rather than overwriting them.

Publisher captions are unavailable for these selected source files. Speech
recognition is still part of full extraction, but holdout transcript agreement is
not applicable. The separate development-set caption comparison remains visible.

## Human review

The review archive provides every change's before/after images, every code crop,
timestamps, source links, and editable CSV decisions. Review can change the
published annotation assessment later, with the previous labels and scores kept.
Until the completed review is returned, all reports must retain the label
provenance: AI-assisted annotations; human review pending.
