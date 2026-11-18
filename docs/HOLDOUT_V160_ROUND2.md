# v1.6.0 screen holdout, round 2

Round 2 covers four full recordings and 75 minutes of video. The
[recording manifest](data/v160-round2-cases.json) lists the publishers and
licenses. The [source checksums](data/v160-round2-sources.json) identify
the downloaded and prepared files.

| Recording | Publisher | Screen content | Changes | Code regions / characters |
| --- | --- | --- | ---: | ---: |
| [Towards an Open Repository for Research Data](https://m.odw.tw/u/odw/m/rdmw2021-4-5/) | Cheng-Jen Lee, Academia Sinica workshop | Mandarin slides and JSON-LD | 23 | 2 / 269 |
| [Adapting Legacy Code with Boost.Geometry](https://talks.osgeo.org/foss4g-europe-2025/talk/C8EMH7/) | Weston Renoud, FOSS4G Europe 2025 | Venue footage and C++ | 22 | 8 / 1,503 |
| [DuckDB with Geodata](https://av.tib.eu/media/68453) | Jakob Miksch, FOSS4G Europe 2024 | Venue footage, SQL and command output | 27 | 14 / 1,665 |
| [maplibre-gl-terradraw](https://talks.osgeo.org/foss4g-europe-2025/talk/UTUW8Z/) | Jin Igarashi, FOSS4G Europe 2025 | Live map edits, JavaScript and HTML | 67 | 15 / 3,543 |

The research-data workshop and FOSS4G are new producer families. The
[language check](data/v160-round2-source-language.json) places Chinese
first in each of three short audio windows from the workshop. The
publisher records give the source titles and credits.

The source shortlist also included a second Academia Sinica workshop
talk on data-management procedures, a FOSS4G GDAL talk, and two
Akademy talks on modern C++ and Mycroft. The four selected recordings
already covered Mandarin, venue footage, live demonstrations and
several types of code. The Akademy metadata did not identify a Creative
Commons license version.

Two independent GPT-6.2 Sol sessions used the same
[screen rules](SCREEN_LABEL_PROTOCOL_V160.md) and source images. They
matched 132 boundaries across 139 and 136 proposals, an F1 of 96.0%.
They agreed on all 2,020 characters in 12 identical source crops.
A separate source-only pass resolved disagreements. The
[boundary and region comparison](data/v160-round2-label-agreement.json)
and [fixed-crop comparison](data/v160-round2-shared-crop-agreement.json)
give the pre-adjudication counts.

The [frozen labels](data/v160-round2-labels.json) use an exact,
video-relative source presentation time for every code region.
All 44 referenced stills matched the [native frame re-encode](data/v160-round2-native-evidence.json) byte for byte. Both scored
backends use the same four input files and the existing ±1 s boundary
matcher.

## Frozen run

The [campaign lock](data/v160-round2-campaign.json) records the candidate,
source files, labels and scorer. The [runtime fingerprint](data/v160-round2-runtime.json)
identifies the application files used by both backends. Docker uses the
[recorded Linux arm64 image](data/v160-round2-docker-image.json). Each
backend runs once on this corpus.

The candidate completed [two exposed development recordings](data/v160-round2-development-smoke.json)
on both backends with no OCR failures or temporary frames left on disk.
Boundary precision was still below 16% on those recordings. They are
separate from the round 2 sources.
