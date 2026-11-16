# Development-set screen errors

AI-assisted annotations; human review pending.

An unchanged v1.3.0 run on the ten development excerpts reproduces 44 matches out of 60 source changes and 159 predicted boundaries in the seven slide-sequence windows. All 16 missed changes and 115 extra boundaries have a source-frame pair and an individual attribution in [the inventory](data/v140-development-attribution.json). The original v1.3 report and source labels remain unchanged.

| Primary cause | Missed changes | Extra boundaries |
| --- | ---: | ---: |
| Progressive build | 14 | 0 |
| Small text / small screen area | 2 | 0 |
| Layout switch | 0 | 40 |
| Speaker motion | 0 | 39 |
| Animation | 0 | 19 |
| Transition | 0 | 10 |
| Duplicate detection of one change | 0 | 7 |
| **Total** | **16** | **115** |

These are descriptions supported by the source pairs, not proof that one parameter causes every error. Some events have several contributing factors. The JSON preserves explanations and secondary causes rather than treating the categories as mutually exclusive mechanisms.

## What the source pairs show

- **Progressive builds:** Xous adds one code line or table row without changing most pixels. Four such additions are missed. The USB and Photon decks lose similar local additions. Keeping a later full build matters for reading, but it must not erase earlier additions from the boundary metric.
- **Small information regions:** the USB pinout changes inside a wide room shot; a Ghidra code page changes within a mostly white, similar layout. Both are missed even though the source contains new information.
- **Composition and motion:** the same slide moves between full-screen, room and picture-in-picture views. Other predictions track the presenter or animated diagrams. These are useful source frames in some contexts, but they are not new informational pages.
- **Duplicate transitions:** several successive sampled frames around one slide change each create a boundary. One-to-one matching credits one and counts the rest as extra predictions.
- **Code OCR:** the Xous retained frame omits later code lines; Fuzzer puts bright foreground code over dim terminal text. The end-to-end CER includes both losing the right frame and recognizing its text incorrectly. They need separate inspection even when the final metric combines them.

There is also a label caveat: the frozen Fuzzer change at 175 seconds describes a small “Left” label, but direct source frames place its first appearance near 177 seconds. The baseline has no corresponding later boundary either. Its score and frozen annotation are retained, with this discrepancy recorded for the owner's review.

## Scope and evidence

The three windows marked not applicable in v1.3 have continuous animation or no slide sequence. They retain that status; their canonical predictions are still stored, but they do not become fabricated false positives against an empty reference sequence. [The protocol](SCREEN_EVALUATION.md) keeps the original canonical control next to the new information-boundary score.

`v1.4.0-development-boundary-review.zip` contains the individual full-size source pairs and contact sheets referenced by the JSON. The source excerpts are from 39C3, published by CCC under CC BY 4.0; [the source manifest](data/real-talk-cases.json) lists titles and publisher links. Review images and source media are release attachments rather than repository files.
