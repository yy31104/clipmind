# Screen reference protocol

Two annotators use identical source-only frames in separate sessions. Neither sees extraction output, previous labels, the other annotator's work, or the implementation. Use the normalized video's zero-based clock. Read chronological contact sheets and inspect full-size neighboring frames to place changes. A third source-only pass resolves disagreements after calculating agreement. Each pass retains its original record.

## Boundaries

- Mark the first frame with new visible information. Count new slides, readable incremental additions, and returns to a different information screen. Record each addition separately, including distinct bullets or code lines revealed on the same page.
- In live demos, count a distinct task screen, code edit, command, or result after it settles for at least one second. Place the boundary at the beginning of the stable new information. Typing, scrolling within a continuous document, cursor motion, menus used for navigation, hover tooltips and resize alone do not count. A completed command or a different stable result reached by navigation can count.
- Camera motion, presenter motion, layout changes, occlusion, focus shifts, decorations, changes to highlights on unchanged text, and transitional animation frames do not count. If an animation settles into a new informative diagram, mark the first frame showing that content. A page shown just during navigation for less than one second does not count. Introductions or credits count if they add stable readable content. The first image is not a change.
- Keep timestamp, visual description, event category, and supporting source image IDs. If the first visible source frame is unclear, record the bracket and uncertainty. Compare boundaries with sorted one-to-one matching at ±1 s, and report matched, missed and extra counts, F1 and directional coverage.

## Code

- Independently choose stable, representative code regions across each code talk. Include small text, dark and light panels, terminal results and inline comments when legible. Prefer several points spaced across the material. Transcribe only visible editable code or literal command output selected for the reference. Exclude editor chrome, unrelated console output, people, and decorative or background text.
- Draw a tight rectangular box in normalized frame coordinates around the reference. Preserve reading order, spelling, case and punctuation. Report ambiguous glyphs and unreadable regions explicitly, with reasons. Include each image ID and timestamp. Do not guess unreadable text.
- Compare references only when their timestamps align within ±1 s and boxes overlap by at least 0.5 intersection over union. Each annotator also transcribes the same preselected source-only crops for agreement. Report unmatched region counts separately. For shared crops, report substitutions, deletions and insertions over the first reference and in the reverse direction. Normalize with NFKC, remove whitespace and preserve case, as in the extraction score.

## Holdout selection and scoring

A new set contains complete openly licensed talks with clear publisher attribution. Use at least two producer families absent from prior sets. Include Mandarin, a filmed room and a live demonstration. Seek legible code from more than one source. Fixed source thumbnails and publisher materials determine eligibility before annotation. Do not screen with extraction output or reject a source because its expected score is low. Record the complete candidate list, inclusion/exclusion reasons, original and normalized file hashes, and license.

Freeze selected recordings, independent labels, adjudicated labels, agreement, and the candidate commit before inference. Run each backend once on the same bytes. Score information recall and precision at ±1 s, with canonical scores as a control. Score retained-frame code CER with the frozen regions and text. Missing and failed results keep all corresponding reference counts. Targets for both backends remain 85% recall, 50% precision and at most 15% code CER. Report the original reference scores alongside any later label revision. A low agreement F1 signals unreliable reference timing or scope; it is not a mathematical upper bound on model precision.
