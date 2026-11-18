# Screen holdout round 3

Four complete recordings total 70 minutes and 28 seconds. They add
three producers absent from the earlier scored sets. Two talks show
an auditorium and projected slides. The other two are Mandarin and
German software demonstrations. The original publisher files retain
their native geometry and presentation clocks.

| Recording | Boundaries | Code regions | Code characters |
| --- | ---: | ---: | ---: |
| Flat ODF with Git | 38 | 6 | 975 |
| Inkscape character components | 20 | 0 | 0 |
| C, Emacs and Make | 47 | 9 | 700 |
| LibreOffice as AppImage | 69 | 0 | 0 |
| **Total** | **174** | **15** | **1,675** |

Two independent runs of the same AI model marked 178 and 171 source
changes. They matched 168 within one second, for 96.28% F1. A third
source-only pass reviewed all 13 unmatched observations and reopened
60 matched pairs. The same-model passes can share omissions. Their
transcriptions of 13 fixed crops agreed over all 1,506 normalized
characters. Only two independently selected regions matched in both
time and position. The final labels include dark terminal output,
light hexdump output, and small XML, Bash and C text.

Scoring times are relative to the first decoded video frame. Each
label also keeps the native presentation timestamp and decoded frame
index. No publisher caption tracks accompany these inputs. Speech
accuracy is outside this score.

The paired evaluation uses one pinned candidate and identical source
bytes. Each backend runs once. Each must reach at least 85% information
boundary recall, 50% precision and at most 15% retained-frame code CER.
A failed or missing reference keeps its full denominator.

[Selection ledger](SCREEN_V160_ROUND3_SOURCES.md) ·
[Corpus](data/v160-round3-cases.json) ·
[Scoring labels](data/v160-round3-labels.json) ·
[Independent agreement](data/v160-round3-label-agreement.json) ·
[Native clocks and adjudication checksums](data/v160-round3-native-evidence.json) ·
[Publisher credits and media links](data/v160-round3-sources.json)
